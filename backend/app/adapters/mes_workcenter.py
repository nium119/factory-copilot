"""WorkCenter MES 适配器 — 工作中心映射到 MES 基础数据 API。

核心映射逻辑
═══════════════════════════════════════════════════════════════════════════
MES 工作中心管理：

  基础数据 — MES_WorkCenter 表
    - 工作中心是产线上的一组工位集合，用于排产和产能管理
    - 字段: code, name, workShopName, productLineName, isActive
    - API: GET /MESApi/Basic/WorkCenter/getPages, getActivePhysicalWorkCenters

  MPS 视角 — MPS LinePlan
    - 排产模块也提供工作中心列表（用于排产分配）
    - API: GET /MESApi/MPS/LinePlan/workcenter

本体 WorkCenter 概念只有 query 操作，Agent 需要工作中心进行排产和任务分配。
═══════════════════════════════════════════════════════════════════════════
"""

from app.adapters.base import ConceptAdapter


class WorkCenterMESAdapter(ConceptAdapter):
    """MES 工作中心适配器。

    设计要点
    ────────
    1. 本体 id → MES code: 工作中心编号
    2. 本体 name → MES name: 工作中心名称
    3. MES 工作中心关联到车间(WorkShop)和产线(ProductLine)
    """

    def __init__(self, concept_name: str):
        super().__init__(concept_name)

    # ── 字段映射 ────────────────────────────────────────────
    # 左侧 = 本体属性名（WorkCenter 概念定义在 manufacturing.onto.yaml）
    # 右侧 = MES API 请求参数字段名
    #
    # 映射说明:
    #   id   → code          : 工作中心编号，本体用 id，MES 用 code
    #   name → name          : 工作中心名称，两边一致
    #   注意: MES 工作中心还有 workShopName, productLineName 等关联字段

    _FIELD_MAP = {
        "id": "code",
        "name": "name",
    }

    # ── Action → MES 端点映射 ──────────────────────────────
    # query → GET /MESApi/Basic/WorkCenter/getPages : 分页查询工作中心

    _ACTION_PATHS = {
        "query": ("/MESApi/Basic/WorkCenter/getPages", "GET"),
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
        """构建 MES 工作中心 API 请求。"""
        ep = self._ACTION_PATHS.get(action)
        if not ep:
            ep = ("/MESApi/Basic/WorkCenter/getPages", "GET")

        path, method = ep
        body = self._translate_fields(args)
        return {"path": path, "method": method, "body": body}

    def parse_response(self, action: str, data: dict) -> dict:
        """解析 MES 工作中心 API 响应。"""
        if data.get("rows"):
            rows = data["rows"]
            items = [{
                "id": r.get("code", ""),
                "name": r.get("name", ""),
                "workshop": r.get("workShopName", ""),
                "line": r.get("productLineName", ""),
                "isActive": r.get("isActive", False),
            } for r in rows]
            return {"success": True, "text": f"返回 {len(items)} 个工作中心", "entityId": None}

        if isinstance(data, list):
            items = [{
                "id": item.get("code", ""),
                "name": item.get("name", ""),
            } for item in data]
            return {"success": True, "text": f"返回 {len(items)} 个工作中心", "entityId": None}

        if "error" in data or data.get("success") is False:
            msg = data.get("error") or data.get("message", "操作失败")
            return {"success": False, "text": str(msg), "entityId": None}

        return {
            "success": True,
            "text": "操作完成",
            "entityId": str(data.get("code", "")),
        }
