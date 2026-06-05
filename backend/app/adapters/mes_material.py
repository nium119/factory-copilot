"""Material MES 适配器 — 物料信息映射到 MES 物料扩展 API。

核心映射逻辑
═══════════════════════════════════════════════════════════════════════════
MES 物料信息来源：

  物料扩展 — MaterialExtend
    - 生产物料的主数据，包含物料编码、名称、规格、单位等
    - API: GET /MESApi/MaterialExtend/getPages (分页), GET /MESApi/MaterialExtend/getInfo (详情)

  MPS 物料 — MPS/Material
    - 排产模块的物料列表
    - API: GET /MESApi/MPS/Material/getmateriallist

  ThreeApi 主数据 — 外部系统物料数据
    - MDM 主数据通过 ThreeApi 代理
    - API: POST /ThreeApi/getMaterialDataView

本适配器优先使用 MaterialExtend 端点，因为它是 MES 内部的标准物料主数据。
Material 是 Agent 查询最频繁的概念之一（有 4 个关系引用它）。
═══════════════════════════════════════════════════════════════════════════
"""

from app.adapters.base import ConceptAdapter


class MaterialMESAdapter(ConceptAdapter):
    """MES 物料适配器 — 物料语义到 MES MaterialExtend API 的翻译。

    设计要点
    ────────
    1. 本体 id → MES itemNo: 物料编码，本体用 id，MES 用 ItemNo（物料号）
    2. 本体 name → MES itemName: 物料名称
    3. MES MaterialExtend 是 MES_MaterialExtend 表的扩展属性视图，
       包含了物料基础信息 + MES 特有的扩展字段
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
    }

    # ── Action → MES 端点映射 ──────────────────────────────
    # query → GET /MESApi/MaterialExtend/getPages : 分页查询物料

    _ACTION_PATHS = {
        "query": ("/MESApi/MaterialExtend/getPages", "GET"),
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
        """构建 MES 物料 API 请求。

        getPages 接受 PageParm 查询参数 {page, pageSize, where, order}，
        where 可包含 itemNo(keyword), plantCode 等筛选条件。
        """
        ep = self._ACTION_PATHS.get(action)
        if not ep:
            ep = ("/MESApi/MaterialExtend/getPages", "GET")

        path, method = ep
        body = self._translate_fields(args)
        return {"path": path, "method": method, "body": body}

    def parse_response(self, action: str, data: dict) -> dict:
        """解析 MES 物料 API 响应。

        MaterialExtend 返回格式:
          - getPages: {rows: [{itemNo, itemName, itemSpec, unitName, stockQty, ...}], total: N}
          - getInfo: 单条记录
          - list: 数组
        """
        # 情况1: 分页格式
        if data.get("rows"):
            rows = data["rows"]
            items = [{
                "id": r.get("itemNo", ""),
                "name": r.get("itemName", ""),
                "spec": r.get("itemSpec", ""),
                "unit": r.get("unitName", ""),
                "stock": r.get("stockQty", 0),
            } for r in rows]
            return {"success": True, "text": f"返回 {len(items)} 种物料", "entityId": None}

        # 情况2: 数组
        if isinstance(data, list):
            items = [{
                "id": item.get("itemNo", ""),
                "name": item.get("itemName", ""),
                "spec": item.get("itemSpec", ""),
            } for item in data]
            return {"success": True, "text": f"返回 {len(items)} 种物料", "entityId": None}

        # 情况3: 错误
        if "error" in data or data.get("success") is False:
            msg = data.get("error") or data.get("message", "操作失败")
            return {"success": False, "text": str(msg), "entityId": None}

        # 情况4: 单条记录
        return {
            "success": True,
            "text": f"物料: {data.get('itemName', data.get('itemNo', ''))}",
            "entityId": str(data.get("itemNo", "")),
        }
