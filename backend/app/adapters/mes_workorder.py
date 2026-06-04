"""WorkOrder MES 适配器 — 制造业 MES 工单 API 集成示例。"""

from app.adapters.base import ConceptAdapter


# 外部 API 枚举映射 — 状态值翻译的唯一数据源
STATUS_MAP = {
    "生产中": "IN_PRODUCTION",
    "已完成": "COMPLETED",
    "已取消": "CANCELLED",
    "已暂停": "SUSPENDED",
    "待返工": "PENDING_REWORK",
}

STATUS_REVERSE = {v: k for k, v in STATUS_MAP.items()}


class WorkOrderMESAdapter(ConceptAdapter):
    """MES 生产工单适配器。

    将 WorkOrder 本体的 action 映射到 MES REST 端点，
    处理嵌套请求结构、枚举值翻译、响应规范化。
    """

    def __init__(self, concept_name: str):
        super().__init__(concept_name)

    # ── 字段映射 ───────────────────────────────────────────────

    _FIELD_MAP = {
        "productName": "product_name",
        "quantity": "planned_qty",
        "dueDate": "due_date",
        "status": "order_status",
        "orderId": "order_id",
        "reworkQty": "rework_qty",
        "reworkOperation": "rework_operation",
    }

    _ACTION_PATHS = {
        "query":           ("/api/production/orders/search", "POST"),
        "create":          ("/api/production/orders", "POST"),
        "startProduction": ("/api/production/orders/{id}/start", "POST"),
        "markAsComplete":  ("/api/production/orders/{id}/complete", "POST"),
        "markAsRework":    ("/api/production/orders/{id}/rework", "POST"),
        "suspend":         ("/api/production/orders/{id}/suspend", "POST"),
        "resume":          ("/api/production/orders/{id}/resume", "POST"),
        "close":           ("/api/production/orders/{id}/close", "POST"),
        "cancel":          ("/api/production/orders/{id}/cancel", "POST"),
        "createReworkOrder": ("/api/production/rework-orders", "POST"),
        "reduceQuantity":  ("/api/production/orders/{id}/reduce", "POST"),
        "accumulateRework": ("/api/production/orders/{id}/accumulate-rework", "POST"),
    }

    # ── 辅助方法 ──────────────────────────────────────────────

    def _translate_fields(self, data: dict) -> dict:
        """将本体字段名翻译为外部 API 字段名。"""
        result = {}
        for ont_name, value in data.items():
            target = self._FIELD_MAP.get(ont_name, ont_name)
            # 翻译已知的枚举值
            if target == "order_status" and isinstance(value, str):
                result[target] = STATUS_MAP.get(value, value)
            else:
                result[target] = value
        return result

    @staticmethod
    def _translate_status(value: str) -> str:
        """将外部 API 状态码翻译回中文。"""
        return STATUS_REVERSE.get(value, value)

    # ── 接口实现 ─────────────────────────────────────────────

    def build_request(self, action: str, args: dict) -> dict:
        """构建 MES API 请求。"""
        ep = self._ACTION_PATHS.get(action)
        if not ep:
            ep = (f"/api/production/orders/search", "POST")

        path, method = ep
        entity_id = args.pop("id", "") or args.pop("workOrderId", "")
        path = path.replace("{id}", str(entity_id)) if entity_id else path.replace("{id}", "")

        body = self._translate_fields(args)
        return {"path": path, "method": method, "body": body}

    def parse_response(self, action: str, data: dict) -> dict:
        """解析 MES API 响应，转为 Agent 可读结果。"""
        if isinstance(data, list):
            items = []
            for item in data:
                item["status"] = self._translate_status(item.get("order_status", ""))
            return {"success": True, "text": f"返回 {len(items)} 条工单", "entityId": None}

        if "error" in data:
            return {"success": False, "text": str(data["error"]), "entityId": None}

        order_id = data.get("order_id") or data.get("id") or ""
        order_status = self._translate_status(data.get("order_status", ""))
        product = data.get("product_name", "")

        labels = {
            "create": f"已创建工单 {order_id}: {product}",
            "startProduction": f"工单 {order_id} 已开工",
            "markAsComplete": f"工单 {order_id} 已完工",
            "markAsRework": f"工单 {order_id} 已标记返工",
            "suspend": f"工单 {order_id} 已暂停",
            "resume": f"工单 {order_id} 已恢复",
            "close": f"工单 {order_id} 已关闭",
            "cancel": f"工单 {order_id} 已取消",
            "createReworkOrder": f"已创建返工单 {order_id}",
            "reduceQuantity": f"工单 {order_id} 已扣减数量",
            "accumulateRework": f"工单 {order_id} 已累加返工数量",
        }

        return {
            "success": True,
            "text": labels.get(action, f"操作完成: {order_id}"),
            "entityId": str(order_id),
        }
