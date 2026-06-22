"""Material 适配器 — 物料主数据通过 ThreeApi 直连 MDM。

核心映射逻辑
═══════════════════════════════════════════════════════════════════════════
物料主数据由 MDM 统一管理，MES 只是消费方。
本适配器通过 ThreeApi 代理直连 MDM，不依赖 MES 内的物料副本。

  ThreeApi 主数据代理
    - MDM 主数据统一入口
    - API: POST /ThreeApi/getMaterialDataView

Material 是 Agent 查询最频繁的概念之一（有 4 个关系引用它）。
═══════════════════════════════════════════════════════════════════════════
"""

from app.adapters.base import ConceptAdapter


class MaterialMESAdapter(ConceptAdapter):
    """MDM 物料适配器 — 物料语义到 ThreeApi/MDM 的翻译。

    设计要点
    ────────
    1. 本体 id → itemNo: 物料编码
    2. 本体 name → itemName: 物料名称
    3. ThreeApi 统一代理 MDM 主数据，物料不走 MES 中转
    """

    def __init__(self, concept_name: str):
        super().__init__(concept_name)

    # ── 字段映射 ────────────────────────────────────────────
    # 左侧 = 本体属性名（Material 概念定义在 manufacturing.onto.yaml）
    # 右侧 = MES API 请求参数字段名
    #
    # 映射说明:
    #   id        → itemNo        : 物料编码，MES 用 ItemNo（物料号）
    #   name      → itemName      : 物料名称，MES 用 ItemName
    #   unit      → unitName      : 单位
    #   stock     → stockQty      : 库存数量（MES 中来自 StockQty）
    #   spec      → spec          : 规格，两边一致
    #   plantCode → plantCode     : 工厂代码，两边一致

    _FIELD_MAP = {
        "id": "itemNo",
        "name": "itemName",
        "unit": "unitName",
        "stock": "stockQty",
        "spec": "spec",
        "plantCode": "plantCode",
        "materialType": "type",
        "materialName": "name",
    }

    # ── Action → MDM 端点映射 ──────────────────────────────
    # query → POST /ThreeApi/getMaterialDataView : MDM 主数据代理

    _ACTION_PATHS = {
        "query": ("/ThreeApi/getMaterialDataView", "POST"),
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
        """构建 MDM 物料 API 请求（通过 ThreeApi 代理）。

        ThreeApi 接受 POST JSON 查询体:
          {keyword, plantCode, page, pageSize, ...}
        """
        ep = self._ACTION_PATHS.get(action)
        if not ep:
            ep = ("/ThreeApi/getMaterialDataView", "POST")

        path, method = ep
        body = self._translate_fields(args)
        return {"path": path, "method": method, "body": body}

    def parse_response(self, action: str, data: dict) -> dict:
        """解析 ThreeApi/MDM 物料响应。

        ThreeApi 统一返回格式:
          {success: true, data: {rows: [...], total: N}}
        rows 中每条记录含 itemNo, itemName, itemSpec, unitName, stockQty 等。
        """
        # ThreeApi 外层解包
        inner = data.get("data", data)

        # 情况1: 分页格式
        if inner.get("rows"):
            rows = inner["rows"]
            items = [{
                "id": r.get("itemNo", ""),
                "name": r.get("itemName", ""),
                "spec": r.get("itemSpec", ""),
                "unit": r.get("unitName", ""),
                "stock": r.get("stockQty", 0),
            } for r in rows]
            return {"success": True, "text": f"返回 {len(items)} 种物料", "entityId": None}

        # 情况2: 数组
        if isinstance(inner, list):
            items = [{
                "id": item.get("itemNo", ""),
                "name": item.get("itemName", ""),
                "spec": item.get("itemSpec", ""),
            } for item in inner]
            return {"success": True, "text": f"返回 {len(items)} 种物料", "entityId": None}

        # 情况3: 错误
        if "error" in data or data.get("success") is False:
            msg = data.get("error") or data.get("message", "操作失败")
            return {"success": False, "text": str(msg), "entityId": None}

        # 情况4: 单条记录
        return {
            "success": True,
            "text": f"物料: {inner.get('itemName', inner.get('itemNo', ''))}",
            "entityId": str(inner.get("itemNo", "")),
        }
