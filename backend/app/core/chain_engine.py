"""本体驱动的链式引擎 — 动态多概念查询规划器。

三阶段执行：
  阶段 1: 查询 Neo4j 获取真实数据（从本体关系中发现的关联概念）
  阶段 2: 链式 LLM 推理步骤（每步看到前序输出 + 阶段 1 数据）
  阶段 3: 最终 LLM 综合分析

链定义（触发条件、推理步骤、最终提示词）存储在 config/chains.yaml。
概念发现和数据查询始终由本体驱动。
"""

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional

from loguru import logger

from app.agents.agent_config import AGENT_DEFINITIONS


def _agent_display(internal_name: str) -> str:
    info = AGENT_DEFINITIONS.get(internal_name, {})
    return info.get("display_name", internal_name)


# ── 数据结构 ──────────────────────────────────────────────────


@dataclass
class ReasoningStep:
    """单个 LLM 推理步骤 — 接收数据上下文 + 前序步骤输出。"""
    step_id: str
    description: str
    agent_name: str
    prompt_template: str
    output_key: str
    focus_concepts: str = ""  # 该步骤查询的概念
    action_name: str = ""
    action_params: str = "{}"
    precondition: str = ""
    on_failure: str = "abort"


@dataclass
class ChainPlan:
    """动态构建的多概念查询 + 推理 + 综合分析计划。"""
    chain_id: str
    name: str
    description: str
    concepts: list = field(default_factory=list)
    relations: list = field(default_factory=list)
    reasoning_steps: list = field(default_factory=list)  # [ReasoningStep, ...]
    final_prompt_template: str = ""
    mode: str = "analysis"  # "analysis" = LLM only, "action" = agent.process() for tool calls


# ── 数据库链注册表 ───────────────────────────────────────────


async def _load_chains_async() -> Dict[str, dict]:
    """从 agent.db 加载链定义（ORM 版本）。"""
    from app.db import get_db
    from app.repositories.chain_repo import ChainRepository
    chains = {}
    try:
        async for session in get_db():
            repo = ChainRepository(session)
            for chain in await repo.get_enabled():
                logger.info(f"[ChainEngine] loaded: {chain.chain_id}")
                chains[chain.chain_id] = {
                    "chain_id": chain.chain_id,
                    "name": chain.name or "",
                    "description": chain.description or "",
                    "triggers": json.loads(chain.triggers or "[]"),
                    "final_prompt_template": chain.final_prompt_template or "",
                    "mode": chain.mode or "merged",
                    "focus_concepts": chain.focus_concepts or "",
                    "reasoning_steps": [
                        {
                            "step_order": s.step_order,
                            "step_id": s.step_id or "",
                            "description": s.description or "",
                            "agent_name": s.agent_name or "",
                            "prompt_template": s.prompt_template or "",
                            "output_key": s.output_key or "",
                            "focus_concepts": s.focus_concepts or "",
                            "action_name": s.action_name or "",
                            "action_params": s.action_params or "{}",
                            "precondition": s.precondition or "",
                            "on_failure": s.on_failure or "abort",
                        }
                        for s in (chain.steps or [])
                    ],
                }
    except Exception:
        return {}
    return chains


def _load_chains_from_db() -> Dict[str, dict]:
    """从 agent.db 加载链定义（同步包装）。"""
    from app.db import run_async
    try:
        return run_async(_load_chains_async())
    except Exception:
        return {}


def reload_chains():
    """从数据库重新加载链定义。仅 sync 上下文调用。async 上下文用 reload_chains_async()。"""
    global _CHAINS
    _CHAINS = _load_chains_from_db()


async def reload_chains_async():
    """从数据库重新加载链定义（async 上下文调用）。"""
    global _CHAINS
    _CHAINS = await _load_chains_async()


_CHAINS: Dict[str, dict] = {}


async def _emit_chain_done(session_id: str, plan, ok: int, total: int,
                           verified=None, verify_summary=""):
    """统一 emit plan.executed 事件（pipeline / merged / dynamic 三个出口共用）。

    verify 验证未通过（verified=False）时 status 标记 needs_review，
    仅提示人工复核，不自动回滚。
    """
    try:
        from app.services.event_bus import event_bus
        status = "ok" if ok >= total else "partial"
        if verified is False:
            status = "needs_review"
        await event_bus.publish("plan.executed", {
            "conversation_id": session_id,
            "chain_id": plan.chain_id,
            "chain_name": plan.name,
            "mode": plan.mode if hasattr(plan, 'mode') else "",
            "steps_completed": ok,
            "total_steps": total,
            "status": status,
            "error_summary": "",
            "verified": verified,
            "verify_summary": verify_summary,
        })
    except Exception:
        pass


# ── verify 验证辅助 ──────────────────────────────────────────

_DEFAULT_VERIFY_PROMPT = (
    "以下是变更方案执行后的复查数据。判断变更目标是否达成。\n"
    "变更方案：{plan_label}\n"
    "验证目标：{goal}\n"
    "复查数据：\n{verify_data}\n\n"
    '严格只输出 JSON，格式：{"verified": true 或 false, "reason": "简要说明", "actual": "从复查数据中提取的实际值（无法提取则为 null）"}。'
)


def _parse_verify_json(raw: str) -> dict:
    """解析 verify LLM 输出的 JSON，容错返回 {"verified", "reason", "actual"}。"""
    text = (raw or "").strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:]).strip()
    m = re.search(r'(\{.*?"verified"\s*:\s*(?:true|false).*?\})', text, re.DOTALL)
    if not m:
        m = re.search(r'(\{.*\})', text, re.DOTALL)
    if m:
        try:
            obj = json.loads(m.group(1))
            return {
                "verified": obj.get("verified"),
                "reason": str(obj.get("reason", "") or "").strip(),
                "actual": obj.get("actual"),
            }
        except Exception:
            pass
    return {"verified": None, "reason": "", "actual": None}


def _compare_hard(expected: str, actual) -> Optional[bool]:
    """硬对比期望值 vs 实际值。返回 True/False/None（无法提取实际值则 None）。

    支持字符串精确匹配 + 数值宽松比较（忽略浮点误差）。
    """
    if actual is None or str(actual).strip() in ("", "-", "(未提取)"):
        return None
    exp = str(expected).strip()
    act = str(actual).strip()
    if exp == act:
        return True
    try:
        if abs(float(exp) - float(act)) < 1e-9:
            return True
    except ValueError:
        pass
    return False


def _build_verify_filters(verify_target: dict) -> dict:
    """verify_target 的 filters（查询参数，定位目标记录）→ dict。"""
    filters = verify_target.get("filters") or {}
    return {k: v for k, v in filters.items()
            if v is not None and str(v).strip() != ""}


class OntologyChainEngine:
    """本体驱动的链式引擎 — 三阶段执行。"""

    def __init__(self):
        self._agent_resolver: Optional[Callable] = None
        self.last_plan: Optional[ChainPlan] = None
        self._executing: bool = False  # 防递归标志
        self._synthetic_chain_seq = 0   # 合成链计数器

    # ── 公共接口 ──────────────────────────────────────────────

    async def _detect_similarity(self, message: str) -> Optional[str]:
        """检测相似匹配意图，自动生成合成链（无需预配置）。

        用户消息包含相似关键词 + 存在已启用向量化的概念 → 自动路由到 findSimilar。
        """
        # 1. 检查消息是否包含相似意图
        _sim_patterns = [
            r'匹配.*相似', r'相似.*匹配', r'找.*相似', r'相似.*推荐',
            r'相似.*(?:bom|物料|工艺|路线)', r'similar',
        ]
        msg_lower = message.lower()
        if not any(re.search(p, msg_lower) for p in _sim_patterns):
            return None

        # 2. 查找已启用向量化的概念
        try:
            from app.services.ontology_service import ontology_service
            concepts = ontology_service.get_concepts()
        except Exception:
            return None

        enabled = []
        for c in concepts:
            vec_cfg = c.get("vectorization")
            if isinstance(vec_cfg, str):
                try:
                    vec_cfg = json.loads(vec_cfg)
                except Exception:
                    continue
            if vec_cfg and vec_cfg.get("enabled"):
                enabled.append(c)

        if not enabled:
            return None

        # 3. 匹配用户提及的概念
        matched = None
        for c in enabled:
            cn = c.get("name", "")
            cl = c.get("label", "")
            if cl and cl in message:
                matched = c
                break
            if cn and cn.lower() in msg_lower:
                matched = c
                break
        if not matched:
            matched = enabled[0]  # 默认第一个启用的概念

        concept_name = matched.get("name", "")
        concept_label = matched.get("label", concept_name)

        # 4. 创建合成链（自动过期，不持久化）
        self._synthetic_chain_seq += 1
        syn_id = f"_syn_similarity_{self._synthetic_chain_seq}"
        _CHAINS[syn_id] = {
            "chain_id": syn_id,
            "name": f"相似{concept_label}匹配(自动)",
            "description": f"自动生成的相似匹配链",
            "triggers": [],
            "final_prompt_template": "{{similarity_result.result}}",
            "mode": "pipeline",
            "focus_concepts": "",
            "reasoning_steps": [{
                "step_order": 1,
                "step_id": "find_similar",
                "description": f"匹配相似{concept_label}",
                "agent_name": "",
                "prompt_template": "",
                "output_key": "similarity_result",
                "focus_concepts": "",
                "action_name": f"{concept_name}_findSimilar",
                "action_params": json.dumps({"message": "{{message}}", "topK": 5}, ensure_ascii=False),
                "precondition": "",
                "on_failure": "abort",
            }],
        }
        return syn_id

    async def detect(self, message: str) -> Optional[str]:
        """检测消息是否触发多概念分析链（含自动相似匹配路由）。

        返回 chain_id 或 None。执行中跳过防止递归。
        优先级: 预配置链 → 自动相似路由 → None
        """
        if self._executing:
            return None
        message_lower = message.lower()
        # 1. 预配置链
        for chain_id, cfg in _CHAINS.items():
            for pattern in cfg.get("triggers", []):
                if re.search(pattern, message_lower):
                    logger.info(f"[ChainEngine] detect: {chain_id} <- {pattern}")
                    return chain_id
        logger.info(f"[ChainEngine] detect: no match for '{message_lower}' ({len(_CHAINS)} chains)")
        return None

    def set_agent_resolver(self, resolver: Callable):
        self._agent_resolver = resolver

    async def execute(
        self,
        message: str,
        chain_id: str = "",
        model_name: Optional[str] = None,
        enable_thinking: Optional[bool] = None,
        session_id: str = "",
        history_messages: list = None,
        params: dict = None,
    ) -> AsyncGenerator[tuple, None]:
        """执行三阶段本体驱动链式分析。

        产出 (type, content) 元组供 SSE 流式输出。
        chain_id 由调用方传入，避免重复 detect 引发递归。
        """
        if not chain_id:
            chain_id = await self.detect(message)
        if not chain_id:
            # 无预定义链匹配 → 尝试动态编排
            runtime = self._get_compiled_runtime()
            if runtime and runtime.skills:
                logger.info("[ChainEngine] 无链匹配, 启用动态编排")
                async for chunk in self._execute_dynamic(
                    message, model_name, enable_thinking, session_id
                ):
                    yield chunk
                return
            yield ('error', '未检测到匹配的分析链')
            return

        self._executing = True
        try:
            plan = await self._build_plan(chain_id, message)
        except Exception:
            self._executing = False
            raise
        self.last_plan = plan

        # 提取方案级 verify_target（LLM 生成变更方案时的验证声明），供执行后验证
        verify_target = None
        if params:
            _plan_data = params.get("plan") if isinstance(params.get("plan"), dict) else None
            if _plan_data:
                verify_target = _plan_data.get("verify_target")
            else:
                verify_target = params.get("verify_target")

        # ── 发送 chain_start ──
        steps_summary = []
        if plan.reasoning_steps:
            # 链式模式：每步自己管理数据查询，只发送推理步骤
            steps_summary += [
                {"step_id": rs.step_id, "description": rs.description, "phase": "reasoning",
                 "agent_name": rs.agent_name, "focus_concepts": rs.focus_concepts}
                for rs in plan.reasoning_steps
            ]
            # 如果有最终汇总提示词，添加汇总步骤
            if plan.final_prompt_template:
                steps_summary.append(
                    {"step_id": "final_summary", "description": "综合汇总", "phase": "summary"}
                )
        else:
            # 合并模式：先发送数据查询步骤，再发送综合研判步骤
            steps_summary += [
                {"step_id": f"query_{cn}", "description": f"查询{cl}", "phase": "data", "concept": cn}
                for cn, cl, _ in plan.concepts
            ]
            steps_summary.append(
                {"step_id": "comprehensive_analysis", "description": "综合研判", "phase": "reasoning"}
            )
        # verify 步骤：作为执行链最后一步（有 verify_target 或配置了 verify 链时）
        _has_verify = bool(verify_target) or bool(_CHAINS.get(f"{plan.chain_id}_verify"))
        if _has_verify:
            _vlabel = ""
            if isinstance(verify_target, dict):
                _vlabel = verify_target.get("label", "") or ""
            steps_summary.append({
                "step_id": "verify",
                "description": f"验证：{_vlabel}" if _vlabel else "验证执行结果",
                "phase": "verify",
            })
        yield ('chain_start', json.dumps({
            "chain_id": plan.chain_id,
            "chain_name": plan.name,
            "mode": plan.mode if plan.mode else ("chained" if plan.reasoning_steps else "merged"),
            "steps": steps_summary,
            "relations": [
                {"source": s, "label": l, "target": t}
                for s, l, t in plan.relations
            ],
        }, ensure_ascii=False))
        logger.info(
            f"[ChainEngine] chain_start: {plan.chain_id}, "
            f"{len(plan.concepts)} 个数据查询 + {len(plan.reasoning_steps)} 个推理步骤"
        )

        # ═══════════════════════════════════════════════════════
        # Pipeline 模式：确定性的分步执行，不依赖 LLM
        # ═══════════════════════════════════════════════════════
        if plan.mode == "pipeline":
            from app.services.action_executor import action_executor
            pipeline_context: Dict[str, Any] = {"message": message, "plan": (params or {}).get("plan", {}), **(params or {})}
            pipeline_ok, pipeline_total = 0, len(plan.reasoning_steps)

            _step_idx = 0
            while _step_idx < len(plan.reasoning_steps):
                rs = plan.reasoning_steps[_step_idx]
                _retries = 0
                yield ('chain_step', json.dumps({
                    "step_id": rs.step_id, "status": "running",
                    "description": rs.description, "phase": "data",
                    "agent_name": rs.agent_name,
                }, ensure_ascii=False))

                try:
                    # 前置条件检查
                    if rs.precondition:
                        ok = _eval_precondition(rs.precondition, pipeline_context)
                        if not ok:
                            raise Exception(f"前置条件不满足: {rs.prompt_template}")

                    # 模板变量替换
                    params = _render_template(rs.action_params, pipeline_context) if rs.action_params else {}
                    param_warnings = []

                    # 执行 action（优先 action_name，否则查数据）
                    if rs.action_name:
                        from app.services.action_executor import action_executor as _ae
                        _ae._ensure_loaded()
                        # 校验参数名是否匹配本体定义
                        sig = _ae._sigs.get(rs.action_name)
                        # 自动映射：plan 参数 → action 参数
                        plan_params = pipeline_context.get("plan", {})
                        if sig and plan_params:
                            action_param_names = {p["name"] for p in sig.get("params", [])}
                            param_label_map = {p["name"]: p.get("label", "") for p in sig.get("params", [])}
                            for ap_name in action_param_names:
                                if ap_name not in params or not params[ap_name]:
                                    if ap_name in plan_params and plan_params[ap_name]:
                                        params[ap_name] = plan_params[ap_name]
                                    elif (label := param_label_map.get(ap_name, "")) and label in plan_params:
                                        params[ap_name] = plan_params[label]
                        if sig and params:
                            valid_params = {p["name"] for p in sig.get("params", [])}
                            param_labels = {p["name"]: p.get("label", p["name"]) for p in sig.get("params", [])}
                            unknown = set(params.keys()) - valid_params
                            if unknown:
                                param_warnings.append(f"无效参数: {', '.join(unknown)}，已过滤")
                                logger.warning(f"[ChainEngine] {rs.step_id}: 参数 {unknown} 不在本体 {rs.action_name} 定义中，有效参数: {valid_params}")
                                params = {k: v for k, v in params.items() if k in valid_params}
                            missing_required = {p["name"] for p in sig.get("params", []) if p.get("required")} - set(params.keys())
                            if missing_required:
                                missing_labels = [f"{n}({param_labels.get(n, n)})" for n in missing_required]
                                param_warnings.append(f"缺少必填参数: {', '.join(missing_labels)}")
                                logger.warning(f"[ChainEngine] {rs.step_id}: 缺少必填参数 {missing_required}")
                        exec_result = await _ae.execute_structured_async(
                            rs.action_name, params, user_id="",
                        )
                        pipeline_context[rs.output_key or rs.step_id] = exec_result
                        output_preview = str(exec_result.get("result", ""))[:500]
                    elif rs.focus_concepts:
                        # 纯数据查询步骤
                        from app.services.action_executor import action_executor as _ae2
                        _ae2._ensure_loaded()
                        result_text = ""
                        for cn in rs.focus_concepts.split(","):
                            cn = cn.strip()
                            tool_name = f"{cn}_query"
                            sig = _ae2._sigs.get(tool_name, {"conceptName": cn})
                            result_text += await _ae2._execute_query(sig, params)
                        pipeline_context[rs.output_key or rs.step_id] = result_text
                        output_preview = result_text[:500]
                    else:
                        output_preview = ""

                    pipeline_ok += 1
                    yield ('chain_step', json.dumps({
                        "step_id": rs.step_id, "status": "done",
                        "description": rs.description, "phase": "data",
                        "output_preview": output_preview,
                        **({"warnings": param_warnings} if param_warnings else {}),
                    }, ensure_ascii=False))

                except Exception as e:
                    logger.error(f"[ChainEngine] Pipeline 步骤失败 {rs.step_id}: {e}")
                    yield ('chain_step', json.dumps({
                        "step_id": rs.step_id, "status": "error",
                        "description": rs.description, "phase": "data",
                        "error": str(e),
                    }, ensure_ascii=False))
                    if rs.on_failure == "abort":
                        _step_idx = len(plan.reasoning_steps)
                    elif rs.on_failure == "retry" and _retries < 3:
                        _retryable = any(kw in str(e).lower() for kw in ('timeout', 'connect', 'refused', 'reset', 'network', 'unreachable'))
                        if not _retryable:
                            logger.info(f"[ChainEngine] {rs.step_id} 非网络错误，不重试: {e}")
                        else:
                            _retries += 1
                            delay = 2 ** (_retries - 1)  # 1s → 2s → 4s
                            logger.info(f"[ChainEngine] {rs.step_id} 第{_retries}次重试，等待{delay}s")
                            await asyncio.sleep(delay)
                            continue
                    # skip: +1 走下一步
                _step_idx += 1

            # 失败时尝试回滚链
            has_failure = pipeline_ok < pipeline_total
            if has_failure:
                rollback_id = f"{plan.chain_id}_rollback"
                rollback_cfg = _CHAINS.get(rollback_id)
                if rollback_cfg:
                    logger.info(f"[ChainEngine] 触发回滚链: {rollback_id}")
                    yield ('chain_step', json.dumps({
                        "step_id": "rollback", "status": "running",
                        "description": "执行回滚", "phase": "data",
                    }, ensure_ascii=False))
                    try:
                        rollback_ok = 0
                        for rs_data in rollback_cfg.get("reasoning_steps", []):
                            rs = ReasoningStep(
                                step_id=rs_data.get("step_id", ""),
                                description=rs_data.get("description", ""),
                                agent_name=rs_data.get("agent_name", ""),
                                prompt_template=rs_data.get("prompt_template", ""),
                                output_key=rs_data.get("output_key", ""),
                                focus_concepts=rs_data.get("focus_concepts", ""),
                                action_name=rs_data.get("action_name", ""),
                                action_params=rs_data.get("action_params", "{}"),
                                precondition=rs_data.get("precondition", ""),
                                on_failure=rs_data.get("on_failure", "skip"),
                            )
                            try:
                                params = _render_template(rs.action_params, pipeline_context) if rs.action_params else {}
                                if rs.action_name:
                                    exec_result = await action_executor.execute_structured_async(
                                        rs.action_name, params, user_id="",
                                    )
                                rollback_ok += 1
                            except Exception:
                                pass
                        yield ('chain_step', json.dumps({
                            "step_id": "rollback", "status": "done",
                            "description": f"回滚完成 ({rollback_ok}步)", "phase": "data",
                        }, ensure_ascii=False))
                    except Exception as e:
                        yield ('chain_step', json.dumps({
                            "step_id": "rollback", "status": "error",
                            "description": "回滚失败", "phase": "data", "error": str(e),
                        }, ensure_ascii=False))

            # ── 输出最终结果到聊天区 ──
            if plan.final_prompt_template:
                try:
                    final_text = _render_template_str(plan.final_prompt_template, pipeline_context)
                    if final_text and final_text.strip():
                        # 标记汇总完成
                        yield ('chain_step', json.dumps({
                            "step_id": "final_summary", "status": "done",
                            "description": "综合汇总", "phase": "summary",
                            "output_preview": final_text[:300],
                        }, ensure_ascii=False))
                        yield ('content', final_text)
                except Exception:
                    pass
            else:
                # 无 final_prompt_template 时，自动收集各 step 的 result 输出
                _results = []
                for _key, _val in pipeline_context.items():
                    if isinstance(_val, dict) and "result" in _val and "tool" in _val:
                        _text = str(_val.get("result", "")).strip()
                        if _text:
                            _results.append(_text)
                if _results:
                    final_text = "\n\n".join(_results)
                    yield ('chain_step', json.dumps({
                        "step_id": "final_summary", "status": "done",
                        "description": "综合汇总", "phase": "summary",
                        "output_preview": final_text[:300],
                    }, ensure_ascii=False))
                    yield ('content', final_text)

            # ── verify 阶段：执行后验证业务目标是否达成 ──
            verified, verify_summary, verify_detail = None, "", []
            async for _vt, _vc in self._run_verify_phase(
                plan, pipeline_context, verify_target, plan_label=plan.name or "",
            ):
                if _vt == 'verify_result':
                    _vr = json.loads(_vc)
                    verified, verify_summary, verify_detail = (
                        _vr.get("verified"), _vr.get("summary", ""), _vr.get("detail", []),
                    )
                else:
                    yield (_vt, _vc)

            self._executing = False
            yield ('chain_done', json.dumps({
                "chain_id": plan.chain_id,
                "steps_completed": pipeline_ok,
                "total_steps": pipeline_total,
                "data_queries": pipeline_ok,
                "reasoning_steps": 0,
                "summary_ok": 0,
                "verified": verified,
                "verify_summary": verify_summary,
                "verify_detail": verify_detail,
            }, ensure_ascii=False))

            # ── emit 事件: plan.executed ──
            await _emit_chain_done(session_id, plan, pipeline_ok, pipeline_total,
                                   verified=verified, verify_summary=verify_summary)

            return

        # ═══════════════════════════════════════════════════════
        # 阶段 1: 查询 Neo4j 获取真实数据（仅合并模式；链式模式每步独立查询）
        # ═══════════════════════════════════════════════════════
        from app.services.action_executor import action_executor

        data_sections: Dict[str, str] = {}
        if not plan.reasoning_steps:
            # 合并模式：并发查询 all concepts（无依赖，可并行）
            # Step 1: 发所有 running 事件
            for cn, cl, tool_name in plan.concepts:
                yield ('chain_step', json.dumps({
                    "step_id": f"query_{cn}",
                    "status": "running",
                    "description": f"查询{cl}",
                    "phase": "data",
                    "concept": cn,
                }, ensure_ascii=False))

            # Step 2: 并发执行
            async def _query_one(cn, cl, tool_name):
                try:
                    sig = action_executor._sigs.get(tool_name)
                    if sig:
                        params = await self._extract_params_for_concept(message, cn)
                        result = await action_executor._execute_query(sig, params)
                        return cn, cl, result, None
                    else:
                        return cn, cl, f"[无查询工具] {cn}", f"概念 {cn} 没有查询 Action"
                except Exception as e:
                    logger.error(f"[ChainEngine] 阶段 1 查询失败 {cn}: {e}")
                    return cn, cl, f"[查询失败] {e}", str(e)

            tasks = [_query_one(cn, cl, tn) for cn, cl, tn in plan.concepts]
            results = await asyncio.gather(*tasks)

            # Step 3: 发 done/error 事件
            for cn, cl, result_data, error in results:
                data_sections[cn] = result_data
                yield ('chain_step', json.dumps({
                    "step_id": f"query_{cn}",
                    "status": "error" if error else "done",
                    "description": f"查询{cl}",
                    "phase": "data",
                    "concept": cn,
                    **({"output_preview": result_data[:200] + ("..." if len(result_data) > 200 else "")} if not error else {}),
                    **({"error": error} if error else {}),
                }, ensure_ascii=False))

        # 为推理步骤构建数据上下文字符串
        data_text_parts = []
        for cn, cl, _ in plan.concepts:
            data = data_sections.get(cn, "[无数据]")
            data_text_parts.append(f"## {cl} ({cn})\n\n{data}")
        data_context = "\n\n".join(data_text_parts) if data_text_parts else ""

        # ═══════════════════════════════════════════════════════
        # 阶段 2: 推理
        # - 如果 reasoning_steps 为空且 final_prompt_template 存在 → 合并为一次 LLM 调用
        # - 否则 → 逐步执行 reasoning_steps（action chain 需要 agent.process()）
        # ═══════════════════════════════════════════════════════
        if plan.mode == "action":
            if not self._agent_resolver:
                yield ('error', 'Agent 解析器未设置')
                self._executing = False
                return
            # ── Action 模式：agent.process() 执行工具链 ──
            for rs in plan.reasoning_steps:
                yield ('chain_step', json.dumps({
                    "step_id": rs.step_id, "status": "running",
                    "description": rs.description, "phase": "reasoning",
                    "agent_name": rs.agent_name,
                }, ensure_ascii=False))
                agent = self._agent_resolver(rs.agent_name)
                if agent:
                    async for chunk_type, chunk_content in agent.process(
                        message=rs.prompt_template.replace("{message}", message).replace("{data_context}", data_context),
                        session_id=session_id, model_name=model_name,
                        use_agent=False, web_search=False, enable_thinking=enable_thinking,
                        context=None, history_messages=history_messages or [], matched_agents=[],
                    ):
                        if chunk_type == 'content':
                            yield ('content', chunk_content)
                yield ('chain_step', json.dumps({
                    "step_id": rs.step_id, "status": "done",
                    "description": rs.description, "phase": "reasoning",
                }, ensure_ascii=False))
            reasoning_ok = len(plan.reasoning_steps)
            total_steps = len(plan.reasoning_steps)
            summary_ok = 0

        elif not plan.reasoning_steps and plan.final_prompt_template:
            # ── 合并模式：一次 LLM 综合研判 ──
            yield ('chain_step', json.dumps({
                "step_id": "comprehensive_analysis",
                "status": "running",
                "description": "综合研判",
                "phase": "reasoning",
            }, ensure_ascii=False))
            yield ('content', "\n\n---\n### 综合研判\n\n")
            logger.info("[ChainEngine] 阶段 2 综合研判（合并模式）")

            try:
                from app.services.llm_service import llm_service
                from datetime import datetime as _dt
                _today = _dt.now().strftime("%Y-%m-%d")
                analysis_prompt = (plan.final_prompt_template
                    .replace("{message}", message)
                    .replace("{data_context}", data_context))
                # 用户提到时间时才注入日期引导
                import re as _re2
                _ct = _re2.search(r'今天|今日|昨天|明天|当前|现在', message)
                _rt = _re2.search(r'最近|本周|本月|近.*月|近.*天|近.*年|今年以来', message)
                if _ct:
                    analysis_prompt = (
                        f"【当前日期: {_today}】报告日期写 {_today}。"
                        f"无 {_today} 数据就回复：今日（{_today}）无生产数据\n\n"
                        + analysis_prompt
                    )
                elif _rt:
                    analysis_prompt = (
                        f"【当前日期: {_today}】用户问的是时间段。"
                        f"用合适的日期范围分析数据趋势。\n\n"
                        + analysis_prompt
                    )
                # 无数据时注入诚实指令，防止 LLM 编造分析内容
                if data_sections and all(
                    v.startswith("未找到") if isinstance(v, str) else True
                    for v in data_sections.values()
                ):
                    analysis_prompt = (
                        "⚠️ 未查询到任何匹配的实时数据。直接一句话告知用户无数据，"
                        "提示用户提供具体查询条件。禁止输出分析框架或评估模板。回复不超过3句话。\n\n" + analysis_prompt
                    )
                async with asyncio.timeout(120):
                    async for chunk_type, chunk_content in llm_service.chat_stream(
                        message=analysis_prompt,
                        session_id=session_id,
                        system_prompt="你是数据分析专家。直接输出 Markdown 格式报告（表格+图表+行动项），不要用 ```markdown 或 ```md 代码块包裹输出。图表用 ```echarts 代码块生成柱状图/饼图。",
                        model_name=model_name,
                        enable_thinking=enable_thinking,
                        tools=None,
                    ):
                        if chunk_type == 'content':
                            yield ('content', chunk_content)

                yield ('chain_step', json.dumps({
                    "step_id": "comprehensive_analysis",
                    "status": "done", "description": "综合研判", "phase": "reasoning",
                }, ensure_ascii=False))
            except asyncio.TimeoutError:
                yield ('chain_step', json.dumps({
                    "step_id": "comprehensive_analysis", "status": "error",
                    "phase": "reasoning", "error": "推理超时",
                }, ensure_ascii=False))
            except Exception as e:
                logger.error(f"[ChainEngine] 综合研判失败: {e}")
                yield ('chain_step', json.dumps({
                    "step_id": "comprehensive_analysis", "status": "error",
                    "phase": "reasoning", "error": str(e),
                }, ensure_ascii=False))
            reasoning_ok = 1
            total_steps = len(plan.concepts) + 1
            summary_ok = 0
        else:
            # ── 链式模式：每步独立查询数据集 + 逐步推理 ──
            context: Dict[str, str] = {"message": message}
            chain_steps_taken: list = []  # 记录已查概念供跨概念注入
            for rs in plan.reasoning_steps:
                yield ('chain_step', json.dumps({
                    "step_id": rs.step_id, "status": "running",
                    "description": rs.description, "phase": "reasoning",
                    "agent_name": rs.agent_name,
                    "agent_display_name": _agent_display(rs.agent_name),
                }, ensure_ascii=False))
                logger.info(f"[ChainEngine] 链式推理: {rs.step_id} → {rs.agent_name}")

                # 查询该步骤专属数据（含跨概念注入）
                step_data_parts = []
                data_found = False
                if rs.focus_concepts:
                    from app.services.ontology_service import ontology_service
                    _cmap = {c["name"]: c for c in (ontology_service.get_concepts() or [])}
                    step_concepts = [c.strip() for c in rs.focus_concepts.split(",") if c.strip()]
                    for cn in step_concepts:
                        tool_name = f"{cn}_query"
                        sig = action_executor._sigs.get(tool_name)
                        if sig:
                            try:
                                params = await self._extract_params_for_concept(
                                    message, cn, steps_taken=chain_steps_taken, context=context,
                                )
                                result = await action_executor._execute_query(sig, params)
                                chain_steps_taken.append({"concept": cn})
                                label = _cmap.get(cn, {}).get("label", cn)
                                step_data_parts.append(f"## {label} ({cn})\n\n{result}")
                                if not result.startswith("未找到"):
                                    data_found = True
                            except Exception as e:
                                logger.warning(f"[ChainEngine] 查询 {cn} 失败: {e}")
                step_data = "\n\n".join(step_data_parts) or data_context  # 回退到全局数据
                context["data_context"] = step_data

                prompt = rs.prompt_template
                for key, value in context.items():
                    prompt = prompt.replace(f"{{{key}}}", value)

                # 无数据时注入诚实指令，防止 LLM 编造分析内容
                if not data_found and rs.focus_concepts:
                    prompt = (
                        "⚠️ 未查询到任何匹配的实时数据。直接一句话告知用户无数据，"
                        "提示用户提供具体查询条件（如工单号、设备编号）。"
                        "禁止输出任何分析框架、评估模板或示例格式。回复不超过3句话。\n\n" + prompt
                    )

                # 步骤标题分段
                yield ('content', f"\n\n---\n### {rs.description}\n\n")
                try:
                    from app.services.llm_service import llm_service
                    step_response = ""
                    async with asyncio.timeout(120):
                        async for chunk_type, chunk_content in llm_service.chat_stream(
                            message=prompt, session_id=session_id,
                            system_prompt="你是制造业专家。用最少的字输出结论。不要写框架、模板或示例。如果没数据就直说。",
                            model_name=model_name, enable_thinking=enable_thinking, tools=None,
                        ):
                            if chunk_type in ('content', 'thinking'):
                                if chunk_type == 'content':
                                    step_response += chunk_content
                                yield (chunk_type, chunk_content)
                    context[rs.output_key] = step_response
                    yield ('chain_step', json.dumps({
                        "step_id": rs.step_id, "status": "done",
                        "description": rs.description, "phase": "reasoning",
                    }, ensure_ascii=False))
                except asyncio.TimeoutError:
                    context[rs.output_key] = "[超时]"
                    yield ('chain_step', json.dumps({
                        "step_id": rs.step_id, "status": "error",
                        "phase": "reasoning", "error": "推理超时",
                    }, ensure_ascii=False))
                except Exception as e:
                    logger.error(f"[ChainEngine] 推理失败 {rs.step_id}: {e}")
                    context[rs.output_key] = f"[错误] {str(e)}"
                    yield ('chain_step', json.dumps({
                        "step_id": rs.step_id, "status": "error",
                        "phase": "reasoning", "error": str(e),
                    }, ensure_ascii=False))

            # 最终汇总（链式模式每步推理后汇总，合并模式在 comprehensive_analysis 已完成）
            summary_ok = 0
            if plan.final_prompt_template:
                yield ('chain_step', json.dumps({
                    "step_id": "final_summary", "status": "running",
                    "description": "综合汇总", "phase": "summary",
                }, ensure_ascii=False))
                yield ('content', "\n\n---\n")
                try:
                    final_prompt = plan.final_prompt_template
                    for key, value in context.items():
                        final_prompt = final_prompt.replace(f"{{{key}}}", value)
                    from datetime import datetime as _dt2
                    _today2 = _dt2.now().strftime("%Y-%m-%d")
                    import re as _re3
                    _ct2 = _re3.search(r'今天|今日|昨天|明天|当前|现在', message)
                    _rt2 = _re3.search(r'最近|本周|本月|近.*月|近.*天|近.*年|今年以来', message)
                    if _ct2:
                        final_prompt = (
                            f"【当前日期: {_today2}】报告日期写 {_today2}。"
                            f"无 {_today2} 数据就说无数据\n\n"
                            + final_prompt
                        )
                    elif _rt2:
                        final_prompt = (
                            f"【当前日期: {_today2}】用户问时间段，用日期范围分析趋势\n\n"
                            + final_prompt
                        )
                    async with asyncio.timeout(120):
                        async for chunk_type, chunk_content in llm_service.chat_stream(
                            message=final_prompt, session_id=session_id,
                            system_prompt="你是数据分析专家。直接输出 Markdown 格式报告，不要用 ```markdown 或 ```md 代码块包裹输出。图表用 ```echarts 代码块。注意：遇到参数不明确（时间范围/对象/指标定义）时，先反问确认而非猜测。",
                            model_name=model_name, enable_thinking=enable_thinking, tools=None,
                        ):
                            if chunk_type == 'content':
                                yield ('content', chunk_content)
                    yield ('chain_step', json.dumps({
                        "step_id": "final_summary", "status": "done",
                        "description": "综合汇总", "phase": "summary",
                    }, ensure_ascii=False))
                    summary_ok = 1
                except asyncio.TimeoutError:
                    yield ('chain_step', json.dumps({
                        "step_id": "final_summary", "status": "error",
                        "phase": "summary", "error": "汇总超时",
                    }, ensure_ascii=False))
                except Exception as e:
                    logger.error(f"[ChainEngine] 最终汇总失败: {e}")
                    yield ('chain_step', json.dumps({
                        "step_id": "final_summary", "status": "error",
                        "phase": "summary", "error": str(e),
                    }, ensure_ascii=False))

            reasoning_ok = sum(1 for rs in plan.reasoning_steps
                if not (context.get(rs.output_key, "") or "").startswith(("[错误]", "[超时]")))
            total_steps = len(plan.reasoning_steps) + (1 if plan.final_prompt_template else 0)

        # ── verify 阶段：执行后验证业务目标是否达成 ──
        _vctx = context if ("context" in locals() and isinstance(context, dict)) else data_sections
        verified, verify_summary, verify_detail = None, "", []
        async for _vt, _vc in self._run_verify_phase(
            plan, _vctx, verify_target, plan_label=plan.name or "",
        ):
            if _vt == 'verify_result':
                _vr = json.loads(_vc)
                verified, verify_summary, verify_detail = (
                    _vr.get("verified"), _vr.get("summary", ""), _vr.get("detail", []),
                )
            else:
                yield (_vt, _vc)

        # ── 发送 chain_done ──
        data_ok = sum(1 for v in data_sections.values() if not v.startswith("["))
        try:
            yield ('chain_done', json.dumps({
                "chain_id": plan.chain_id,
                "steps_completed": data_ok + reasoning_ok + summary_ok,
                "total_steps": total_steps,
                "data_queries": data_ok,
                "reasoning_steps": reasoning_ok,
                "summary_ok": summary_ok,
                "verified": verified,
                "verify_summary": verify_summary,
                "verify_detail": verify_detail,
            }, ensure_ascii=False))
            logger.info(f"[ChainEngine] chain_done: {plan.chain_id} ({data_ok + reasoning_ok}/{total_steps})")

            await _emit_chain_done(session_id, plan, data_ok + reasoning_ok + summary_ok, total_steps,
                                   verified=verified, verify_summary=verify_summary)

        finally:
            self._executing = False

    # ── 动态编排 ─────────────────────────────────────────────

    @staticmethod
    def _get_compiled_runtime():
        """获取编译器产出 (供动态编排使用)。"""
        try:
            from app.agents import get_compiled_runtime
            return get_compiled_runtime()
        except Exception:
            return None

    async def _execute_dynamic(
        self, message: str, model_name: str = None,
        enable_thinking: bool = None, session_id: str = "",
        history_messages: list = None,
    ) -> AsyncGenerator[tuple, None]:
        """动态编排: LLM 自主决定多跳查询路径。"""
        from app.agents.compiler.dynamic import DynamicPlanner

        runtime = self._get_compiled_runtime()
        if not runtime:
            yield ('error', '编译器未产出, 动态编排不可用')
            return

        planner = DynamicPlanner(runtime)

        # 埋点：收集查询概念
        import time as _t
        _t_start = _t.time()
        _concepts = []

        # 发送动态编排开始 — mode 在 chain_done 根据实际步数判定
        yield ('chain_start', json.dumps({
            "chain_id": "dynamic",
            "chain_name": "智能分析",
            "mode": "",  # 动态判定：chain_done 时根据实际步数覆盖
            "steps": [],  # 步骤由 LLM 动态决定
            "dynamic": True,
        }, ensure_ascii=False))

        try:
            async for chunk_type, chunk_content in planner.execute(
                message=message, model_name=model_name,
                enable_thinking=enable_thinking, session_id=session_id,
                history_messages=history_messages,
            ):
                if chunk_type == 'step':
                    step = json.loads(chunk_content) if isinstance(chunk_content, str) else chunk_content
                    # 作为 chain_step 事件转发
                    action = step.get('action', '')
                    if action == 'query_start' or action == 'action_start':
                        status = 'running'
                    elif action == 'query_done' or action == 'action_done':
                        status = 'done' if step.get('ok', True) else 'error'
                    elif action == 'error':
                        status = 'error'
                    elif action == 'summary':
                        status = 'done'
                    else:
                        status = 'done'
                    concept = step.get('concept', '')
                    if concept and concept not in _concepts:
                        _concepts.append(concept)
                    error_msg = step.get('error', '')
                    if error_msg:
                        desc = f'失败: {error_msg}'
                    elif step.get('description'):
                        desc = step['description']
                    elif concept:
                        desc = f'查询{concept}'
                    else:
                        desc = '分析步骤'
                    yield ('chain_step', json.dumps({
                        "step_id": f"dynamic_{step.get('step', 1)}",
                        "status": status,
                        "description": desc,
                        "concept": concept,
                        "focus_concepts": concept,
                        "error": error_msg,
                        "phase": "data" if concept else "reasoning",
                        "output_preview": step.get("output_preview", "")[:2000],
                        "model": step.get("model", ""),
                    }, ensure_ascii=False))
                elif chunk_type == 'content':
                    yield ('content', chunk_content)
                elif chunk_type == 'done':
                    done = json.loads(chunk_content) if isinstance(chunk_content, str) else chunk_content
                    steps_taken = done.get("steps_taken", 0)
                    # 埋点：记录 DynamicPlanner 执行详情
                    try:
                        from app.core.tracking import track_dynamic_steps
                        track_dynamic_steps(
                            conversation_id=session_id,
                            message=message,
                            steps_taken=steps_taken,
                            concepts=_concepts,
                            elapsed_ms=int((_t.time() - _t_start) * 1000),
                        )
                    except Exception:
                        pass
                    # 多步查询 → 链式；单步/零步 → 合并
                    actual_mode = "chained" if steps_taken >= 2 else "merged"
                    yield ('chain_done', json.dumps({
                        "chain_id": "dynamic",
                        "steps_completed": steps_taken,
                        "total_steps": steps_taken,
                        "data_queries": steps_taken,
                        "reasoning_steps": 1,
                        "dynamic": True,
                        "mode": actual_mode,
                    }, ensure_ascii=False))

                    # ── emit 事件: plan.executed (dynamic) ──
                    try:
                        from app.services.event_bus import event_bus
                        await event_bus.publish("plan.executed", {
                            "conversation_id": session_id,
                            "chain_id": "dynamic",
                            "chain_name": "智能分析",
                            "mode": actual_mode,
                            "steps_completed": steps_taken,
                            "total_steps": steps_taken,
                            "status": "ok",
                            "error_summary": "",
                        })
                    except Exception:
                        pass

                else:
                    # 透传 thinking / tool_call 等未显式处理的 chunk
                    yield (chunk_type, chunk_content)
        except Exception as e:
            logger.error(f"[ChainEngine] 动态编排失败: {e}")
            yield ('error', f'动态编排失败: {e}')

    # ── 计划构建 ─────────────────────────────────────────────

    async def _build_plan(self, chain_id: str, message: str) -> ChainPlan:
        """从本体关系 + YAML 配置动态构建查询计划。

        1. 查找消息中提到的概念
        2. 通过本体关系发现关联概念（1 跳）
        3. 为有 _query Action 的概念构建查询步骤
        4. 从 config/chains.yaml 加载推理步骤
        """
        from app.services.ontology_service import ontology_service
        from app.services.action_executor import action_executor

        action_executor._ensure_loaded()
        concepts = ontology_service.get_concepts()
        concept_map = {c["name"]: c for c in concepts}

        # 查找消息中提到的概念，通过本体关系发现关联概念（限制数量）
        all_names: set[str] = set()
        relations: list[tuple] = []

        # 优先用链配置的数据集
        chain_cfg = _CHAINS.get(chain_id, {})
        focus = chain_cfg.get("focus_concepts", "")
        if focus:
            all_names = set(focus.replace(" ", "").split(","))
        else:
            # 无配置时，LLM 提取消息中的概念
            mentioned = self._find_mentioned_concepts(message, concepts)
            for c in mentioned[:5]:
                all_names.add(c["name"])
                for rel in c.get("relations", [])[:3]:
                    target = rel["target"]
                    if target in concept_map and len(all_names) < 12:
                        all_names.add(target)
                        relations.append((
                            c.get("label", c["name"]),
                            rel.get("label", ""),
                            concept_map[target].get("label", target),
                        ))
            # 仍然为空 → 全量兜底
            if not all_names:
                all_names = set(concept_map.keys())

        # 构建查询概念列表
        query_concepts = []
        for cn in all_names:
            tool_name = f"{cn}_query"
            if tool_name in action_executor._sigs:
                c = concept_map.get(cn, {})
                query_concepts.append((cn, c.get("label", cn), tool_name))

        # 从 YAML 配置加载推理步骤
        chain_cfg = _CHAINS.get(chain_id, {})
        reasoning_steps = [
            ReasoningStep(
                step_id=rs["step_id"],
                description=rs.get("description", ""),
                agent_name=rs.get("agent_name", "analysis_monitor"),
                prompt_template=rs.get("prompt_template", ""),
                output_key=rs.get("output_key", ""),
                focus_concepts=rs.get("focus_concepts", ""),
                action_name=rs.get("action_name", ""),
                action_params=rs.get("action_params", "{}"),
                precondition=rs.get("precondition", ""),
                on_failure=rs.get("on_failure", "abort"),
            )
            for rs in chain_cfg.get("reasoning_steps", [])
        ]

        return ChainPlan(
            chain_id=chain_id,
            name=chain_cfg.get("name", chain_id),
            description=chain_cfg.get("description", ""),
            concepts=query_concepts,
            relations=relations,
            reasoning_steps=reasoning_steps,
            final_prompt_template=chain_cfg.get("final_prompt_template", ""),
            mode=chain_cfg.get("mode", "analysis"),
        )

    async def _run_verify_phase(
        self, plan, verify_context=None, verify_target=None, plan_label="",
    ) -> tuple:
        """执行链后验证（verify）阶段：验证业务目标是否真正达成（不只步骤执行成功）。

        两条路径（用户已确认"两者结合"）：
        1. verify 链优先：配置 {chain_id}_verify 链（与 rollback 链对称），
           其步骤查询复查概念收集"执行后状态"，final_prompt_template（无则默认 prompt）
           让 LLM 判定目标是否达成。
        2. verify_target 回退：LLM 方案声明的 {concept, property, expected, label, filters}，
           执行后自动查询目标概念，LLM 对比期望判定。
        3. 都无 → 返回 (None, "", [])，不产出验证步骤。

        未通过仅标记 needs_review（人工复核），不自动回滚。
        返回 (verified, verify_summary, verify_detail)。
        """
        from app.services.llm_service import llm_service
        from app.services.action_executor import action_executor

        def _emit(status, description, **extra):
            payload = {
                "step_id": "verify", "status": status,
                "description": description, "phase": "verify", **extra,
            }
            return ('chain_step', json.dumps(payload, ensure_ascii=False))

        def _result(verified, summary, detail):
            """async generator 不能 return 值，用特殊事件携带验证结果。"""
            return ("verify_result", json.dumps(
                {"verified": verified, "summary": summary, "detail": detail},
                ensure_ascii=False,
            ))

        async def _judge(goal: str, data_text: str, template: str = "") -> tuple:
            """LLM 判定目标是否达成，并提取实际值。

            返回 (verified, reason, actual)——actual 为 LLM 从复查数据中提取的实际值，
            供结构化硬对比（verify_detail）使用。
            """
            prompt = (template or _DEFAULT_VERIFY_PROMPT)
            prompt = prompt.replace("{plan_label}", plan_label or plan.name or "")
            prompt = prompt.replace("{goal}", goal)
            if "{verify_data}" in prompt:
                prompt = prompt.replace("{verify_data}", str(data_text)[:6000])
            else:
                prompt = prompt + f"\n\n## 复查数据\n{str(data_text)[:6000]}"
            try:
                raw = await asyncio.wait_for(
                    llm_service.chat_sync(
                        message=prompt,
                        system_prompt="你是执行结果验证器，只输出 JSON，不输出其他文字。",
                    ),
                    timeout=20.0,
                )
            except asyncio.TimeoutError:
                logger.warning("[ChainEngine] verify LLM 判定超时，标记人工复核")
                return None, "验证判定超时，请人工复核", None
            except Exception as e:
                logger.warning(f"[ChainEngine] verify LLM 判定失败: {e}")
                return None, "验证判定失败，请人工复核", None
            verdict = _parse_verify_json(raw)
            verified = verdict.get("verified")
            reason = verdict.get("reason", "")
            actual = verdict.get("actual")
            if verified is None:
                return False, reason or "验证判定失败，请人工复核", actual
            return bool(verified), reason, actual

        verify_id = f"{plan.chain_id}_verify"
        verify_cfg = _CHAINS.get(verify_id)

        # ── 路径 1：verify 链 ──
        if verify_cfg:
            yield _emit("running", f"执行验证链：{verify_cfg.get('name', verify_id)}",
                        verify_chain=verify_id)
            vctx = dict(verify_context or {})
            vctx.setdefault("message", "")
            steps_ok = 0
            try:
                action_executor._ensure_loaded()
                for rs_data in verify_cfg.get("reasoning_steps", []):
                    rs = ReasoningStep(
                        step_id=rs_data.get("step_id", ""),
                        description=rs_data.get("description", ""),
                        agent_name=rs_data.get("agent_name", ""),
                        prompt_template=rs_data.get("prompt_template", ""),
                        output_key=rs_data.get("output_key", "") or f"verify_out_{steps_ok}",
                        focus_concepts=rs_data.get("focus_concepts", ""),
                        action_name=rs_data.get("action_name", ""),
                        action_params=rs_data.get("action_params", "{}"),
                        precondition=rs_data.get("precondition", ""),
                        on_failure=rs_data.get("on_failure", "skip"),
                    )
                    try:
                        params = _render_template(rs.action_params, vctx) if rs.action_params else {}
                        if rs.action_name:
                            exec_result = await action_executor.execute_structured_async(
                                rs.action_name, params, user_id="",
                            )
                        elif rs.focus_concepts:
                            sig = action_executor._sigs.get(f"{rs.focus_concepts}_query")
                            if sig:
                                exec_result = await action_executor._execute_query(sig, params)
                            else:
                                exec_result = f"[{rs.focus_concepts}] 无查询签名"
                        else:
                            exec_result = ""
                        vctx[rs.output_key] = exec_result
                        steps_ok += 1
                    except Exception as e:
                        vctx[rs.output_key] = f"[验证步骤错误: {e}]"
                goal = f"执行链 {plan.name}（{plan.chain_id}）的业务目标是否达成"
                verified, reason, actual = await _judge(
                    goal, json.dumps(vctx, ensure_ascii=False, default=str),
                    verify_cfg.get("final_prompt_template", ""),
                )
                detail = [{
                    "property": "业务目标",
                    "expected": "达成",
                    "actual": str(actual) if actual is not None else "(综合判定)",
                    "match": verified,
                }]
                summary = f"验证：{'通过' if verified else '需人工复核'} — {reason}"
                yield _emit("done", summary, verified=verified, summary=summary,
                            verify_detail=detail, verify_chain=verify_id, steps_ok=steps_ok)
                yield _result(verified, summary, detail)
                return
            except Exception as e:
                logger.warning(f"[ChainEngine] verify 链执行失败: {e}")
                yield _emit("error", f"验证链执行失败: {e}", error=str(e))
                yield _result(None, f"验证失败: {e}", [])
                return

        # ── 路径 2：verify_target 自动复查 ──
        if verify_target and isinstance(verify_target, dict) and verify_target.get("concept"):
            concept = verify_target["concept"]
            prop = verify_target.get("property", "")
            expected = str(verify_target.get("expected", "") or "").strip()
            label = verify_target.get("label", "") or f"{concept}.{prop}"
            yield _emit("running", f"验证：{label}")
            try:
                # 只读 Cypher 硬取目标属性值（工业界 AgentSkeptic 模式：确定性验证，
                # 不经过 LLM 提取——LLM 从文本看值会引入共享盲区）
                actual = await self._cypher_get_property(
                    concept, prop, _build_verify_filters(verify_target),
                )
                match = _compare_hard(expected, actual)
                detail = [{
                    "property": prop,
                    "expected": expected,
                    "actual": str(actual) if actual is not None else "(未取到)",
                    "match": match,
                }]
                # 纯硬判定：能取到实际值就硬比；取不到则保守标记需人工复核
                verified = match if match is not None else False
                actual_str = str(actual) if actual is not None else "未取到"
                summary = (f"{label}：{'验证通过' if verified else '需人工复核'} — "
                           f"期望 {expected}，实际 {actual_str}")
                yield _emit("done", summary, verified=verified, summary=summary,
                            verify_detail=detail, verify_target=label)
                yield _result(verified, summary, detail)
                return
            except Exception as e:
                logger.warning(f"[ChainEngine] verify_target 复查失败: {e}")
                yield _emit("error", f"验证失败: {e}", error=str(e))
                yield _result(None, f"验证失败: {e}", [])
                return

        # ── 路径 3：无验证配置 ──
        yield _result(None, "", [])
        return

    async def _cypher_get_property(
        self, concept: str, prop: str, filters: dict,
    ) -> Optional[Any]:
        """只读 Cypher 直查概念的目标属性值（确定性取值，不经过 LLM）。

        verify_target 校验用：按 filters 定位目标记录，直接 RETURN n.{prop}。
        返回程序取到的实际值，无记录或取值失败返回 None。
        """
        from app.services.neo4j_service import neo4j_service
        from app.services.action_executor import action_executor
        from app.core.config import settings
        action_executor._ensure_loaded()
        concept_def = action_executor._concepts.get(concept, {})
        ns = concept_def.get("namespace") or settings.NEO4J_NAMESPACE
        where = []
        params = {}
        for k, v in filters.items():
            if v is None or str(v).strip() == "":
                continue
            p = f"p{len(params)}"
            where.append(f"n.`{k}` CONTAINS ${p}")
            params[p] = str(v)
        if not where:
            logger.warning(f"[ChainEngine] verify 直查缺少定位条件 {concept}.{prop}")
            return None
        if ns:
            where.append("n._namespace = $ns")
            params["ns"] = ns
        cypher = (f"MATCH (n:`{concept}`) WHERE {' AND '.join(where)} "
                  f"RETURN n.`{prop}` AS val LIMIT 1")
        try:
            records = await neo4j_service.execute_read(cypher, params)
            if records:
                return records[0].get("val")
        except Exception as e:
            logger.warning(f"[ChainEngine] verify 直查失败 {concept}.{prop}: {e}")
        return None

    def _find_mentioned_concepts(self, message: str, concepts: list) -> list:
        """查找用户消息中提及的本体概念。"""
        mentioned = []
        for c in concepts:
            label = c.get("label", "")
            name = c.get("name", "")
            desc = c.get("description", "")
            if label and label in message:
                mentioned.append(c)
            elif name and name.lower() in message.lower():
                mentioned.append(c)
            elif desc:
                for kw in self._extract_keywords(desc):
                    if kw in message and len(kw) >= 2:
                        mentioned.append(c)
                        break
        return mentioned

    @staticmethod
    def _extract_keywords(text: str) -> set:
        if not text:
            return set()
        kw = set()
        for part in re.split(r'[，。、；：（）\s]+', text):
            part = part.strip()
            if len(part) >= 2:
                kw.add(part)
        return kw

    async def _extract_params_for_concept(self, message: str, concept_name: str,
                                     steps_taken: list = None, context: dict = None) -> dict:
        """从消息中提取概念查询的过滤参数（含跨概念自动注入）。"""
        from app.services.intent_router import intent_router

        tool_name = f"{concept_name}_query"
        params = intent_router.extract_params(message, tool_name)

        # 优先匹配编码格式 (MO001, WO-20250521-001)
        m = re.search(r'[A-Z]{2,}[\d-]+', message) or re.search(r'[A-Z]{2,}-\d+(?:-\d+)*', message)
        code_val = m.group() if m else None

        # 跨概念自动注入（与 DynamicPlanner 共享逻辑），结果优先于 intent_router
        try:
            from app.agents.compiler.param_extractor import extract_params_with_cross_concept
            runtime = self._get_compiled_runtime()
            cross_params = await extract_params_with_cross_concept(
                message=message,
                concept=concept_name,
                compiled_runtime=runtime,
                steps_taken=steps_taken,
                context=context,
            )
            if cross_params:
                return cross_params
        except Exception:
            pass

        # 回退 1: 实体编码匹配（优先于 intent_router 的泛化参数）
        if code_val:
            from app.services.ontology_service import ontology_service
            concept = ontology_service.get_concept(concept_name)
            if concept:
                for prop in concept.get("properties", []):
                    if prop.get("isPrimary"):
                        return {prop["name"]: code_val}

        # 回退 2: intent_router 有值时返回
        if any(v for v in params.values() if v):
            return params

        return params


# ── Pipeline 辅助函数 ──────────────────────────────────────────

def _render_template_str(template_str: str, context: dict) -> str:
    """渲染字符串中的 {{变量}} 模板。"""
    if not template_str:
        return ""
    import re as _re
    def _resolve(key_path: str) -> str:
        parts = key_path.split("||")
        key = parts[0].strip()
        default = parts[1].strip() if len(parts) > 1 else ""
        keys = key.split(".")
        val = context
        for kk in keys:
            if isinstance(val, dict):
                val = val.get(kk, default)
            else:
                return default
        return str(val) if val else default
    return _re.sub(r'\{\{([^}]+)\}\}', lambda m: _resolve(m.group(1)), template_str)


def _render_template(template_str: str, context: dict) -> dict:
    """渲染 {{变量}} 模板，返回解析后的 dict。"""
    if not template_str:
        return {}
    import json as _json
    if isinstance(template_str, str):
        try:
            template_str = _json.loads(template_str)
        except Exception:
            return {}
    result = {}
    for k, v in template_str.items():
        if isinstance(v, str) and "{{" in v:
            # 替换 {{path.to.key}}
            import re as _re
            val = v
            for m in _re.findall(r'\{\{([^}]+)\}\}', v):
                # 支持 {{key}} 和 {{key || default}}
                parts = m.split("||")
                key = parts[0].strip()
                default = parts[1].strip() if len(parts) > 1 else ""
                # 从 context 取值
                keys = key.split(".")
                ctx_val = context
                for kk in keys:
                    if isinstance(ctx_val, dict):
                        ctx_val = ctx_val.get(kk, default)
                    else:
                        ctx_val = default
                        break
                val = val.replace("{{" + m + "}}", str(ctx_val) if ctx_val != default else default)
            result[k] = val
        else:
            result[k] = v
    return result


def _eval_precondition(expr: str, context: dict) -> bool:
    """简单的前置条件评估。支持 ==, !=, >, <, in 操作符。"""
    if not expr or not expr.strip():
        return True
    import re as _re
    expr = expr.strip()
    # 替换 {{变量}}
    for m in _re.findall(r'\{\{([^}]+)\}\}', expr):
        keys = m.split(".")[0].strip()
        ctx_val = context.get(keys, "")
        if isinstance(ctx_val, dict):
            ctx_val = ctx_val.get("result", str(ctx_val))
        expr = expr.replace("{{" + m + "}}", str(ctx_val))
    try:
        # 安全评估：只允许简单比较
        if "==" in expr:
            left, right = expr.split("==", 1)
            return str(eval(left.strip())) == str(eval(right.strip()))
        if "!=" in expr:
            left, right = expr.split("!=", 1)
            return str(eval(left.strip())) != str(eval(right.strip()))
        if ">=" in expr:
            left, right = expr.split(">=", 1)
            return float(eval(left.strip())) >= float(eval(right.strip()))
        if "<=" in expr:
            left, right = expr.split("<=", 1)
            return float(eval(left.strip())) <= float(eval(right.strip()))
        if ">" in expr:
            left, right = expr.split(">", 1)
            return float(eval(left.strip())) > float(eval(right.strip()))
        if "<" in expr:
            left, right = expr.split("<", 1)
            return float(eval(left.strip())) < float(eval(right.strip()))
    except Exception:
        pass
    return True  # 无法评估时默认通过


# 全局单例
chain_engine = OntologyChainEngine()
