"""A2A 外部 Agent 自动协作 — 确定性技能匹配 + 命中短路委派（阶段二）

2026 工业界透明委派模式落地：
- 匹配独立于 LLM 路由（后者提示词只认识内置 Agent，永远无法正确判断外部 Agent 能力）
- 匹配依据 Agent Card skills 的 name/description/tags/examples 做关键词 + ngram 匹配
- 命中即短路委派，未命中不影响原有链路（保守阈值，宁可不触发不误触发）

本模块为无状态函数集合，由 message_service.process_message_stream 在真实消息入口调用
（编译模式下消息走 get_agent(域Agent) → BaseAgent.process，GeneralAgent 不在链路中）。
"""
import asyncio
import json as _json
import time
from typing import Any, AsyncGenerator, Dict, List, Optional

from app.core.logger import log
from app.core.prompts import DEFAULT_SYSTEM_PROMPT

# 技能名常见的动词前缀（剥离后得到核心名词，如「查询能耗」→「能耗」）
_SKILL_LEADING_VERBS = (
    "查询", "获取", "生成", "计算", "分析", "统计", "查看", "检索", "导出", "创建", "记录", "搜索",
)

# 单个外部 Agent 委托超时（秒），覆盖 A2AClient 默认 30s 内的异常兜底
_EXTERNAL_TASK_TIMEOUT = 30.0

# 流式订阅（sendSubscribe）整体超时（秒），长任务留足余量，httpx read 超时兜底
_EXTERNAL_STREAM_TIMEOUT = 120.0


def _skill_core_terms(skill) -> set:
    """从 Agent Card 技能提取核心匹配词（确定性、可解释）。

    策略（保守，宁可不触发不误触发）：
    - name 剥离动词前缀 → 核心名词（技能精确标识，2 字符可保留）
    - tags 直接作为核心词
    - description/examples 只保留 ≥3 字符 ngram（避开 2 字符歧义）
    """
    from app.services.intent_router import _tokenize_keywords
    terms: set = set()
    name = (skill.name or "").strip()
    for v in _SKILL_LEADING_VERBS:
        if name.startswith(v):
            name = name[len(v):]
            break
    if len(name) >= 2:
        terms.add(name)
        terms.update(k for k in _tokenize_keywords(name) if len(k) >= 2)
    terms.update(t for t in (skill.tags or []) if len(t) >= 2)
    for text in [skill.description] + list(skill.examples or []):
        terms.update(k for k in _tokenize_keywords(text) if len(k) >= 3)
    return {t for t in terms if t and len(t) >= 2}


def match_external_skills(message: str) -> List[Dict[str, Any]]:
    """确定性技能匹配：消息命中外部 A2A Agent 的 skills 描述则加入委派列表。

    只遍历 a2a_registry.auto_collab_agents()（启用自动协作且已连接的外部 Agent）。
    """
    from app.a2a import a2a_registry
    matched: List[Dict[str, Any]] = []
    for name in a2a_registry.auto_collab_agents():
        client = a2a_registry.get_client(name)
        if not client or not client.agent_card:
            continue
        hit_skill = None
        for skill in client.agent_card.skills:
            if any(term in message for term in _skill_core_terms(skill)):
                hit_skill = skill
                break
        if hit_skill:
            matched.append({
                "name": name,
                "display_name": client.display_name,
                "type": "external_a2a",
                "skill": hit_skill.name,
            })
            log.info(f"[外部协作] {name} 命中技能 '{hit_skill.name}'")
    return matched


async def _run_external_agent(agent_name: str, message: str, session_id: str = "") -> Dict[str, Any]:
    """委托单个外部 A2A Agent，返回结构化结果。"""
    from app.a2a import a2a_registry
    t_start = time.time()
    ext_client = a2a_registry.get_client(agent_name)
    if ext_client is None:
        return {"agent_name": agent_name, "display_name": agent_name,
                "status": "error", "data": None, "elapsed": 0.0, "error": "外部 Agent 未连接"}
    display_name = ext_client.display_name
    try:
        task = await ext_client.send_task(message, context_id=session_id, timeout=_EXTERNAL_TASK_TIMEOUT)
        elapsed = time.time() - t_start
        data = task.result_text or ""
        status = "success" if data else "empty"
        log.info(f"[协作] {agent_name} A2A 委托 → {status} ({elapsed:.2f}s)")
        return {"agent_name": agent_name, "display_name": display_name,
                "status": status, "data": data, "elapsed": round(elapsed, 3)}
    except Exception as e:
        elapsed = time.time() - t_start
        log.warning(f"[协作] {agent_name} A2A 委托异常: {e}")
        return {"agent_name": agent_name, "display_name": display_name,
                "status": "error", "data": None, "elapsed": round(elapsed, 3), "error": str(e)}


async def _run_external_agent_stream(agent_name: str, message: str, session_id: str = "") -> AsyncGenerator[tuple, None]:
    """委托单个外部 A2A Agent，优先走 sendSubscribe 流式订阅，边收边产出事件。

    产出 (event_kind, payload_dict)：
    - ("progress", {...})  中间状态（status-update，如 working/submitted）
    - ("result", {...})    最终结果（artifact-update / send_task fallback）

    外部 Agent 未实现 sendSubscribe 端点时回退到同步 tasks/send（_run_external_agent）。
    """
    from app.a2a import a2a_registry
    ext_client = a2a_registry.get_client(agent_name)
    display_name = ext_client.display_name if ext_client else agent_name
    if ext_client is None:
        yield ("result", {"agent_name": agent_name, "display_name": display_name,
                          "status": "error", "data": None, "elapsed": 0.0, "error": "外部 Agent 未连接"})
        return

    # 仅当 Agent Card 声明了 sendSubscribe 端点才走流式（否则回退同步 send）
    has_subscribe = bool(
        ext_client.agent_card and ext_client.agent_card.endpoints
        and ext_client.agent_card.endpoints.get("tasks/sendSubscribe")
    )
    if not has_subscribe:
        yield ("result", await _run_external_agent(agent_name, message, session_id))
        return

    t_start = time.time()
    final_text = ""
    try:
        async for evt_type, data in ext_client.send_task_subscribe(
            message, context_id=session_id, timeout=_EXTERNAL_STREAM_TIMEOUT
        ):
            if evt_type == "status-update":
                # 中间状态 → 进度事件（统一 status=running，前端 ExecutionOrbit 渲染「执行中」）
                yield ("progress", {
                    "agent_name": agent_name, "display_name": display_name,
                    "status": "running",
                })
            elif evt_type in ("artifact-update", "message"):
                # 0.3 标准事件：artifact.parts[0].text
                text = ""
                artifact = (data or {}).get("artifact") if isinstance(data, dict) else None
                if isinstance(artifact, dict):
                    parts = artifact.get("parts") or []
                    if parts and isinstance(parts[0], dict):
                        text = parts[0].get("text", "")
                if text:
                    final_text = text
        elapsed = time.time() - t_start
        status = "success" if final_text else "empty"
        log.info(f"[协作] {agent_name} A2A 流式委托 → {status} ({elapsed:.2f}s)")
        yield ("result", {"agent_name": agent_name, "display_name": display_name,
                          "status": status, "data": final_text, "elapsed": round(elapsed, 3)})
    except Exception as e:
        elapsed = time.time() - t_start
        log.warning(f"[协作] {agent_name} A2A 流式委托异常: {e}")
        yield ("result", {"agent_name": agent_name, "display_name": display_name,
                          "status": "error", "data": None, "elapsed": round(elapsed, 3), "error": str(e)})


async def delegate_external(
    external_agents: List[Dict[str, Any]],
    message: str,
    session_id: str,
    model_name: Optional[str],
    system_prompt: str = "",
    history_messages: Optional[List] = None,
    enable_thinking: Optional[bool] = None,
) -> AsyncGenerator[tuple, None]:
    """外部 A2A Agent 短路协作 — 命中外部技能时直接委托，不再广播内部 Agent。

    产出 parallel_start/parallel_task/parallel_done + metadata(collab_agents) + LLM 综合报告，
    前端 ExecutionOrbit / CollabStepsPanel 零改动渲染。
    """
    from app.core.resource_monitor import resource_monitor
    from app.services.llm_service import llm_service

    t0 = time.time()
    total = len(external_agents)
    batch_id = f"extcollab_{int(t0 * 1000)}"
    system_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT
    log.info(f"外部 A2A Agent 协作（短路）: {[e['name'] for e in external_agents]}")

    tasks_summary = [
        {"task_id": f"task_{e['name']}", "agent_name": e["name"], "display_name": e["display_name"]}
        for e in external_agents
    ]
    yield ("parallel_start", _json.dumps(
        {"batch_id": batch_id, "total": total, "tasks": tasks_summary}, ensure_ascii=False))

    # 队列并发：每个外部 Agent 一个 worker，流式消费事件进队列，主循环边收边 yield。
    # 长任务期间实时回传 parallel_progress（前端无需黑盒等待到 parallel_task 一次性冒结果）
    queue: asyncio.Queue = asyncio.Queue()

    async def worker(e: Dict[str, Any]) -> None:
        try:
            async for kind, payload in _run_external_agent_stream(e["name"], message, session_id):
                await queue.put((e, kind, payload))
        finally:
            await queue.put((e, "__done__", None))

    workers = [asyncio.create_task(worker(e)) for e in external_agents]

    all_results: Dict[str, Any] = {}
    success_count = 0
    collab_agents_info: List[Dict[str, Any]] = []

    remaining = len(workers)
    while remaining > 0:
        e, kind, payload = await queue.get()
        if kind == "__done__":
            remaining -= 1
            continue
        if kind == "progress":
            yield ("parallel_progress", _json.dumps(payload, ensure_ascii=False))
            continue

        # kind == "result"
        agent_name = payload["agent_name"]
        display_name = payload["display_name"]
        all_results[agent_name] = payload["data"]
        if payload["status"] == "success":
            success_count += 1
        elif payload["status"] == "timeout":
            all_results[agent_name] = f"[{display_name} 查询超时]"
        elif payload["status"] == "error":
            all_results[agent_name] = f"[{display_name} 查询失败: {payload.get('error', '')}]"

        collab_agents_info.append({
            "name": agent_name,
            "display_name": display_name,
            "status": payload["status"],
            "data": payload["data"],
            "elapsed": payload.get("elapsed", 0),
            "priority": "external",
        })

        yield ("parallel_task", _json.dumps({
            "batch_id": batch_id,
            "agent_name": agent_name,
            "display_name": display_name,
            "status": payload["status"],
            "data": payload["data"][:800] if payload["data"] else None,
            "elapsed": payload.get("elapsed", 0),
            "completed": len(collab_agents_info), "total": total,
        }, ensure_ascii=False))

    await asyncio.gather(*workers, return_exceptions=True)

    yield ("parallel_done", _json.dumps(
        {"batch_id": batch_id, "success": success_count, "total": total}, ensure_ascii=False))

    # LLM 综合报告（外部 Agent 结果喂给 LLM 生成自然语言回答）
    data_context = _build_collab_data_context(all_results, external_agents, success_count, total)
    collab_prompt = (
        f"{system_prompt}\n\n## 协作数据\n{data_context}\n\n"
        f"请基于以上各模块的数据，以自然、简洁的方式生成一份综合分析报告，回答用户的问题：「{message}」。"
    )
    effective_model = model_name
    if resource_monitor.enabled:
        tier = resource_monitor.current_tier
        if tier.value in ("constrained", "critical"):
            effective_model = resource_monitor.get_recommended_model(model_name)
            log.info(f"[协作] 资源 {tier.value}, 降级模型: {model_name} → {effective_model}")

    log.info(f"[协作] 开始调用 LLM 生成综合报告 (t+{time.time() - t0:.2f}s)")
    async for chunk_type, chunk_content in llm_service.chat_stream(
        message=collab_prompt, session_id=session_id,
        system_prompt=system_prompt, model_name=effective_model,
        use_agent=False, web_search=False,
        history_messages=history_messages, enable_thinking=enable_thinking,
    ):
        yield chunk_type, chunk_content

    yield "metadata", _json.dumps({"collab_agents": collab_agents_info}, ensure_ascii=False)
    log.info(f"外部 A2A 协作完成 (总耗时: {time.time() - t0:.2f}s)")


def _build_collab_data_context(all_results: dict, external_agents: List[Dict[str, Any]],
                               success_count: int, total_count: int) -> str:
    """将协作结果组装为 LLM 可读的数据上下文。"""
    display_map = {e["name"]: e["display_name"] for e in external_agents}
    lines = [f"查询状态: {success_count}/{total_count} 个模块返回数据\n"]
    for agent_name, result in all_results.items():
        display_name = display_map.get(agent_name, agent_name)
        if result:
            lines.append(f"### {display_name}({agent_name})")
            lines.append(result)
        else:
            lines.append(f"### {display_name}({agent_name}): 无匹配数据")
        lines.append("")
    return "\n".join(lines)
