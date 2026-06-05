"""Tooling MES 适配器 — 工装夹具管理映射到 MES Basic/Tooling API。

核心映射逻辑
═══════════════════════════════════════════════════════════════════════════
MES 工装管理：
  - 工装台账（Basic/Tooling）
  - 工位执行校验（WorkOrderExecute/CheckToolingCode）
  - 工装状态确认（WorkOrderExecute/StatusToolingConfirm）

本体 Tooling 有三个 action：
  - query:         查询工装台账
  - assign:        领用工装 → 更新状态为"使用中"，绑定到设备
  - returnTooling: 归还工装 → 更新状态为"封存"

MES 工装管理核心 API：
  - GET  /MESApi/Basic/Tooling/getPages       : 分页查询工装
  - GET  /MESApi/Basic/Tooling/getActiveToolings : 查询活跃工装
  - POST /MESApi/Basic/Tooling/save            : 新建/编辑工装
  - POST /MESApi/Basic/Tooling/delete          : 删除工装
  - POST /MESApi/WorkOrderExecute/CheckToolingCode    : 开工前校验工装编码
  - POST /MESApi/WorkOrderExecute/StatusToolingConfirm : 确认工装状态变更
  - GET  /MESApi/Preparation/getToolingStation : 查询工装在工位绑定

Tooling 与 Mould 结构高度对称，assign/returnTooling 映射到 save（状态变更）。
═══════════════════════════════════════════════════════════════════════════
"""

from app.adapters.base import ConceptAdapter


class ToolingMESAdapter(ConceptAdapter):
    """MES 工装适配器 — 工装语义到 MES Basic/Tooling API 的翻译。

    设计要点
    ────────
    1. query → Basic/Tooling/getPages（分页查询工装台账）
    2. assign 需要一个 equipmentId → save 更新 status=使用中 + 绑定设备
    3. returnTooling → save 更新 status=封存
    4. MES 工装字段: toolingCode, toolingName, toolingType, status, equipmentCode
    """

    def __init__(self, concept_name: str):
        super().__init__(concept_name)

    # ── 字段映射 ────────────────────────────────────────────
    # 左侧 = 本体属性名（Tooling 概念定义在 manufacturing.onto.yaml）
    # 右侧 = MES API 请求参数字段名
    #
    # 映射说明:
    #   id/name/code → toolingCode : 工装编码
    #   name/label   → toolingName : 工装名称
    #   type         → toolingType : 工装类型（刀具/夹具/量具/辅具）
    #   status       → status      : 状态
    #   equipmentId  → equipmentCode : 所属设备

    _FIELD_MAP = {
        "id": "toolingCode",
        "code": "toolingCode",
        "name": "toolingName",
        "label": "toolingName",
        "type": "toolingType",
        "status": "status",
        "equipmentId": "equipmentCode",
    }

    # ── Action → MES 端点映射 ──────────────────────────────
    # query          → GET /MESApi/WorkOrderExecute/RecordTool（MES 无独立工装主数据，用工位工装记录查询）
    # assign         → POST /MESApi/Preparation/saveToolingStation（工装领用到工位）
    # returnTooling  → POST /MESApi/Preparation/saveToolingStation（工装归还，更新状态）

    _ACTION_PATHS = {
        "query":          ("/MESApi/WorkOrderExecute/RecordTool", "GET"),
        "assign":         ("/MESApi/Preparation/saveToolingStation", "POST"),
        "returnTooling":  ("/MESApi/Preparation/saveToolingStation", "POST"),
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
        """构建 MES 工装 API 请求。

        assign → 构造 save 请求体: {toolingCode, toolingName, status="使用中", equipmentCode}
        returnTooling → 构造 save 请求体: {toolingCode, status="封存"}
        query → 构造 getPages 查询参数
        """
        ep = self._ACTION_PATHS.get(action)
        if not ep:
            ep = ("/MESApi/Basic/Tooling/getPages", "GET")

        path, method = ep
        body = self._translate_fields(args)

        if action == "assign":
            # 领用工装: 更新状态为使用中 + 绑定设备
            entity_id = (
                args.get("id", "")
                or args.get("code", "")
                or args.get("name", "")
            )
            equipment_id = args.get("equipmentId", "") or body.get("equipmentCode", "")
            body = {
                "toolingCode": entity_id or body.get("toolingCode", ""),
                "status": "使用中",
            }
            if equipment_id:
                body["equipmentCode"] = equipment_id

        elif action == "returnTooling":
            # 归还工装: 更新状态为封存
            entity_id = (
                args.get("id", "")
                or args.get("code", "")
                or args.get("name", "")
            )
            body = {
                "toolingCode": entity_id or body.get("toolingCode", ""),
                "status": "封存",
            }

        return {"path": path, "method": method, "body": body}

    def parse_response(self, action: str, data: dict) -> dict:
        """解析 MES 工装 API 响应。

        getPages 返回: {rows: [{toolingCode, toolingName, toolingType, status, ...}]}
        save 返回:     {success: true, toolingCode: "..."}
        """
        # 情况1: 分页查询
        if data.get("rows"):
            rows = data["rows"]
            items = [{
                "id": r.get("toolingCode", ""),
                "name": r.get("toolingName", ""),
                "code": r.get("toolingCode", ""),
                "type": r.get("toolingType", ""),
                "status": r.get("status", ""),
                "equipmentId": r.get("equipmentCode", ""),
            } for r in rows]
            return {"success": True, "text": f"返回 {len(items)} 个工装", "entityId": None}

        # 情况2: 数组
        if isinstance(data, list):
            items = [{
                "id": item.get("toolingCode", ""),
                "name": item.get("toolingName", ""),
                "type": item.get("toolingType", ""),
                "status": item.get("status", ""),
            } for item in data]
            return {"success": True, "text": f"返回 {len(items)} 个工装", "entityId": None}

        # 情况3: 错误
        if "error" in data or data.get("success") is False:
            msg = data.get("error") or data.get("message", "操作失败")
            return {"success": False, "text": str(msg), "entityId": None}

        # 情况4: assign/returnTooling 操作结果
        entity_id = data.get("toolingCode", "")
        labels = {
            "assign": f"工装 {entity_id} 已领用",
            "returnTooling": f"工装 {entity_id} 已归还",
        }

        return {
            "success": True,
            "text": labels.get(action, "操作完成"),
            "entityId": str(entity_id),
        }
