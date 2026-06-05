"""Mould MES 适配器 — 模具管理映射到 MES Basic/Mould API。

核心映射逻辑
═══════════════════════════════════════════════════════════════════════════
MES 模具管理：
  - 模具台账（Basic/Mould）
  - 工位执行校验（WorkOrderExecute/CheckMouldCode）
  - 模具状态确认（WorkOrderExecute/StatusMouldConfirm）

本体 Mould 有三个 action：
  - query:       查询模具台账
  - assign:      领用模具 → 更新状态为"使用中"，绑定到设备
  - returnMould: 归还模具 → 更新状态为"封存"

MES 模具管理核心 API：
  - GET  /MESApi/Basic/Mould/getPages        : 分页查询模具
  - GET  /MESApi/Basic/Mould/getActiveMoulds  : 查询活跃模具
  - POST /MESApi/Basic/Mould/save             : 新建/编辑模具
  - POST /MESApi/Basic/Mould/delete           : 删除模具
  - POST /MESApi/WorkOrderExecute/CheckMouldCode     : 开工前校验模具编码
  - POST /MESApi/WorkOrderExecute/StatusMouldConfirm : 确认模具状态变更

assign 操作映射到 save（更新 status + equipmentId），returnMould 同理。
═══════════════════════════════════════════════════════════════════════════
"""

from app.adapters.base import ConceptAdapter


class MouldMESAdapter(ConceptAdapter):
    """MES 模具适配器 — 模具语义到 MES Basic/Mould API 的翻译。

    设计要点
    ────────
    1. query → Basic/Mould/getPages（分页查询模具台账）
    2. assign 需要一个 equipmentId → save 更新 status=使用中 + 绑定设备
    3. returnMould → save 更新 status=封存
    4. MES 模具字段: mouldCode, mouldName, status, equipmentCode, lifeCount, maxLife
    """

    def __init__(self, concept_name: str):
        super().__init__(concept_name)

    # ── 字段映射 ────────────────────────────────────────────
    # 左侧 = 本体属性名（Mould 概念定义在 manufacturing.onto.yaml）
    # 右侧 = MES API 请求参数字段名
    #
    # 映射说明:
    #   id/name/code → mouldCode  : 模具编码（本体有多个标识字段）
    #   name/label   → mouldName  : 模具名称
    #   status       → status     : 状态
    #   equipmentId  → equipmentCode : 所属设备
    #   lifeCount    → usedCount  : 已使用次数
    #   maxLife      → maxCount   : 最大寿命次数

    _FIELD_MAP = {
        "id": "mouldCode",
        "code": "mouldCode",
        "name": "mouldName",
        "label": "mouldName",
        "status": "status",
        "equipmentId": "equipmentCode",
        "lifeCount": "usedCount",
        "maxLife": "maxCount",
    }

    # ── Action → MES 端点映射 ──────────────────────────────
    # query       → GET  /MESApi/Basic/Mould/getPages
    # assign      → POST /MESApi/Basic/Mould/save（更新状态+绑定设备）
    # returnMould → POST /MESApi/Basic/Mould/save（更新状态为封存）

    _ACTION_PATHS = {
        "query":       ("/MESApi/Basic/Mould/getPages", "GET"),
        "assign":      ("/MESApi/Basic/Mould/save", "POST"),
        "returnMould": ("/MESApi/Basic/Mould/save", "POST"),
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
        """构建 MES 模具 API 请求。

        assign → 构造 save 请求体: {mouldCode, mouldName, status="使用中", equipmentCode}
        returnMould → 构造 save 请求体: {mouldCode, status="封存"}
        query → 构造 getPages 查询参数
        """
        ep = self._ACTION_PATHS.get(action)
        if not ep:
            ep = ("/MESApi/Basic/Mould/getPages", "GET")

        path, method = ep
        body = self._translate_fields(args)

        if action == "assign":
            # 领用模具: 更新状态为使用中 + 绑定设备
            entity_id = (
                args.get("id", "")
                or args.get("code", "")
                or args.get("name", "")
            )
            equipment_id = args.get("equipmentId", "") or body.get("equipmentCode", "")
            body = {
                "mouldCode": entity_id or body.get("mouldCode", ""),
                "status": "使用中",
            }
            if equipment_id:
                body["equipmentCode"] = equipment_id

        elif action == "returnMould":
            # 归还模具: 更新状态为封存
            entity_id = (
                args.get("id", "")
                or args.get("code", "")
                or args.get("name", "")
            )
            body = {
                "mouldCode": entity_id or body.get("mouldCode", ""),
                "status": "封存",
            }

        return {"path": path, "method": method, "body": body}

    def parse_response(self, action: str, data: dict) -> dict:
        """解析 MES 模具 API 响应。

        getPages 返回: {rows: [{mouldCode, mouldName, status, equipmentCode, ...}]}
        save 返回:    {success: true, mouldCode: "..."}
        """
        # 情况1: 分页查询
        if data.get("rows"):
            rows = data["rows"]
            items = [{
                "id": r.get("mouldCode", ""),
                "name": r.get("mouldName", ""),
                "code": r.get("mouldCode", ""),
                "status": r.get("status", ""),
                "equipmentId": r.get("equipmentCode", ""),
                "lifeCount": r.get("usedCount", 0),
                "maxLife": r.get("maxCount", 0),
            } for r in rows]
            return {"success": True, "text": f"返回 {len(items)} 个模具", "entityId": None}

        # 情况2: 数组
        if isinstance(data, list):
            items = [{
                "id": item.get("mouldCode", ""),
                "name": item.get("mouldName", ""),
                "status": item.get("status", ""),
            } for item in data]
            return {"success": True, "text": f"返回 {len(items)} 个模具", "entityId": None}

        # 情况3: 错误
        if "error" in data or data.get("success") is False:
            msg = data.get("error") or data.get("message", "操作失败")
            return {"success": False, "text": str(msg), "entityId": None}

        # 情况4: assign/returnMould 操作结果
        entity_id = data.get("mouldCode", "")
        labels = {
            "assign": f"模具 {entity_id} 已领用",
            "returnMould": f"模具 {entity_id} 已归还",
        }

        return {
            "success": True,
            "text": labels.get(action, "操作完成"),
            "entityId": str(entity_id),
        }
