"""参数提取器 — 跨概念自动注入 join key 的共享逻辑。

DynamicPlanner 和 ChainEngine 共用此模块，确保链式模式和动态模式
都有一致的跨概念过滤能力。
"""

import re
from typing import Optional

from loguru import logger


async def extract_params_with_cross_concept(
    message: str,
    concept: str,
    *,
    compiled_runtime=None,
    steps_taken: Optional[list] = None,
    context: Optional[dict] = None,
    all_values: Optional[list] = None,
) -> dict:
    """从消息中提取查询参数，自动注入跨概念 join key。

    优先级：
    1. 消息编码 → 上游实体解析（resolve entity → join key）
    2. 上一跳结果 → 提取 join key 值（第二跳及后续）
    3. 直接匹配当前概念参数

    参数:
        message: 用户消息
        concept: 当前要查询的概念名
        compiled_runtime: CompiledRuntime 实例（提供 skills 列表）
        steps_taken: 已执行的步骤列表 [{"concept": "...", ...}]
        context: 上下文 dict，包含 "{concept}_records" 键
        all_values: 预提取的消息编码/数字列表（若为 None 则自动提取）
    """
    from app.services.action_executor import action_executor
    action_executor._ensure_loaded()

    # 获取当前概念的查询参数（优先从运行时 skill）
    sig_params = _get_sig_params(concept, compiled_runtime)

    # 提取消息中的编码/数字
    if all_values is None:
        codes = re.findall(r'([A-Z]{2,6}[-_]?\d{2,8})', message)
        nums = re.findall(r'(?<![a-zA-Z])(\d{4,})(?![a-zA-Z])', message)
        all_values = codes + nums

    params: dict = {}

    if not all_values:
        return params

    # ── 策略 1: 消息编码 → 上游实体解析 ──
    upstream_candidates = _build_upstream_candidates(
        concept, steps_taken, compiled_runtime
    )
    from app.services.neo4j_service import neo4j_service

    for upstream_concept in upstream_candidates:
        join_key, target_key = _find_join_keys(upstream_concept, concept)
        if not join_key:
            continue

        for val in all_values:
            entity = None
            try:
                upstream_def = action_executor._concepts.get(upstream_concept, {})
                upstream_pk = "id"
                for pp in upstream_def.get("properties", []):
                    if pp.get("isPrimary"):
                        upstream_pk = pp["name"]
                        break
                ns = upstream_def.get("namespace", "")
                ns_where = " AND n._namespace = $ns" if ns else ""
                records = await neo4j_service.execute_read(
                    f"MATCH (n:{upstream_concept}) WHERE n.`{upstream_pk}` = $kw{ns_where} RETURN n LIMIT 1",
                    {"kw": val, "ns": ns},
                )
                if records:
                    entity = dict(records[0]["n"])
            except Exception:
                continue

            if entity and entity.get(join_key) is not None:
                join_value = entity[join_key]
                if _match_and_set_param(params, sig_params, upstream_concept, concept,
                                        join_key, target_key, join_value):
                    logger.info(
                        f"[ParamExtractor] 上游注入: {upstream_concept}.{join_key}={join_value} → {concept}"
                    )
                    return params
        if params:
            return params

    # ── 策略 2: 上一跳结果 → 提取 join key ──
    if steps_taken and context:
        prev_step = steps_taken[-1]
        prev_concept = prev_step.get("concept", "")
        if prev_concept and prev_concept != concept:
            prev_records = context.get(f"{prev_concept}_records", [])
            if prev_records:
                join_key, target_key = _find_join_keys(prev_concept, concept)
                if join_key:
                    join_values = []
                    seen = set()
                    for rec in prev_records:
                        val = rec.get(join_key)
                        if val is not None and str(val) not in seen:
                            seen.add(str(val))
                            join_values.append(val)
                            if len(join_values) >= 50:
                                break
                    if join_values:
                        _match_and_set_param(params, sig_params, prev_concept, concept,
                                            join_key, target_key, join_values[0])
                        if params:
                            logger.info(
                                f"[ParamExtractor] 上一跳注入: {prev_concept}.{join_key}"
                                f"={join_values[:3]} → {concept}"
                            )
                            return params

    return params


# ── 辅助函数 ────────────────────────────────────────────────────


def _get_sig_params(concept: str, compiled_runtime=None) -> list[dict]:
    """获取概念的查询参数（优先从运行时 skill，回退到 action_executor）。"""
    sig_params = []
    if compiled_runtime:
        skill_map = {s.concept: s for s in compiled_runtime.skills}
        skill = skill_map.get(concept)
        if skill and skill.input_params:
            sig_params = [
                {
                    "name": p.name,
                    "label": p.label,
                    "type": p.type,
                    "required": p.required,
                    "conceptPropertyRef": p.conceptPropertyRef,
                }
                for p in skill.input_params
            ]
    if not sig_params:
        from app.services.action_executor import action_executor
        action_executor._ensure_loaded()
        sig = action_executor._sigs.get(f"{concept}_query", {})
        sig_params = sig.get("params", [])
    return sig_params


def _build_upstream_candidates(
    concept: str,
    steps_taken: Optional[list],
    compiled_runtime=None,
) -> list[str]:
    """构建上游概念候选列表（有关系到当前概念的已查询/已编译概念）。"""
    candidates = []
    if steps_taken:
        for prev_step in reversed(steps_taken):
            pc = prev_step.get("concept", "")
            if pc and pc != concept:
                candidates.append(pc)
    if compiled_runtime:
        for skill in compiled_runtime.skills:
            sc = skill.concept
            if sc == concept or sc in candidates:
                continue
            jk, _ = _find_join_keys(sc, concept)
            if jk:
                candidates.append(sc)
    return candidates


def _find_join_keys(from_concept: str, to_concept: str) -> tuple:
    """查找两个概念间的 join key。返回 (from_side_key, to_side_key) 或 (None, None)。"""
    from app.services.action_executor import action_executor
    action_executor._ensure_loaded()

    from_def = action_executor._concepts.get(from_concept, {})
    for rel in from_def.get("relations", []):
        if rel.get("target") == to_concept and rel.get("joinOn"):
            keys = _parse_join_on(rel["joinOn"], from_concept, to_concept)
            if keys[0]:
                return keys

    to_def = action_executor._concepts.get(to_concept, {})
    for rel in to_def.get("relations", []):
        if rel.get("target") == from_concept and rel.get("joinOn"):
            keys = _parse_join_on(rel["joinOn"], from_concept, to_concept)
            if keys[0]:
                return keys
    return (None, None)


def _parse_join_on(join_on: str, from_concept: str, to_concept: str) -> tuple:
    """解析 joinOn 字符串，提取 from/to 两侧的属性名。"""
    from_key, to_key = None, None
    for part in join_on.split("="):
        part = part.strip()
        if part.startswith(from_concept + "."):
            from_key = part.split(".")[1].strip()
        elif part.startswith(to_concept + "."):
            to_key = part.split(".")[1].strip()
    return (from_key, to_key)


def _match_and_set_param(
    params: dict,
    sig_params: list[dict],
    upstream_concept: str,
    target_concept: str,
    join_key: str,
    target_key: str,
    join_value,
) -> bool:
    """尝试将 join_value 匹配到 sig_params 中的参数。成功返回 True。"""
    # 精确匹配 conceptPropertyRef
    for p in sig_params:
        prop_ref = p.get("conceptPropertyRef", "")
        if prop_ref and prop_ref == f"{upstream_concept}.{join_key}":
            params[p["name"]] = join_value
            return True
    # 参数名匹配
    for p in sig_params:
        if p["name"] in (target_key, join_key):
            params[p["name"]] = join_value
            return True
    # 回退：conceptPropertyRef 前缀匹配
    for p in sig_params:
        prop_ref = p.get("conceptPropertyRef", "")
        if prop_ref and prop_ref.startswith(upstream_concept + "."):
            params[p["name"]] = join_value
            return True
    return False
