"""LineStockTransaction MES 适配器 — 线边库存流水映射到 MES LineStock Task API。

核心映射逻辑
═══════════════════════════════════════════════════════════════════════════
MES 线边库存流水：

  出入库任务 — LineStock/Task
    - 线边仓的出入库操作，记录物料从线边仓 → 工位（出库）或从工位 → 线边仓（入库）
    - out:      POST /MESApi/LineStock/Task/out       — 出库（物料从线边仓发到工位）
    - in:       POST /MESApi/LineStock/Task/in        — 入库（工位退料回线边仓）
    - completed: POST /MESApi/LineStock/Task/completed — 完成任务
    - processlist: GET /MESApi/LineStock/Task/processlist — 查询任务列表

  出入明细 — LineStock/Stock
    - 库存出入流水明细查询
    - API: GET /MESApi/LineStock/Stock/getInOutStockPages

本体 LineStockTransaction 有 query 和 create 两个操作:
  - query: 查询出入库流水
  - create: 创建出库/入库任务

这是 P1 中唯一一个既有查询又有写操作的概念。
═══════════════════════════════════════════════════════════════════════════
"""

from app.adapters.base import ConceptAdapter


class LineStockTransactionMESAdapter(ConceptAdapter):
    """MES 线边库存流水适配器 — 出入库操作。

    设计要点
    ────────
    1. create 需要 direction 字段区分出库/入库:
       - "out" / "出库" → POST /MESApi/LineStock/Task/out
       - "in" / "入库" → POST /MESApi/LineStock/Task/in
    2. 出入库操作需要: materialCode, qty, batchNo, positionCode
    3. query 返回出入库历史流水
    """

    def __init__(self, concept_name: str):
        super().__init__(concept_name)

    # ── 字段映射 ────────────────────────────────────────────
    # 左侧 = 本体属性名（LineStockTransaction 概念定义在 manufacturing.onto.yaml）
    # 右侧 = MES API 请求参数字段名
    #
    # 映射说明:
    #   id           → id           : 流水ID
    #   materialId   → materialCode : 物料编码
    #   qty          → qty          : 操作数量
    #   direction    → direction    : 方向(out=出库, in=入库)
    #   workOrderId  → workOrderNo  : 关联工单号
    #   batchNo      → batchNo      : 批次号
    #   positionId   → positionCode : 库位编码
    #   barcode      → qrCode       : 物料标签/二维码

    _FIELD_MAP = {
        "id": "id",
        "materialId": "materialCode",
        "qty": "qty",
        "direction": "direction",
        "workOrderId": "workOrderNo",
        "batchNo": "batchNo",
        "positionId": "positionCode",
        "barcode": "qrCode",
    }

    # ── Action → MES 端点映射 ──────────────────────────────
    # query  → GET  /MESApi/LineStock/Stock/getInOutStockPages : 查询出入库明细
    # create → POST /MESApi/LineStock/Task/out 或 Task/in     : 按 direction 路由

    _ACTION_PATHS = {
        "query":  ("/MESApi/LineStock/Stock/getInOutStockPages", "GET"),
        "create": ("/MESApi/LineStock/Task/out", "POST"),
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
        """构建 MES 线边库存流水 API 请求。

        create 操作按 direction 字段路由到不同端点:
          - direction = "out" / "出库" → POST /MESApi/LineStock/Task/out
          - direction = "in" / "入库" → POST /MESApi/LineStock/Task/in

        MES 出入库任务接受 MESLineStockDto 数组:
          [{materialCode, qty, batchNo, positionCode, workOrderNo, qrCode, ...}]
        """
        ep = self._ACTION_PATHS.get(action)
        if not ep:
            ep = ("/MESApi/LineStock/Stock/getInOutStockPages", "GET")

        path, method = ep
        body = self._translate_fields(args)

        if action == "create":
            # 按 direction 路由到 out 或 in 端点
            direction = args.pop("direction", "") or body.get("direction", "")
            if direction in ("in", "入库"):
                path = "/MESApi/LineStock/Task/in"
            elif direction in ("out", "出库"):
                path = "/MESApi/LineStock/Task/out"
            # MES Task/out 和 Task/in 接受 MESLineStockDto 数组
            # 将单个流水包装为数组
            task_entry = {
                k: v for k, v in body.items()
                if k in ("materialCode", "qty", "batchNo", "positionCode",
                         "workOrderNo", "qrCode", "plantCode")
            }
            body = {"dtos": [task_entry]}

        return {"path": path, "method": method, "body": body}

    def parse_response(self, action: str, data: dict) -> dict:
        """解析 MES 线边库存 API 响应。

        Task 返回格式:
          - out/in: {success: true, no: "任务号", ...}
          - getInOutStockPages: {rows: [{billNo, materialCode, qty, type, ...}]}
        """
        # 情况1: 分页查询 — 出入库流水
        if data.get("rows"):
            rows = data["rows"]
            items = [{
                "id": r.get("billNo", ""),
                "materialId": r.get("materialCode", ""),
                "name": r.get("materialName", ""),
                "qty": r.get("qty", 0),
                # type: 1=入库, -1=出库
                "direction": "入库" if r.get("type") == 1 else "出库",
                "batchNo": r.get("batchNo", ""),
                "createTime": r.get("createDate", ""),
            } for r in rows]
            return {"success": True, "text": f"返回 {len(items)} 条流水", "entityId": None}

        # 情况2: 数组
        if isinstance(data, list):
            items = [{
                "id": item.get("billNo", ""),
                "materialId": item.get("materialCode", ""),
                "qty": item.get("qty", 0),
            } for item in data]
            return {"success": True, "text": f"返回 {len(items)} 条流水", "entityId": None}

        # 情况3: 错误
        if "error" in data or data.get("success") is False:
            msg = data.get("error") or data.get("message", "操作失败")
            return {"success": False, "text": str(msg), "entityId": None}

        # 情况4: create 操作结果
        task_no = data.get("no", "")
        direction = "出库" if "/out" in str(data.get("path", "")) else "入库"
        labels = {
            "create": f"线边仓{direction}任务 {task_no} 已创建",
        }

        return {
            "success": True,
            "text": labels.get(action, "操作完成"),
            "entityId": str(task_no),
        }
