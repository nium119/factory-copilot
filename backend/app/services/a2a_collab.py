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
import re
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

# 指代延续判定词：消息含这些词说明是指代上文，而非新对象
_COREF_CONTINUE_WORDS = (
    "其它", "其他", "别的", "还有", "另外", "第二个", "下一个",
    "这个", "那个", "这些", "那些", "它", "该", "其余", "剩下的",
)

# 新查询意图词：出现即视为新查询，不做外部协作延续
_NEW_QUERY_WORDS = (
    "查询", "列出", "显示", "查看", "获取", "搜索", "创建", "删除",
    "修改", "更新", "统计", "分析", "报告", "全部", "所有", "列表",
)


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


def _last_collab_turn(history_messages: Optional[List]) -> Optional[Dict[str, Any]]:
    """从历史投影中找最近一条外部协作 turn，返回 {collab, content} 或 None。"""
    from app.services.history_projection import TURN_META_KEY
    for hm in reversed(history_messages or []):
        meta = getattr(hm, "additional_kwargs", {}).get(TURN_META_KEY)
        if not isinstance(meta, dict):
            continue
        collab = meta.get("collab_agents") or []
        if collab:
            return {
                "collab": collab,
                "content": str(getattr(hm, "content", ""))[:600],
            }
    return None


def _build_continuation(collab: List) -> List[Dict[str, Any]]:
    """把 collab_agents 投影为 delegate_external 所需的 matched 结构。"""
    result = []
    for c in collab:
        if isinstance(c, dict) and c.get("name"):
            result.append({
                "name": c["name"],
                "display_name": c.get("display_name") or c["name"],
                "type": "external_a2a",
            })
    return result


def match_external_continuation(message: str, history_messages: Optional[List]) -> List[Dict[str, Any]]:
    """确定性指代延续：上轮由外部 A2A Agent 处理，且当前含指代词 → 延续该外部 Agent。

    「其它/别的/第二个/这个」这类指代词语义明确（指代上文），可程序判定。
    命中后原样把消息转给该外部 Agent，上下文由 A2A context_id 维持。
    """
    if not history_messages:
        return []
    _msg = (message or "").strip()
    if not _msg or len(_msg) > 30:
        return []
    # 明确新查询 → 不延续
    if any(w in _msg for w in _NEW_QUERY_WORDS):
        return []
    if not any(w in _msg for w in _COREF_CONTINUE_WORDS):
        return []
    last = _last_collab_turn(history_messages)
    if not last:
        return []
    result = _build_continuation(last["collab"])
    if result:
        log.info(f"[外部协作] 指代延续命中 {[r['name'] for r in result]}（消息: {message[:30]}）")
    return result


async def match_external_continuation_llm(message: str, history_messages: Optional[List]) -> List[Dict[str, Any]]:
    """LLM 判断延续：上轮外部协作 + 当前是短编码/短值回复（非指代词）→ 判断是否延续。

    「L02」「P002」这类短值回复是「延续上轮请求」还是「切换到新对象」，属于语义判断，
    程序正则判不准，交给 LLM 结合上轮外部 Agent 的回复内容判断（仅在上轮外部协作后触发）。
    """
    if not history_messages:
        return []
    _msg = (message or "").strip()
    if not _msg or len(_msg) > 30:
        return []
    if any(w in _msg for w in _NEW_QUERY_WORDS):
        return []
    # 指代词已由确定性延续处理；这里只处理非指代词的短值回复
    if any(w in _msg for w in _COREF_CONTINUE_WORDS):
        return []
    if not re.search(r'[A-Za-z0-9]', _msg):
        return []
    last = _last_collab_turn(history_messages)
    if not last:
        return []
    prompt = (
        "上轮由外部 Agent 处理了用户请求，现在用户回了一句简短的话。判断这句话是否在延续上轮的请求。\n\n"
        f"上轮外部 Agent 的回复（节选）：\n{last['content'][:400]}\n\n"
        f"用户当前消息：{message}\n\n"
        "规则：\n"
        "- 上轮回复若在引导用户提供某个值（如指定编码/编号/名称），且当前消息正是这个值的回答 → true\n"
        "- 当前消息是切换到新话题/新对象（如上轮说能耗，用户说工单/库存/设备等新对象）→ false\n"
        "只输出 true 或 false。"
    )
    try:
        from app.agents.settings.model import MODEL_CONFIG
        from app.services.llm_service import llm_service
        result = await asyncio.wait_for(
            llm_service.chat_sync(
                message=prompt,
                system_prompt="你是延续判断器，只输出 true 或 false。",
                model_name=MODEL_CONFIG.get("decision_model"),
            ),
            timeout=5.0,
        )
        if 'true' in (result or "").strip().lower():
            out = _build_continuation(last["collab"])
            if out:
                log.info(f"[外部协作] LLM 延续命中 {[r['name'] for r in out]}（消息: {message[:30]}）")
            return out
    except Exception as e:
        log.warning(f"[外部协作] LLM 延续判断失败，走正常路由: {e}")
    return []


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
