"""WorkOrderTask MES 适配器 — 工单任务映射到 MES 排产/执行/流转卡三层 API。

核心映射逻辑
═══════════════════════════════════════════════════════════════════════════
MES 中「工单任务」由三层结构承载：

  计划层 — MPSLinePlan（排产计划）
    - 产线排产的工序级计划，Agent 查询"有哪些任务"时回答排产计划
    - 字段: no, processId, workcenterId, qty, MESStatus, starttime, endtime
    - API: GET /MESApi/MPS/LinePlan/list

  流转卡层 — ProcessFlowCard（流转卡）
    - 计划到执行的桥梁，表示"这个任务已发卡，可以开始做了"
    - 创建: POST /MESApi/ProcessFlowCard/createProcessFlow
    - 开工: POST /MESApi/ProcessFlowCard/processFlowStart
    - 完工: POST /MESApi/ProcessFlowCard/processFlowEnd
    - 注意: MES 中没有 "RecordStart" 端点，开工 = 流转卡创建 + processFlowStart

  执行层 — WorkStationProcessRecord（工位加工记录）
    - 工位操作的实际记录，报工/暂停/恢复都在这一层
    - API: POST /MESApi/WorkOrderExecute/RecordReport|RecordPause|RecordContinue|RecordDel

本适配器将本体 8 个 action 路由到对应层级:
  query        → 计划层: MPS/LinePlan/list
  startTask    → 流转卡层: ProcessFlowCard/processFlowStart（修复：原来指向不存在的 RecordStart）
  completeTask → 执行层: RecordReport（最终报工）
  suspendTask  → 执行层: RecordPause
  resumeTask   → 执行层: RecordContinue
  changeover   → 执行层: ChangeModel
  reportProgress  → 执行层: RecordReport（阶段性报工）
  queryReports    → 执行层: ReportLog
═══════════════════════════════════════════════════════════════════════════
"""

from app.adapters.base import ConceptAdapter


class WorkOrderTaskMESAdapter(ConceptAdapter):
    """MES 工单任务适配器 — 计划/流转卡/执行三层 API 翻译。

    设计原则
    ────────
    1. query → 查计划层（MPSLinePlan），Agent 问"有哪些任务"时回答排产计划
    2. startTask → 流转卡层（ProcessFlowCard/processFlowStart），
       在 MES 中没有独立的"开工"按钮，开工 = 流转卡创建后执行 processFlowStart，
       工位操作员通过 ExecuteInfo 获取上下文 → 完成换型验证 → PrepareStatus=2 后即可执行
    3. completeTask/suspendTask/resumeTask/reportProgress → 执行层（WorkOrderExecute），
       这些是工位终端的实际操作
    4. changeover → 执行层 ChangeModel，触发工位换型
    """

    def __init__(self, concept_name: str):
        super().__init__(concept_name)

    # ── 字段映射 ────────────────────────────────────────────
    # 左侧 = 本体属性名（WorkOrderTask 概念定义在 manufacturing.onto.yaml）
    # 右侧 = MES API 请求参数字段名
    #
    # 映射说明:
    #   processOperation → processId    : 工序，MES 中工序用 processId 标识
    #   workCenter       → workcenterId : 工作中心，MES 中使用 workcenterId
    #   qty              → qty          : 计划数量，两边同名
    #   completedQty     → completedQty : 已完成数量，两边同名（query 用）
    #   scrapQty         → scrapQty     : 报废数量，两边同名（query 用）
    #   startTime        → starttime    : 计划开始时间，MES 字段全小写
    #   endTime          → endtime      : 计划结束时间，MES 字段全小写
    #   status           → MESStatus    : 任务状态，MES 的排产状态字段
    #   workStationId    → workStationId: 工位ID，两边同名（执行层用）
    #   workOrderId      → workOrderMainId: 所属工单，MES 关联字段
    #   defectType       → qualityDefectId: 缺陷类型，MES 质检缺陷 ID
    #   operator         → empCode      : 操作员工号，MES 用 empCode
    #   materialCode     → materialCode : 物料编码（上料/消耗/下料用）
    #   batchNo          → batchNo      : 批次号（上料/消耗用）

    _FIELD_MAP = {
        "processOperation": "processId",
        "workCenter": "workcenterId",
        "qty": "qty",
        "completedQty": "completedQty",
        "scrapQty": "scrapQty",
        "startTime": "starttime",
        "endTime": "endtime",
        "status": "MESStatus",
        "workStationId": "workStationId",
        "workOrderId": "workOrderMainId",
        "defectType": "qualityDefectId",
        "operator": "empCode",
        "materialCode": "materialCode",
        "batchNo": "batchNo",
    }

    # ── Action → MES 端点映射 ──────────────────────────────
    # 每个 action 对应 (API路径, HTTP方法)
    #
    # 计划层:
    #   query → GET /MESApi/MPS/LinePlan/list : 查询排产计划列表
    #
    # 流转卡层:
    #   startTask → POST /MESApi/ProcessFlowCard/processFlowStart : 流转卡开工
    #   （MES 中不存在 RecordStart 端点，开工 = 流转卡开工）
    #
    # 执行层 — 工位操作:
    #   completeTask    → POST RecordReport   : 完工报工（含 RecordCardDtos）
    #   suspendTask     → POST RecordPause    : 暂停加工（recordId）
    #   resumeTask      → POST RecordContinue : 恢复加工（recordId）
    #   changeover      → POST ChangeModel    : 工位换型（workStationId）
    #
    # 执行层 — 报工:
    #   reportProgress  → POST RecordReport   : 阶段性报工（RecordCardDtos 结构）
    #   queryReports    → GET ReportLog       : 查询报工历史
    #
    # 执行层 — 物料操作:
    #   verifyMaterial  → POST CheckMaterialCode      : 扫码校验物料编码和批次号（工序上料前）
    #   loadMaterial    → POST RecordMaterialConfirm  : 确认上料（校验通过后，更新 StockStatus=1）
    #   consumMaterial  → POST RecordConsumpMaterial  : 消耗物料（更新 StockStatus=2）
    #   downMaterial    → POST DownRecordMaterial     : 下料（移除未消耗完的物料）

    _ACTION_PATHS = {
        "query":              ("/MESApi/MPS/LinePlan/list", "GET"),
        "startTask":          ("/MESApi/ProcessFlowCard/processFlowStart", "POST"),
        "completeTask":       ("/MESApi/WorkOrderExecute/RecordReport", "POST"),
        "suspendTask":        ("/MESApi/WorkOrderExecute/RecordPause", "POST"),
        "resumeTask":         ("/MESApi/WorkOrderExecute/RecordContinue", "POST"),
        "changeover":         ("/MESApi/WorkOrderExecute/ChangeModel", "POST"),
        "reportProgress":     ("/MESApi/WorkOrderExecute/RecordReport", "POST"),
        "queryReports":       ("/MESApi/WorkOrderExecute/ReportLog", "GET"),
        "verifyMaterial":     ("/MESApi/WorkOrderExecute/CheckMaterialCode", "POST"),
        "loadMaterial":       ("/MESApi/WorkOrderExecute/RecordMaterialConfirm", "POST"),
        "consumMaterial":     ("/MESApi/WorkOrderExecute/RecordConsumpMaterial", "POST"),
        "downMaterial":       ("/MESApi/WorkOrderExecute/DownRecordMaterial", "POST"),
    }

    # ── 辅助方法 ──────────────────────────────────────────────

    def _translate_fields(self, data: dict) -> dict:
        """将本体字段名翻译为 MES API 字段名。

        遍历输入 dict，对每个 key 查找 _FIELD_MAP 获取 MES 字段名，
        未找到映射的字段保持原名不变。
        """
        result = {}
        for ont_name, value in data.items():
            target = self._FIELD_MAP.get(ont_name, ont_name)
            result[target] = value
        return result

    # ── 接口实现 ─────────────────────────────────────────────

    def build_request(self, action: str, args: dict) -> dict:
        """构建 MES API 请求 — 按 action 路由到不同层级的 API。

        请求体构建逻辑分三层:
        ┌────────────────────────────────────────────────────────────┐
        │ 计划层 query → GET LinePlan/list，字段作为查询参数        │
        │ 流转卡层 startTask → POST processFlowStart，需要 flowCardId│
        │ 执行层 POST → 工位操作/报工，需要 processRecordId           │
        └────────────────────────────────────────────────────────────┘
        """
        ep = self._ACTION_PATHS.get(action)
        if not ep:
            ep = ("/MESApi/MPS/LinePlan/list", "GET")

        path, method = ep
        # 提取实体 ID: 优先 id → taskId → processRecordId
        entity_id = args.pop("id", "") or args.pop("taskId", "") or args.pop("processRecordId", "")
        body = self._translate_fields(args)

        if method == "GET":
            # 查询类请求: body 中的字段作为 GET 参数传递
            # reportProgress → ReportLog 需要 workStationId, workOrderMainId, processRecordId
            return {"path": path, "method": method, "body": body}
        else:
            # ── POST 类请求: 按 action 类型构建不同的请求体 ──
            if action == "startTask":
                # 流转卡开工: 自动路由优化
                # - 有 flowCardId → ProcessFlowCard/processFlowStart（直接开工）
                # - 无 flowCardId 但有 workOrderId+cardId → 自动路由到 createProcessFlow（先创建流转卡）
                flow_card_id = args.pop("flowCardId", "") or entity_id
                work_order_id = args.pop("workOrderId", "") or body.pop("workOrderMainId", "")
                card_id = args.pop("cardId", "")
                if flow_card_id:
                    # 已有流转卡 → 直接开工
                    body["id"] = flow_card_id
                elif work_order_id and card_id:
                    # 无流转卡 → 切换到创建模式
                    path = "/MESApi/ProcessFlowCard/createProcessFlow"
                    body = {"workOrderNo": work_order_id, "cardNo": card_id}
                else:
                    # 参数不完整 → 告知 Agent 需要更多信息
                    body["_hint"] = "需要 flowCardId 或 (workOrderId + cardId)"
            elif action == "changeover":
                # 换型: ChangeModel 只需要 workStationId
                work_station_id = args.pop("workStationId", "") or entity_id
                if work_station_id:
                    body["workStationId"] = work_station_id
            elif action in ("suspendTask", "resumeTask"):
                # 暂停/恢复: RecordPause/RecordContinue 需要 recordId
                if entity_id:
                    body["recordId"] = entity_id
            elif action in ("completeTask", "reportProgress"):
                # 报工: RecordReport 需要 RecordCardDtos 数组结构
                # 每条 RecordCardDto: {id, qrCode, qualifiedQty, scrapQty, remark, isComplete}
                card_entry_id = args.pop("cardEntryId", "")
                report = {
                    "qualifiedQty": body.pop("completedQty", body.pop("qualifiedQty", 0)),
                    "scrapQty": body.pop("scrapQty", 0),
                }
                if card_entry_id:
                    report["id"] = card_entry_id
                if entity_id:
                    body["processRecordId"] = entity_id
                # 完工报工标记为完成
                if action == "completeTask":
                    report["isComplete"] = True
                body["recordCardDtos"] = [report]
            elif action == "verifyMaterial":
                # 物料校验: CheckMaterialCode 扫描物料编码和批次号进行验证
                # 这是上料前的必要步骤，校验通过后才可调用 loadMaterial 确认上料
                material_code = body.pop("materialCode", "") or args.get("materialCode", "")
                batch_no = body.pop("batchNo", "") or args.get("batchNo", "")
                body = {
                    "materialCode": material_code,
                    "batchNo": batch_no,
                }
                if entity_id:
                    body["processRecordId"] = entity_id
            elif action == "loadMaterial":
                # 确认上料: RecordMaterialConfirm — 物料校验通过后确认加载到工位
                # 前置步骤: verifyMaterial (CheckMaterialCode) 必须先通过
                material_code = body.pop("materialCode", "") or args.get("materialCode", "")
                batch_no = body.pop("batchNo", "") or args.get("batchNo", "")
                load_qty = body.pop("qty", 0) or args.get("qty", 0)
                body = {
                    "materialCode": material_code,
                    "batchNo": batch_no,
                    "qty": load_qty,
                }
                if entity_id:
                    body["processRecordId"] = entity_id
            elif action == "consumMaterial":
                # 消耗物料: RecordConsumpMaterial 需要 materialCode + qty + batchNo(可选)
                material_code = body.pop("materialCode", "") or args.get("materialCode", "")
                consum_qty = body.pop("qty", 0) or args.get("qty", 0)
                batch_no = body.pop("batchNo", "") or args.get("batchNo", "")
                body = {
                    "materialCode": material_code,
                    "qty": consum_qty,
                }
                if batch_no:
                    body["batchNo"] = batch_no
                if entity_id:
                    body["processRecordId"] = entity_id
            elif action == "downMaterial":
                # 下料: DownRecordMaterial 需要 materialCode + qty
                material_code = body.pop("materialCode", "") or args.get("materialCode", "")
                down_qty = body.pop("qty", 0) or args.get("qty", 0)
                body = {
                    "materialCode": material_code,
                    "qty": down_qty,
                }
                if entity_id:
                    body["processRecordId"] = entity_id
            return {"path": path, "method": method, "body": body}

    def parse_response(self, action: str, data: dict) -> dict:
        """解析 MES API 响应 — 统一转为 Agent 可读格式。

        MES API 返回格式:
          1. list — 数组（LinePlan 列表查询）
          2. {rows: [...], total: N} — 分页格式
          3. dict — 单条操作结果或错误

        返回值统一为: {success: bool, text: str, entityId: str | None}
        """
        # 情况1: 直接返回数组 — 排产计划列表
        if isinstance(data, list):
            items = []
            for item in data:
                items.append({
                    "id": item.get("no") or item.get("id", ""),
                    "status": item.get("MESStatus") or item.get("status", ""),
                    "qty": item.get("qty", 0),
                    "completedQty": item.get("completedQty", 0),
                })
            return {"success": True, "text": f"返回 {len(items)} 条任务", "entityId": None}

        # 情况2: 分页格式 — getPages 标准返回
        if data.get("rows"):
            rows = data["rows"]
            items = [{
                "id": r.get("no") or r.get("id", ""),
                "status": r.get("MESStatus") or r.get("status", ""),
                "qty": r.get("qty", 0),
                "completedQty": r.get("completedQty", 0),
            } for r in rows]
            return {"success": True, "text": f"返回 {len(items)} 条任务", "entityId": None}

        # 情况3: 错误响应
        if "error" in data or data.get("success") is False:
            msg = data.get("error") or data.get("message", "操作失败")
            return {"success": False, "text": str(msg), "entityId": None}

        # 情况4: POST 操作成功
        task_id = data.get("no") or data.get("id", "")
        labels = {
            "startTask": f"流转卡已开工，任务 {task_id} 进入执行状态",
            "completeTask": f"任务 {task_id} 已完工",
            "suspendTask": f"任务 {task_id} 已挂起",
            "resumeTask": f"任务 {task_id} 已恢复加工",
            "changeover": "工位已换型",
            "reportProgress": f"任务 {task_id} 已上报进度",
            "queryReports": f"任务 {task_id} 报工记录已获取",
            "verifyMaterial": f"任务 {task_id} 物料校验完成",
            "loadMaterial": f"任务 {task_id} 已确认上料",
            "consumMaterial": f"任务 {task_id} 已消耗物料",
            "downMaterial": f"任务 {task_id} 已下料",
        }

        return {
            "success": True,
            "text": labels.get(action, "操作完成"),
            "entityId": str(task_id),
        }
