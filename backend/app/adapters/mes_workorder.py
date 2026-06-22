"""WorkOrder MES 适配器 — 生产工单本体到 MES 多模块 API 的协议翻译。

核心映射逻辑
═══════════════════════════════════════════════════════════════════════════
MES 中「工单」由两层承载：

  MPS 层 — MPSMO（制造订单）
    - 排产计划层的工单，从 ERP 同步或手动创建
    - 字段: Id, MONo, MaterialCode, MaterialName, PlanQty, Status, StartDate, DueDate
    - API: /MESApi/MPS/MO/* (add, edit, delete, enable, disable, list, getDetail)

  MES 执行层 — WorkOrderMain（执行工单）
    - 工位执行层的工单，关联 MPS 制造订单
    - 字段: WorkOrderMainId, WorkOrderNo, PlanQty, CompletedQty, OrderStatus
    - API: /MESApi/WorkOrder/getPages (查询执行工单)

本适配器将本体 12 个 action 映射到 MES 真实端点：
  - 查询 → WorkOrder/getPages (执行层工单列表)
  - 创建/编辑/删除 → MPS/MO/* (排产层制造订单 CRUD)
  - 启停控制 → MPS/MO/enable, MPS/MO/disable
  - 返工类 → MPS/MO/add (创建返工单) / MPS/MO/edit (修改订单)
  - 注意：MES 中不存在独立的 "开工/完工/暂停/恢复" 工单级端点，
    这些操作在工位执行层通过 ProcessFlowCard + RecordReport 完成，
    工单级仅能做 enable/disable 控制
═══════════════════════════════════════════════════════════════════════════
"""

from app.adapters.base import ConceptAdapter


class WorkOrderMESAdapter(ConceptAdapter):
    """MES 生产工单适配器 — MPS 制造订单 + MES 执行工单双入口。

    设计要点
    ────────
    1. 查询走 MES 执行层: WorkOrder/getPages 返回工位执行视角的工单列表
    2. CRUD 走 MPS 排产层: MPS/MO/add|edit|delete 管理制造订单
    3. 启停控制: MPS/MO/enable (上线) / disable (下线)
       - 启用 = 工单可被工位选择执行
       - 禁用 = 工单从工位队列中移除
    4. 返工/扣减: 通过 MPS/MO/add 创建新返工单，或 MPS/MO/edit 修改数量
    5. 本体中 startProduction/markAsComplete/suspend/resume 没有直接的
       工单级 MES 端点对应，映射到 enable/disable/edit(状态) 作为近似操作
    """

    def __init__(self, concept_name: str):
        super().__init__(concept_name)

    # ── 字段映射 ────────────────────────────────────────────
    # 左侧 = 本体属性名（WorkOrder 概念定义在 manufacturing.onto.yaml）
    # 右侧 = MES API 请求参数字段名
    #
    # 映射说明:
    #   id          → id           : 工单标识，两边一致（MES 用 WorkOrderMainId）
    #   orderId     → workOrderNo  : 工单号，本体用 orderId，MES 用 WorkOrderNo
    #   productName → materialName : 产品名称，MES 用 MaterialName（物料名称）
    #   quantity    → planQty      : 工单数量，MES 用 PlanQty（计划数量）
    #   dueDate     → dueDate      : 交货日期，两边一致
    #   status      → orderStatus  : 工单状态，MES 用 OrderStatus
    #   startDate   → startDate    : 计划开始日期
    #   reworkQty   → reworkQty    : 返工数量
    #   reworkOperation → reworkOperation : 返工工序

    _FIELD_MAP = {
        "id": "id",
        "orderId": "workOrderNo",
        "productName": "materialName",
        "quantity": "planQty",
        "qty": "planQty",
        "dueDate": "dueDate",
        "status": "orderStatus",
        "startDate": "startDate",
        "reworkQty": "reworkQty",
        "reworkOperation": "reworkOperation",
        "operation": "reworkOperation",
    }

    # ── Action → MES 真实端点映射 ──────────────────────────
    # 每个 action 对应 (API路径, HTTP方法)
    #
    # 查询层 — MES 执行工单:
    #   query → GET /MESApi/WorkOrder/getPages : 分页查询执行工单列表
    #   注意: getPages 接受 PageParm 查询参数 {page, pageSize, where, order}
    #
    # CRUD 层 — MPS 制造订单:
    #   create → POST /MESApi/MPS/MO/add        : 新建制造订单
    #   cancel → DELETE /MESApi/MPS/MO/delete   : 删除制造订单
    #
    # 启停控制 — MPS 制造订单:
    #   startProduction → PUT /MESApi/MPS/MO/enable : 启用 MO（工位可见）
    #   resume          → PUT /MESApi/MPS/MO/enable : 重新启用
    #   suspend         → PUT /MESApi/MPS/MO/disable: 禁用 MO（暂停）
    #   close           → PUT /MESApi/MPS/MO/disable: 禁用 MO（关闭）
    #
    # 状态变更:
    #   markAsComplete → PUT /MESApi/MPS/MO/edit   : 修改 MO 为完成状态
    #
    # 返工/数量调整:
    #   createReworkOrder → POST /MESApi/MPS/MO/add : 新建返工制造订单
    #   markAsRework      → PUT /MESApi/MPS/MO/edit : 修改 MO 为返工状态
    #   reduceQuantity    → PUT /MESApi/MPS/MO/edit : 修改计划数量
    #   accumulateRework  → PUT /MESApi/MPS/MO/edit : 修改返工累计数量
    #
    # 注意: enable/disable 接受 int[] ids 数组，可批量操作

    _ACTION_PATHS = {
        "query":           ("/MESApi/WorkOrder/getPages", "GET"),
        "create":          ("/MESApi/MPS/MO/add", "POST"),
        "startProduction": ("/MESApi/MPS/MO/enable", "PUT"),
        "markAsComplete":  ("/MESApi/MPS/MO/edit", "PUT"),
        "markAsRework":    ("/MESApi/MPS/MO/edit", "PUT"),
        "suspend":         ("/MESApi/MPS/MO/disable", "PUT"),
        "resume":          ("/MESApi/MPS/MO/enable", "PUT"),
        "close":           ("/MESApi/MPS/MO/disable", "PUT"),
        "cancel":          ("/MESApi/MPS/MO/delete", "DELETE"),
        "createReworkOrder": ("/MESApi/MPS/MO/add", "POST"),
        "reduceQuantity":  ("/MESApi/MPS/MO/edit", "PUT"),
        "accumulateRework": ("/MESApi/MPS/MO/edit", "PUT"),
    }

    # ── 辅助方法 ──────────────────────────────────────────────

    def _translate_fields(self, data: dict) -> dict:
        """将本体字段名翻译为 MES API 字段名。

        对每个输入字段查找 _FIELD_MAP 获取 MES 对应字段名，
        未映射的字段保持原名。
        """
        result = {}
        for ont_name, value in data.items():
            target = self._FIELD_MAP.get(ont_name, ont_name)
            result[target] = value
        return result

    # ── 接口实现 ─────────────────────────────────────────────

    def build_request(self, action: str, args: dict) -> dict:
        """构建 MES API 请求。

        MES 端点按参数格式分为三种:
          GET getPages  → PageParm 查询参数 {page, pageSize, where, order}
          POST add     → MPSMO 对象 body
          PUT enable/disable/edit → int[] ids 或 MPSMO body
          DELETE delete → int id query 参数
        """
        ep = self._ACTION_PATHS.get(action)
        if not ep:
            ep = ("/MESApi/WorkOrder/getPages", "GET")

        path, method = ep
        entity_id = args.pop("id", "") or args.pop("workOrderId", "")

        # ── 按 MES 端点要求构建请求体 ──
        if action in ("startProduction", "resume", "suspend", "close"):
            # enable/disable 端点需要 int[] ids 数组
            try:
                body = {"ids": [int(entity_id)]} if entity_id else {}
            except (ValueError, TypeError):
                body = {"ids": [entity_id]} if entity_id else {}
        elif action == "cancel":
            # delete 端点需要 int id 查询参数
            if entity_id:
                path = f"{path}?id={entity_id}"
            body = {}
        elif action in ("markAsComplete", "markAsRework", "reduceQuantity", "accumulateRework"):
            # edit 端点需要 MPSMO 对象，包含 id 和要修改的字段
            body = self._translate_fields(args)
            if entity_id:
                body["id"] = entity_id
        elif action == "create" or action == "createReworkOrder":
            # add 端点需要完整的 MPSMO 对象
            body = self._translate_fields(args)
            if entity_id:
                body["id"] = entity_id
        else:
            # query 及其他: GET 请求，字段作为查询参数
            body = self._translate_fields(args)

        return {"path": path, "method": method, "body": body}

    def parse_response(self, action: str, data: dict) -> dict:
        """解析 MES API 响应 — 统一转为 Agent 可读格式。

        MES API 返回格式:
          1. getPages 返回 {rows: [...], total: N}
          2. CRUD 操作返回 MPSMO 对象 {id, moNo, materialCode, ...}
          3. 错误返回 {error: "..."} 或 {success: false, message: "..."}
        """
        # 情况1: 分页查询 — getPages 返回 {rows: [...], total: N}
        if data.get("rows"):
            rows = data["rows"]
            items = [{
                "id": r.get("workOrderMainId") or r.get("id", ""),
                "orderId": r.get("workOrderNo", ""),
                "productName": r.get("materialName", ""),
                "quantity": r.get("planQty", 0),
                "completedQty": r.get("completedQty", 0),
                "status": r.get("orderStatus", ""),
            } for r in rows]
            return {"success": True, "text": f"返回 {len(items)} 条工单", "entityId": None}

        # 情况2: 直接返回数组
        if isinstance(data, list):
            items = []
            for item in data:
                items.append({
                    "id": item.get("workOrderMainId") or item.get("id", ""),
                    "orderId": item.get("workOrderNo", ""),
                    "productName": item.get("materialName", ""),
                    "status": item.get("orderStatus", ""),
                })
            return {"success": True, "text": f"返回 {len(items)} 条工单", "entityId": None}

        # 情况3: 错误响应
        if "error" in data or data.get("success") is False:
            msg = data.get("error") or data.get("message", "操作失败")
            return {"success": False, "text": str(msg), "entityId": None}

        # 情况4: 操作成功 — MPS MO CRUD 返回
        order_id = data.get("id") or data.get("moNo") or ""
        order_no = data.get("moNo") or data.get("workOrderNo", "")
        material = data.get("materialName") or data.get("materialCode", "")

        labels = {
            "create": f"已创建制造订单 {order_no}: {material}",
            "startProduction": f"工单 {order_no} 已启用，工位可执行",
            "markAsComplete": f"工单 {order_no} 已完工",
            "markAsRework": f"工单 {order_no} 已标记返工",
            "suspend": f"工单 {order_no} 已禁用(暂停)",
            "resume": f"工单 {order_no} 已重新启用",
            "close": f"工单 {order_no} 已禁用(关闭)",
            "cancel": f"工单 {order_no} 已删除",
            "createReworkOrder": f"已创建返工单 {order_no}: {material}",
            "reduceQuantity": f"工单 {order_no} 数量已调整",
            "accumulateRework": f"工单 {order_no} 返工数量已更新",
        }

        return {
            "success": True,
            "text": labels.get(action, f"操作完成: {order_no}"),
            "entityId": str(order_id),
        }
