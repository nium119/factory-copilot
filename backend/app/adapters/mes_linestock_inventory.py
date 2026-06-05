"""LineStockInventory MES 适配器 — 线边库存映射到 MES LineStock API。

核心映射逻辑
═══════════════════════════════════════════════════════════════════════════
MES 线边库存：

  库存查询 — LineStock/Stock
    - 线边仓的物料库存明细，含批次、库位、数量、状态
    - API: GET /MESApi/LineStock/Stock/getStockPages (分页)
           GET /MESApi/LineStock/Stock/getInOutStockPages (出入明细)

  关键状态 — MesStockStatus
    - -1: 未上料（线边仓有库存，工位未加载）
    - 0:  未下料（已加载到工位）
    - 1:  已下料（已从工位移除）

本体 LineStockInventory 概念只有 query 操作，Agent 需要查询线边库存剩余情况。
═══════════════════════════════════════════════════════════════════════════
"""

from app.adapters.base import ConceptAdapter


class LineStockInventoryMESAdapter(ConceptAdapter):
    """MES 线边库存适配器。

    设计要点
    ────────
    1. 本体 id → MES barcode/qrCode: 库存唯一标识是物料标签条码
    2. 库存按 MaterialCode + BatchNo + QrCode + Position 四维定位
    3. MesStockStatus 告知 Agent 物料当前在库房还是工位
    """

    def __init__(self, concept_name: str):
        super().__init__(concept_name)

    # ── 字段映射 ────────────────────────────────────────────
    # 左侧 = 本体属性名（LineStockInventory 概念定义在 manufacturing.onto.yaml）
    # 右侧 = MES API 请求参数字段名
    #
    # 映射说明:
    #   materialId → materialCode : 物料编码
    #   qty        → qty          : 库存数量
    #   batchNo    → batchNo      : 批次号
    #   positionId → positionCode : 库位编码
    #   warehouseId→ warehouseCode: 仓库编码

    _FIELD_MAP = {
        "id": "id",
        "materialId": "materialCode",
        "qty": "qty",
        "batchNo": "batchNo",
        "positionId": "positionCode",
        "warehouseId": "warehouseCode",
    }

    # ── Action → MES 端点映射 ──────────────────────────────
    # query → GET /MESApi/LineStock/Stock/getStockPages : 分页查询线边库存

    _ACTION_PATHS = {
        "query": ("/MESApi/LineStock/Stock/getStockPages", "GET"),
    }

    # ── 辅助方法 ──────────────────────────────────────────────

    def _translate_fields(self, data: dict) -> dict:
        """将本体字段名翻译为 MES API 字段名。"""
        result = {}
        for ont_name, value in data.items():
            target = self._FIELD_MAP.get(ont_name, ont_name)
            result[target] = value
        return result

    # ── 接口实现 ─────────────────────────────────────────────

    def build_request(self, action: str, args: dict) -> dict:
        """构建 MES 线边库存 API 请求。

        getStockPages 接受 PageParm，可按 materialCode, plantCode, positionCode 筛选。
        """
        ep = self._ACTION_PATHS.get(action)
        if not ep:
            ep = ("/MESApi/LineStock/Stock/getStockPages", "GET")

        path, method = ep
        body = self._translate_fields(args)
        return {"path": path, "method": method, "body": body}

    def parse_response(self, action: str, data: dict) -> dict:
        """解析 MES 线边库存 API 响应。

        Stock 返回格式: {rows: [{id, materialCode, materialName, qty,
          batchNo, barcode, positionName, mesStockStatus, ...}]}
        """
        # 情况1: 分页格式
        if data.get("rows"):
            rows = data["rows"]
            items = [{
                "id": r.get("barcode") or r.get("id", ""),
                "materialId": r.get("materialCode", ""),
                "name": r.get("materialName", ""),
                "qty": r.get("qty", 0),
                "batchNo": r.get("batchNo", ""),
                "position": r.get("positionName", ""),
                # MesStockStatus: -1=未上料, 0=未下料, 1=已下料
                "status": {-1: "未上料", 0: "未下料", 1: "已下料"}.get(
                    r.get("mesStockStatus"), "未知"),
            } for r in rows]
            return {"success": True, "text": f"返回 {len(items)} 条库存记录", "entityId": None}

        # 情况2: 数组
        if isinstance(data, list):
            items = [{
                "id": item.get("barcode") or item.get("id", ""),
                "materialId": item.get("materialCode", ""),
                "name": item.get("materialName", ""),
                "qty": item.get("qty", 0),
            } for item in data]
            return {"success": True, "text": f"返回 {len(items)} 条库存记录", "entityId": None}

        # 情况3: 错误
        if "error" in data or data.get("success") is False:
            msg = data.get("error") or data.get("message", "操作失败")
            return {"success": False, "text": str(msg), "entityId": None}

        return {
            "success": True,
            "text": "操作完成",
            "entityId": str(data.get("barcode") or data.get("id", "")),
        }
