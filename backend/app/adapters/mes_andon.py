"""AndonEvent MES 适配器 — 安灯异常呼叫与逐级上报。

核心映射逻辑
═══════════════════════════════════════════════════════════════════════════
MES 安灯有独立的 API 网关 AndonWebApi：
  - 安灯呼叫创建：操作工发现问题时触发，按类型分类（物料/设备/质量/工艺）
  - 逐级上报：安灯未及时响应时自动升级（线长→经理→总监→副总）
  - 状态追踪：待响应→处理中→已升级→已关闭
  - KPI 监控：响应时长、解决时长、类型分布

本体 AndonEvent 有 4 个 action:
  create  → 创建安灯报警
  query   → 查询活跃/历史安灯
  escalate → 升级安灯到上级
  resolve → 关闭安灯
═══════════════════════════════════════════════════════════════════════════
"""

from app.adapters.base import ConceptAdapter


class AndonMESAdapter(ConceptAdapter):
    """MES 安灯适配器 — AndonWebApi 翻译。

    设计要点
    ────────
    1. create → AndonWebApi/api/andon/create : 操作工触发安灯
    2. query  → AndonWebApi/api/andon/active : 查询活跃安灯（默认）
              → AndonWebApi/api/andon/history : 查询历史记录
    3. escalate → AndonWebApi/api/andon/escalate : 升级到上级
    4. resolve → AndonWebApi/api/andon/resolve : 关闭安灯
    """

    def __init__(self, concept_name: str):
        super().__init__(concept_name)

    # ── 字段映射 ────────────────────────────────────────────
    _FIELD_MAP = {
        "id": "andonId",
        "type": "andonType",
        "status": "status",
        "level": "level",
        "line": "line",
        "description": "description",
        "responder": "responder",
        "createdAt": "createdAt",
        "resolvedAt": "resolvedAt",
        "remarks": "remarks",
    }

    # ── Action → 端点映射 ──────────────────────────────
    _ACTION_PATHS = {
        "create":   ("/AndonWebApi/api/andon/create", "POST"),
        "query":    ("/AndonWebApi/api/andon/active", "GET"),
        "escalate": ("/AndonWebApi/api/andon/escalate", "POST"),
        "resolve":  ("/AndonWebApi/api/andon/resolve", "POST"),
    }

    # ── 辅助方法 ──────────────────────────────────────────────

    def _translate_fields(self, data: dict) -> dict:
        result = {}
        for ont_name, value in data.items():
            target = self._FIELD_MAP.get(ont_name, ont_name)
            result[target] = value
        return result

    # ── 接口实现 ─────────────────────────────────────────────

    def build_request(self, action: str, args: dict) -> dict:
        ep = self._ACTION_PATHS.get(action)
        if not ep:
            ep = ("/AndonWebApi/api/andon/active", "GET")

        path, method = ep
        entity_id = args.pop("id", "") or args.pop("andonId", "")
        body = self._translate_fields(args)

        if action == "create":
            # 创建安灯: andonType + description + line
            pass  # body 已由 _translate_fields 处理
        elif action == "query":
            # 查询: 状态/产线过滤作为 GET 参数
            status = args.pop("status", "") or body.pop("status", "")
            line = args.pop("line", "") or body.pop("line", "")
            if status:
                body["status"] = status
            if line:
                body["line"] = line
        elif action == "escalate":
            # 升级: andonId + targetLevel
            level = args.pop("level", "") or body.pop("level", "")
            if entity_id:
                body["andonId"] = entity_id
            if level:
                body["targetLevel"] = level
        elif action == "resolve":
            # 关闭: andonId + remarks
            if entity_id:
                body["andonId"] = entity_id

        return {"path": path, "method": method, "body": body}

    def parse_response(self, action: str, data: dict) -> dict:
        # 列表响应
        if isinstance(data, list):
            items = [{
                "id": item.get("andonId", ""),
                "type": item.get("andonType", ""),
                "status": item.get("status", ""),
                "level": item.get("level", ""),
                "line": item.get("line", ""),
                "description": item.get("description", ""),
            } for item in data]
            return {"success": True, "text": f"返回 {len(items)} 条安灯事件", "entityId": None}

        # 分页响应
        if data.get("rows"):
            rows = data["rows"]
            items = [{
                "id": r.get("andonId", ""),
                "type": r.get("andonType", ""),
                "status": r.get("status", ""),
                "level": r.get("level", ""),
                "line": r.get("line", ""),
                "description": r.get("description", ""),
            } for r in rows]
            return {"success": True, "text": f"返回 {len(items)} 条安灯事件", "entityId": None}

        # 错误
        if "error" in data or data.get("success") is False:
            msg = data.get("error") or data.get("message", "操作失败")
            return {"success": False, "text": str(msg), "entityId": None}

        # 单条操作结果
        andon_id = data.get("andonId", "")
        labels = {
            "create": f"安灯 {andon_id} 已创建",
            "escalate": f"安灯 {andon_id} 已升级",
            "resolve": f"安灯 {andon_id} 已关闭",
        }
        return {
            "success": True,
            "text": labels.get(action, "操作完成"),
            "entityId": str(andon_id),
        }
