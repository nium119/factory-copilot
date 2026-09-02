# -*- coding: utf-8 -*-
"""写操作标准流程（SOP）— 对齐 DSH 的「先查证 → 再变更 → 审批 → 复查」。

DSH 把「工单变更/删除的标准操作流程」沉淀为可加载的技能（skill），agent 匹配后按
SOP 执行；FC 此前没有这套过程性知识，写操作全靠决策 LLM 临场发挥，显得「死板」。
本模块把 SOP 固化为确定性行为（不依赖 LLM 自觉）：

  - precheck：删除/变更前查关联下游节点，把影响面讲给用户（删前查证）
  - postcheck：执行后按主键回读，确认落库/删除生效（删后复查）

只读 Cypher（与 DSH 的 ontology_query 同性质），不改业务数据。
"""
import logging
from typing import Optional

log = logging.getLogger(__name__)

# 需要查证影响面的写操作类型（create 无下游影响面，不需要 precheck）
_PRECheck_ACTIONS = ("delete", "update")


def _rows_of(result) -> list:
    """健壮地提取查询记录列表（neo4j_service.execute_read 返回结构多变）。"""
    if isinstance(result, list):
        return result if result and isinstance(result[0], dict) else []
    if isinstance(result, dict):
        for k in ("records", "rows", "data"):
            if isinstance(result.get(k), list):
                return result[k]
        for v in result.values():
            if isinstance(v, list):
                return v
    return []


async def precheck(concept_name: str, pk_name: str, pk_value, action_type: str) -> Optional[dict]:
    """删除/变更前查证影响面。

    返回 {summary, links}；无关联下游时 links 为空、summary 给「不影响其他数据」结论。
    查不到实体或非 delete/update 时返回 None（不打断主流程）。
    """
    if action_type not in _PRECheck_ACTIONS or not concept_name or not pk_name or not pk_value:
        return None
    try:
        from app.services.neo4j_service import neo4j_service
        cypher = (
            f"MATCH (n:`{concept_name}` {{{pk_name}: $v}})-[r]-(x) "
            "RETURN type(r) AS rel, labels(x) AS labs, count(x) AS cnt"
        )
        result = await neo4j_service.execute_read(cypher, {"v": pk_value})
        links = []
        for row in _rows_of(result):
            rel = row.get("rel") if isinstance(row, dict) else None
            labs = row.get("labs") if isinstance(row, dict) else None
            cnt = row.get("cnt") if isinstance(row, dict) else 1
            if rel:
                label = labs[0] if isinstance(labs, list) and labs else str(labs)
                links.append({"rel": rel, "label": label, "count": int(cnt or 0)})
        if not links:
            return {"summary": f"已查证：该{concept_name}无任何关联下游节点，变更/删除不影响其他数据。", "links": []}
        desc = "、".join(f"{l['count']} 条「{l['label']}」({l['rel']})" for l in links[:6])
        return {
            "summary": f"已查证：该{concept_name}关联 {desc}，变更/删除将影响这些数据，请确认影响面。",
            "links": links,
        }
    except Exception as e:
        log.warning(f"[WriteSOP] 影响面查证失败 {concept_name}.{pk_name}={pk_value}: {e}")
        return None


async def postcheck(concept_name: str, pk_name: str, pk_value, action_type: str) -> Optional[str]:
    """执行后按主键回读，确认落库/删除/更新生效。

    返回复查结论一句话；回读失败返回 None（不打断主流程）。
    """
    if not concept_name or not pk_name or not pk_value:
        return None
    try:
        from app.services.neo4j_service import neo4j_service
        cypher = f"MATCH (n:`{concept_name}` {{{pk_name}: $v}}) RETURN count(n) AS cnt"
        result = await neo4j_service.execute_read(cypher, {"v": str(pk_value)})
        rows = _rows_of(result)
        exists = bool(rows) and bool((rows[0].get("cnt") if isinstance(rows[0], dict) else 1))
        if action_type == "delete":
            return "复查确认：已回读，该记录已不存在，删除生效。" if not exists else "复查提示：删除后仍能查到该记录，请核实。"
        if action_type == "create":
            return "复查确认：已回读，新记录已落库。" if exists else "复查提示：创建后未回读到新记录，请核实。"
        if action_type == "update":
            return "复查确认：已回读，更新已生效。" if exists else "复查提示：更新后未回读到记录，请核实。"
        return None
    except Exception as e:
        log.warning(f"[WriteSOP] 复查回读失败 {concept_name}.{pk_name}={pk_value}: {e}")
        return None
