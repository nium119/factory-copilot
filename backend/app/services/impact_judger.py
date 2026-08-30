"""影响判定器：影响分析链完成后，确定性计算「专用化率 + 日期归因」。

设计原则：
- 确定性：程序判定（聚合/占比/日期比较/打标签），不依赖 LLM。
- 本体驱动：概念名、关系、字段名尽量从本体动态读取，不写死字面量；
  只有「订单分录 → 物料 → 订单 → 客户」这一业务计算模式是固定的。
- 数据从 Neo4j 只读查询获取（英文属性名，可靠），不依赖查询结果的中文列头。

判定项（对齐需求 Excel 的「影响判定」列）：
1. 专用化率 = 该客户需求 ÷ 物料总需求 → 专用件(≥80%) / 半专用件(50~80%) / 通用件(<50%)
2. 日期归因 = 事件日期 vs 库存形成PO日期 → 事后需求变化 / 已知需求下降仍采购
"""

import re
from typing import Any, Optional

from loguru import logger

# 专用化率阈值（需求 Excel 建议阈值，可配置）
SPEC_HIGH = 0.8
SPEC_LOW = 0.5


# ── 基础工具 ──────────────────────────────────────────────────


def _to_number(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_date(v: Any) -> str:
    """日期值归一化为 YYYY-MM-DD（Neo4j datetime 可能带 T 和时分秒）。"""
    s = str(v or "").strip()
    if not s:
        return ""
    return s[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", s) else s


def classify_specialization(rate: Optional[float]) -> str:
    if rate is None:
        return "无法判定"
    if rate >= SPEC_HIGH:
        return "专用件"
    if rate >= SPEC_LOW:
        return "半专用件"
    return "通用件"


# ── 需求聚合（字段匹配，口径一致，不依赖关系边）──────────────


async def _query_metrics(
    material_id: Any, customer_no: str, ns: str,
) -> dict:
    """查物料的影响分析指标（字段匹配口径一致，逐步聚合避免笛卡尔积）。

    返回 {totalDemand, customerDemand, totalStock, poQty, latestPrice}。
    """
    from app.services.neo4j_service import neo4j_service

    cypher = (
        "MATCH (m:MdmMaterial {_namespace: $ns, id: $mid}) "
        "OPTIONAL MATCH (e:WmsSaleOrderEntry {_namespace: $ns, materialId: m.id}) "
        "OPTIONAL MATCH (so:WmsSaleOrder {_namespace: $ns, id: e.saleOrderId}) "
        "WITH m, coalesce(sum(e.qty), 0) AS totalDemand, "
        "coalesce(sum(CASE WHEN so.customerNo = $cust THEN e.qty ELSE 0 END), 0) AS customerDemand "
        "OPTIONAL MATCH (inv:Inventory {_namespace: $ns, materialId: m.id}) "
        "WITH m, totalDemand, customerDemand, coalesce(sum(inv.qty), 0) AS totalStock "
        "OPTIONAL MATCH (po:PurchaseOrderEntry {_namespace: $ns, itemCode: m.itemNo}) "
        "RETURN totalDemand, customerDemand, totalStock, "
        "coalesce(sum(po.qty), 0) AS poQty, m.latestPrice AS latestPrice"
    )
    params: dict = {"mid": str(material_id), "cust": str(customer_no), "ns": ns}
    try:
        rows = await neo4j_service.execute_read(cypher, params)
        if rows:
            return dict(rows[0])
    except Exception as e:
        logger.warning(f"[ImpactJudger] 查询指标失败 {material_id}: {e}")
    return {}


# ── 受影响物料反推 ───────────────────────────────────────────


async def _query_affected_materials(ns: str, so_id: str = "") -> list[dict]:
    """从 Neo4j 反推受影响物料 + 客户 + 事件日期。

    变更日志 → 销售订单 → 销售分录 → 物料，取每个受影响物料的
    客户号与变更日期。这是「订单变更影响」的业务模式。
    """
    from app.services.neo4j_service import neo4j_service

    so_filter = "WHERE cl.soId = $soid" if so_id else ""
    cypher = (
        "MATCH (cl:SoChangeLog {_namespace: $ns}) "
        f"{so_filter} "
        "MATCH (so:WmsSaleOrder {_namespace: $ns, id: cl.soId}) "
        "MATCH (e:WmsSaleOrderEntry {_namespace: $ns, saleOrderId: so.id}) "
        "MATCH (m:MdmMaterial {_namespace: $ns, id: e.materialId}) "
        "RETURN DISTINCT m.id AS materialId, m.itemNo AS itemNo, "
        "so.customerNo AS customerNo, cl.changeDate AS changeDate, cl.changeType AS changeType"
    )
    params: dict = {"ns": ns}
    if so_id:
        params["soid"] = so_id
    try:
        rows = await neo4j_service.execute_read(cypher, params)
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"[ImpactJudger] 查询受影响物料失败: {e}")
        return []


async def _query_bom_chain(material_id: Any, ns: str) -> list[dict]:
    """查物料的 BOM 子件链（BOM头 → BOM分录 → 子件物料），用于典型输出的 BOM 链路呈现。"""
    from app.services.neo4j_service import neo4j_service

    cypher = (
        "MATCH (m:MdmMaterial {_namespace: $ns, id: $mid}) "
        "OPTIONAL MATCH (bom:BaseBom {_namespace: $ns, materialId: m.id}) "
        "OPTIONAL MATCH (entry:BaseBomEntry {_namespace: $ns, bomId: bom.fid}) "
        "OPTIONAL MATCH (child:MdmMaterial {_namespace: $ns, id: entry.materialId}) "
        "RETURN bom.fid AS bomId, entry.fid AS entryId, "
        "child.id AS childId, child.itemNo AS childItemNo, child.itemName AS childName, "
        "entry.qty AS qty ORDER BY bomId, entryId"
    )
    try:
        rows = await neo4j_service.execute_read(cypher, {"mid": str(material_id), "ns": ns})
        return [dict(r) for r in rows if r.get("bomId")]
    except Exception as e:
        logger.warning(f"[ImpactJudger] 查询BOM链失败 {material_id}: {e}")
        return []


async def _query_inventory_po_date(material_id: Any, ns: str) -> str:
    """查某物料的库存形成PO日期（日期归因用）。"""
    from app.services.neo4j_service import neo4j_service

    cypher = (
        "MATCH (inv:Inventory {_namespace: $ns, materialId: $mid}) "
        "RETURN inv.formationPoDate AS poDate LIMIT 1"
    )
    try:
        rows = await neo4j_service.execute_read(cypher, {"mid": str(material_id), "ns": ns})
        if rows:
            return _to_date(rows[0].get("poDate"))
    except Exception as e:
        logger.warning(f"[ImpactJudger] 查询PO日期失败 {material_id}: {e}")
    return ""


# ── 主入口 ──────────────────────────────────────────────────


async def judge_impact(context: dict, steps_taken: list, message: str) -> str:
    """影响判定入口：返回判定结果文本（无判定条件时返回空串）。

    从 Neo4j 反推受影响物料 + 客户 + 事件日期，确定性计算专用化率 + 日期归因。
    """
    from app.services.ontology_service import ontology_service
    ns = ontology_service.active_namespace or ""
    if not ns:
        logger.info("[ImpactJudger] 无活跃 namespace，跳过")
        return ""

    # 从用户消息提取订单/事件编号（如 SO-008），判定只针对该事件，避免混入其他变更
    # 不用 \b（中文是 Unicode word char，\b 在「008取」边界不生效）
    so_id = ""
    _m = re.search(r'([A-Z]{2,}[-_]\d{2,})', message or "")
    if _m:
        so_id = _m.group(1)
    affected = await _query_affected_materials(ns, so_id)
    if not affected:
        logger.info("[ImpactJudger] 未查询到受影响物料（无变更日志/分录/物料链路），跳过")
        return ""

    customer_no = affected[0].get("customerNo")
    lines: list[str] = []

    # ── 专用化率 + 净暴露 + 影响金额 + BOM 链（逐物料）──
    spec_lines = []
    amount_lines = []
    bom_lines = []
    _seen = set()  # 按物料去重（同一物料可能被多个变更事件命中）
    for rec in affected[:20]:
        mid = rec.get("materialId")
        cust_no = rec.get("customerNo")
        if mid is None or cust_no is None:
            continue
        _dedup_key = (mid, cust_no)
        if _dedup_key in _seen:
            continue
        _seen.add(_dedup_key)
        metrics = await _query_metrics(mid, str(cust_no), ns)
        item = rec.get("itemNo") or mid
        if not metrics:
            continue
        total = _to_number(metrics.get("totalDemand"))
        cust = _to_number(metrics.get("customerDemand"))

        # 专用化率
        if total is not None and cust is not None and total > 0:
            rate = cust / total
            cls = classify_specialization(rate)
            spec_lines.append(
                f"- {item}：专用化率 {rate*100:.1f}%（该客户 {cust:.0f} ÷ 总需求 {total:.0f}）→ {cls}"
            )

        # 净暴露 + 影响金额（对齐本体 computed 口径：MAX(采购+库存-需求,0) × 最新价格）
        po_qty = _to_number(metrics.get("poQty")) or 0.0
        stock = _to_number(metrics.get("totalStock")) or 0.0
        price = _to_number(metrics.get("latestPrice"))
        if total is not None:
            net_exposure = max(po_qty + stock - total, 0.0)
            if price is not None:
                amount = net_exposure * price
                amount_lines.append(
                    f"- {item}：净暴露 {net_exposure:.0f} × 价格 {price:.0f} = 影响金额 {amount:.0f}"
                )

        # BOM 子件链（典型输出：影响链路的 BOM 呈现）
        bom_chain = await _query_bom_chain(mid, ns)
        for bc in bom_chain:
            child_item = bc.get("childItemNo") or bc.get("childId") or "?"
            child_name = bc.get("childName") or ""
            qty = bc.get("qty")
            qty_s = f"，用量 {qty}" if qty is not None else ""
            bom_lines.append(
                f"- {item} → BOM {bc.get('bomId')} → {child_item}（{child_name}）{qty_s}"
            )

    if spec_lines:
        lines.append("专用化率判定：")
        lines.extend(spec_lines)
    if amount_lines:
        lines.append("影响金额判定：")
        lines.extend(amount_lines)
    if bom_lines:
        lines.append("BOM 子件链：")
        lines.extend(bom_lines)

    # ── 日期归因（事件日期 vs 库存形成PO日期）──
    event_date = _to_date(affected[0].get("changeDate")) if affected else ""
    if event_date and affected:
        for rec in affected:
            po_date = await _query_inventory_po_date(rec.get("materialId"), ns)
            if po_date:
                if event_date > po_date:
                    attr = "事后需求变化"
                    op = ">"
                elif event_date < po_date:
                    attr = "已知需求下降仍采购"
                    op = "<"
                else:
                    attr = "同日（需人工确认）"
                    op = "="
                lines.append(
                    f"- 日期归因：事件日期 {event_date} {op} 库存形成PO日期 {po_date} → {attr}"
                )
                break
        else:
            logger.info("[ImpactJudger] 库存无 formationPoDate 数据，日期归因跳过")

    if not lines:
        return ""
    return "\n".join(lines)
