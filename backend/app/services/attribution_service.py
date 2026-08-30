# -*- coding: utf-8 -*-
"""归因分析服务 — 库龄90天原因分析的确定性算法。

落地 xlsx《库龄90天原因分析维度_AIAgent应用.xlsx》的确定性公式：
  - 取消BOM需求量 = 取消数量 × BOM用量
  - 专用化率 = 该物料本客户/项目需求 ÷ 全部有效需求（≥80%专用 / 50~80%半专用 / <50%通用）
  - 净影响量 = MAX(0, 取消BOM需求量 - 其他可吸收需求量)
  - 最终暴露量 = MIN(净影响量, 当前库存 + 在途 + 不可取消PO)
  - 贡献率 = MIN(影响数量 ÷ 库龄≥90天库存数量, 100%)
  - 关键日期判断：事件Date > PO Date → 事后需求变化；事件Date ≤ PO Date → 已知变化仍采购

确定性优先：纯 Python + Neo4j 查询，不调 LLM。
"""
from datetime import datetime

from app.core.logger import log
from app.services.neo4j_service import neo4j_service


def _num(v, default=0.0):
    """安全转数值，失败返回默认。"""
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _days_between(date_str, ref=None):
    """计算 date_str 距 ref（默认今天）的天数。解析失败返回 None。"""
    if not date_str:
        return None
    s = str(date_str).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
        dt = dt.replace(tzinfo=None)
        ref = ref or datetime.now()
        return (ref - dt).days
    except (ValueError, TypeError):
        return None


async def _q(cypher, params):
    """执行只读查询，返回记录字典列表。"""
    try:
        return await neo4j_service.execute_read(cypher, params or {})
    except Exception as e:
        log.warning(f"[Attribution] 查询失败: {e}")
        return []


async def _active_ns() -> str:
    """当前本体图谱 namespace。"""
    try:
        from app.services.ontology_service import ontology_service
        return ontology_service.active_namespace or ""
    except Exception:
        return ""


def _ns_clause(ns: str) -> str:
    return " AND n._namespace = $ns" if ns else ""


async def analyze_order_cancel(so_id: str, namespace: str = "") -> dict:
    """订单取消影响归因（xlsx 检测场景1 / 维度2 核心）。

    流程：取消事件 → 展开 BOM → 子件物料 → 取消BOM需求 → 净影响 → 最终暴露 → 归因。
    """
    ns = namespace or await _active_ns()
    result = {"soId": so_id, "namespace": ns, "materials": []}

    # 1) 取消事件
    evt = await _q(
        "MATCH (c:SoChangeLog {soId: $soId}) WHERE c._namespace = $ns OR $ns = '' "
        "RETURN c.changeType AS changeType, c.changeDate AS changeDate, "
        "c.changeQty AS changeQty, c.reason AS reason LIMIT 1",
        {"soId": so_id, "ns": ns},
    )
    if not evt:
        result["error"] = f"未找到销售订单 {so_id} 的变更日志"
        return result
    evt = evt[0]
    change_qty = _num(evt.get("changeQty"))
    change_date = evt.get("changeDate")
    result["event"] = {
        "changeType": evt.get("changeType", ""),
        "changeDate": change_date,
        "changeQty": change_qty,
        "reason": evt.get("reason", ""),
    }

    # 2) SO 的成品物料（取第一条分录的成品）
    soe = await _q(
        "MATCH (e:WmsSaleOrderEntry {saleOrderId: $soId}) WHERE e._namespace = $ns OR $ns = '' "
        "RETURN e.materialId AS materialId, e.itemNo AS itemNo, e.qty AS qty LIMIT 1",
        {"soId": so_id, "ns": ns},
    )
    if not soe:
        result["error"] = f"未找到销售订单 {so_id} 的分录（成品物料）"
        return result
    product_id = soe[0].get("materialId")

    # 3) BOM 展开：成品 → 子件 + 用量
    bom_rows = await _q(
        "MATCH (m:MdmMaterial {id: $matId}) "
        "MATCH (b:BaseBom)-[:被BOM主表引用]->(m) "
        "MATCH (be:BaseBomEntry)-[:属于BOM]->(b) "
        "MATCH (be)-[:被BOM分录引用]->(sub:MdmMaterial) "
        "RETURN sub.id AS subId, sub.name AS subName, sub.itemNo AS itemNo, "
        "coalesce(be.baseQty, be.qty, 1) AS usage",
        {"matId": product_id},
    )

    # 4) 逐子件计算归因
    for row in bom_rows:
        sub_id = row.get("subId")
        usage = _num(row.get("usage"), 1.0)
        cancel_bom_qty = change_qty * usage

        # 4.1 其他可吸收需求（该子件在其他未取消 SO 的需求）
        absorb_rows = await _q(
            "MATCH (e:WmsSaleOrderEntry {materialId: $subId}) "
            "WHERE (e._namespace = $ns OR $ns = '') AND e.saleOrderId <> $soId "
            "RETURN coalesce(sum(toFloat(e.qty)), 0) AS absorbable",
            {"subId": sub_id, "soId": so_id, "ns": ns},
        )
        absorbable = _num(absorb_rows[0].get("absorbable")) if absorb_rows else 0.0

        # 4.2 专用化率（本客户需求 ÷ 总需求，近似 = 1 - 可吸收占比）
        total_demand = cancel_bom_qty + absorbable
        specialization_rate = (cancel_bom_qty / total_demand * 100) if total_demand > 0 else 100.0

        # 4.3 净影响量
        net_impact = max(0.0, cancel_bom_qty - absorbable)

        # 4.4 库存/在途 + 库存形成PO日期
        inv_rows = await _q(
            "MATCH (i:Inventory {materialId: $subId}) WHERE i._namespace = $ns OR $ns = '' "
            "RETURN i.qty AS qty, i.onPassageQty AS onPassage, i.formationPoDate AS poDate, "
            "i.lastInDate AS lastInDate",
            {"subId": sub_id, "ns": ns},
        )
        stock = sum(_num(r.get("qty")) for r in inv_rows)
        on_passage = sum(_num(r.get("onPassage")) for r in inv_rows)
        formation_po_date = next((r.get("poDate") for r in inv_rows if r.get("poDate")), None)

        # 4.5 不可取消 PO（未关闭 PO 的该物料数量，按 itemNo 匹配）
        open_po = 0.0
        if row.get("itemNo"):
            po_rows = await _q(
                "MATCH (e:PurchaseOrderEntry {itemCode: $itemNo}) "
                "WHERE (e._namespace = $ns OR $ns = '') AND coalesce(e.bClose, false) <> true "
                "RETURN coalesce(sum(toFloat(e.qty)), 0) AS qty",
                {"itemNo": row.get("itemNo"), "ns": ns},
            )
            open_po = _num(po_rows[0].get("qty")) if po_rows else 0.0

        # 4.6 最终暴露量
        final_exposure = min(net_impact, stock + on_passage + open_po)

        # 4.7 关键日期判断
        key_date_judge = _judge_key_date(change_date, formation_po_date)

        # 4.8 贡献率（影响量 ÷ 库龄≥90天库存量）
        aged_stock = stock  # 简化：以当前库存为 90 天库存分母（可后续按 lastInDate 过滤）
        contribution_rate = min(net_impact / aged_stock * 100, 100.0) if aged_stock > 0 else 0.0

        result["materials"].append({
            "materialId": sub_id,
            "materialName": row.get("subName", ""),
            "itemNo": row.get("itemNo", ""),
            "bomUsage": usage,
            "cancelBomQty": round(cancel_bom_qty, 2),
            "absorbableQty": round(absorbable, 2),
            "specializationRate": round(specialization_rate, 1),
            "specializationClass": _classify(specialization_rate),
            "netImpactQty": round(net_impact, 2),
            "stockQty": stock,
            "onPassageQty": on_passage,
            "openPoQty": open_po,
            "finalExposureQty": round(final_exposure, 2),
            "formationPoDate": formation_po_date or "",
            "keyDateJudge": key_date_judge,
            "contributionRate": round(contribution_rate, 1),
        })

    return result


def _classify(rate: float) -> str:
    """专用化率分类（xlsx 阈值）。"""
    if rate >= 80:
        return "专用件"
    if rate >= 50:
        return "半专用件"
    return "通用件"


def _judge_key_date(event_date, po_date) -> str:
    """关键日期判断：事件Date vs 库存形成PO Date。"""
    if not event_date or not po_date:
        return "无法判断（缺事件日期或库存形成PO日期）"
    e = _parse_date(event_date)
    p = _parse_date(po_date)
    if e is None or p is None:
        return "无法判断（日期解析失败）"
    if e > p:
        return "事后需求变化（事件发生在PO之后，归因于事件本身）"
    if e < p:
        return "已知变化仍采购（事件发生在PO之前，归因于计划/采购执行问题）"
    return "事件与PO同日（需人工确认）"


def _parse_date(s):
    s = str(s).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s).replace(tzinfo=None)
    except (ValueError, TypeError):
        return None
