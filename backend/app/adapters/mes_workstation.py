"""WorkStation MES 适配器 — 工位信息映射到 MES 基础数据 + 登录控制 + 执行上下文 API。

核心映射逻辑
═══════════════════════════════════════════════════════════════════════════
MES 工位有三个维度的接口：

  基础数据层 — 工位 CRUD
    - 工位的基本属性（编码、名称、所属产线、工作中心）
    - API: GET /MESApi/Basic/WorkStation/getPages, getActiveWorkStations

  执行控制层 — 工位登录/登出
    - 操作工在工位刷卡登录/登出
    - API: POST /MESApi/WorkOrderExecute/Login, POST /MESApi/WorkOrderExecute/Logout

  执行上下文层 — ExecuteInfo（P0 优化）
    - 一次调用返回工位全部执行上下文：ProcessRecordId、PrepareStatus、
      工单/物料/模具/工装/工艺卡验证状态、按钮和控制卡可见性
    - API: GET /MESApi/WorkOrderExecute/ExecuteInfo
    - 这是 Agent 最重要的单一端点 —— 替代 4 次分散查询

本体 WorkStation 有 4 个 action:
  query              → 查询工位列表
  login              → 操作工登录工位
  logout             → 操作工登出工位
  getExecutionContext → 获取执行上下文（P0 新增）
═══════════════════════════════════════════════════════════════════════════
"""

from app.adapters.base import ConceptAdapter


class WorkStationMESAdapter(ConceptAdapter):
    """MES 工位适配器 — 工位基础数据 + 登录控制 + 执行上下文。

    设计要点
    ────────
    1. 本体 id → MES workStationCode: 工位编号
    2. 本体 name → MES workStationName: 工位名称
    3. Login 需要 PlantCode + WorkStationCode + EmpCode
    4. Logout 只需要 workStationId
    5. ExecuteInfo 是核心端点 — 聚合返回工位全部状态，Agent 调用次数 4→1
    """

    def __init__(self, concept_name: str):
        super().__init__(concept_name)

    # ── 字段映射 ────────────────────────────────────────────
    # 左侧 = 本体属性名（WorkStation 概念定义在 manufacturing.onto.yaml）
    # 右侧 = MES API 请求参数字段名
    #
    # 映射说明:
    #   id        → workStationCode : 工位编号，本体用 id，MES 用 Code
    #   name      → workStationName : 工位名称
    #   cycleTime → cycleTime       : 节拍时间

    _FIELD_MAP = {
        "id": "workStationCode",
        "name": "workStationName",
        "cycleTime": "cycleTime",
    }

    # ── Action → MES 端点映射 ──────────────────────────────
    # query              → GET  /MESApi/Basic/WorkStation/getPages
    # login              → POST /MESApi/WorkOrderExecute/Login
    # logout             → POST /MESApi/WorkOrderExecute/Logout
    # getExecutionContext → GET  /MESApi/WorkOrderExecute/ExecuteInfo

    _ACTION_PATHS = {
        "query":                ("/MESApi/Basic/WorkStation/getPages", "GET"),
        "login":                ("/MESApi/WorkOrderExecute/Login", "POST"),
        "logout":               ("/MESApi/WorkOrderExecute/Logout", "POST"),
        "getExecutionContext":  ("/MESApi/WorkOrderExecute/ExecuteInfo", "GET"),
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
        """构建 MES 工位 API 请求。

        Login 需要: {PlantCode, WorkStationCode, EmpCode}
        Logout 需要: {workStationId}
        ExecuteInfo 需要: {workStationId, empCode(可选)}
        """
        ep = self._ACTION_PATHS.get(action)
        if not ep:
            ep = ("/MESApi/Basic/WorkStation/getPages", "GET")

        path, method = ep
        body = self._translate_fields(args)

        if action == "login":
            # Login 要求: PlantCode, WorkStationCode, EmpCode
            entity_id = args.pop("id", "") or args.pop("workStationId", "")
            emp_code = args.pop("empCode", "") or args.pop("operator", "")
            plant_code = args.pop("plantCode", "")
            if entity_id:
                body["WorkStationCode"] = entity_id
            if emp_code:
                body["EmpCode"] = emp_code
            if plant_code:
                body["PlantCode"] = plant_code
        elif action == "logout":
            # Logout 只需要 workStationId
            entity_id = args.pop("id", "") or args.pop("workStationId", "")
            if entity_id:
                body["workStationId"] = entity_id
        elif action == "getExecutionContext":
            # ExecuteInfo: 工位编号 + 两种开工模式
            #   模式1: workOrderMainId → 工单工序开工（首次进入该工序）
            #   模式2: cardNo           → 流转卡开工（半成品从上一道工序流转过来）
            entity_id = args.pop("id", "") or args.pop("workStationId", "")
            emp_code = args.pop("empCode", "") or args.pop("operator", "")
            work_order_main_id = args.pop("workOrderMainId", "") or args.pop("workOrderId", "")
            card_no = args.pop("cardNo", "") or args.pop("flowCardId", "")
            if entity_id:
                body["workStationId"] = entity_id
            if emp_code:
                body["empCode"] = emp_code
            if work_order_main_id:
                body["workOrderMainId"] = work_order_main_id
            if card_no:
                body["cardNo"] = card_no

        return {"path": path, "method": method, "body": body}

    def parse_response(self, action: str, data: dict) -> dict:
        """解析 MES 工位 API 响应。

        getPages 返回: {rows: [{workStationCode, workStationName, ...}]}
        Login 返回:    {data: {workStationId, isFormula, ...}}
        Logout 返回:   {success: true}
        ExecuteInfo 返回: 聚合上下文 — 见 _parse_execute_info()
        """
        # 情况1: 分页查询
        if data.get("rows"):
            rows = data["rows"]
            items = [{
                "id": r.get("workStationCode", ""),
                "name": r.get("workStationName", ""),
                "lineName": r.get("productLineName", ""),
            } for r in rows]
            return {"success": True, "text": f"返回 {len(items)} 个工位", "entityId": None}

        # 情况2: 数组
        if isinstance(data, list):
            items = [{
                "id": item.get("workStationCode", ""),
                "name": item.get("workStationName", ""),
            } for item in data]
            return {"success": True, "text": f"返回 {len(items)} 个工位", "entityId": None}

        # 情况3: 错误
        if "error" in data or data.get("success") is False:
            msg = data.get("error") or data.get("message", "操作失败")
            return {"success": False, "text": str(msg), "entityId": None}

        # 情况4: ExecuteInfo — 聚合执行上下文
        if action == "getExecutionContext":
            return self._parse_execute_info(data)

        # 情况5: Login/Logout 操作结果
        station_code = data.get("workStationCode") or data.get("WorkStationCode", "")
        labels = {
            "login": f"工位 {station_code} 登录成功",
            "logout": f"工位 {station_code} 已登出",
        }

        return {
            "success": True,
            "text": labels.get(action, "操作完成"),
            "entityId": str(station_code),
        }

    def _parse_execute_info(self, data: dict) -> dict:
        """解析 ExecuteInfo 响应 — 将 MES 返回的复杂上下文结构化为 Agent 可读格式。

        ExecuteInfo 返回的核心字段（提取自 MES 前端 Execute.jsx）:
          - data.processRecordId — 加工记录 ID（后续报工/暂停/恢复的入参）
          - data.prepareStatus      — 换型就绪状态（2=就绪可执行，其他=需换型验证）
          - data.isFormula          — 是否配方模式
          - data.mesLineStockMaterialListLocations — 工位物料箱库存
          - data.mesPrepareCheckLogs               — 模具/工装/物料/工艺卡验证记录
          - data.mesProcessControlCards             — 控制卡按钮配置
          - data.buttonsVisibility                  — 功能按钮可见性
          - data.cardsShowOrHide                    — 卡片区域显隐
          - data.workStationProcessRecord        — 当前加工记录详情
        """
        info = data.get("data", data)  # 兼容嵌套和直接返回

        # ── 核心字段提取 ──
        ctx = {
            "processRecordId": info.get("processRecordId", ""),
            "prepareStatus": info.get("prepareStatus", ""),
            "isFormula": info.get("isFormula", False),
        }

        # ── 准备状态描述 ──
        prepare_status_map = {
            2: "已就绪 — 可直接执行",
        }
        ctx["prepareStatusText"] = prepare_status_map.get(
            info.get("prepareStatus"), f"未就绪(状态={info.get('prepareStatus', '?')})，需换型验证"
        )

        # ── 换型验证项 — 模具/工装/物料/工艺卡 ──
        check_logs = info.get("mesPrepareCheckLogs", [])
        if check_logs:
            ctx["prepareChecks"] = [{
                "checkType": log.get("checkType", ""),
                "checkName": log.get("checkName", ""),
                "code": log.get("mouldCode") or log.get("toolingCode")
                        or log.get("materialCode") or log.get("cardCode", ""),
                "status": "已确认" if log.get("statusConfirm") else "待确认",
            } for log in check_logs]

        # ── 工位物料箱库存 — 当前工位已上料的物料 ──
        materials = info.get("mesLineStockMaterialListLocations", [])
        if materials:
            ctx["stationMaterials"] = [{
                "materialCode": m.get("materialCode", ""),
                "materialName": m.get("materialName", ""),
                "batchNo": m.get("batchNo", ""),
                "qty": m.get("qty", 0),
                "status": {0: "未上料", 1: "已上料", 2: "已消耗"}.get(
                    m.get("mesStockStatus"), "未知"),
                "positionCode": m.get("positionCode", ""),
            } for m in materials]

        # ── 当前加工记录 — 工单/工序/任务信息 ──
        record = info.get("workStationProcessRecord")
        if record:
            ctx["currentRecord"] = {
                "recordId": record.get("id", ""),
                "workOrderNo": record.get("workOrderMainId", ""),
                "processName": record.get("processName", ""),
                "qty": record.get("qty", 0),
                "completedQty": record.get("completedQty", 0),
                "status": record.get("status", ""),
            }
            # 关联工单 ID
            ctx["workOrderId"] = record.get("workOrderMainId", "")

        # ── 控制卡和按钮可见性 — 前端 UI 驱动，Agent 据此判断可执行操作 ──
        buttons = info.get("buttonsVisibility", {})
        if buttons:
            ctx["availableActions"] = {
                "canReport": buttons.get("btnRecordReport", False),
                "canPause": buttons.get("btnRecordPause", False),
                "canContinue": buttons.get("btnRecordContinue", False),
                "canChangeover": buttons.get("btnChangeModel", False),
                "canDownMaterial": buttons.get("btnDownMaterial", False),
                "canConsumeMaterial": buttons.get("btnConsumeMaterial", False),
            }

        cards = info.get("cardsShowOrHide", {})
        if cards:
            ctx["visibleCards"] = {
                "qccCard": cards.get("QccCardShow", False),
                "spcCard": cards.get("SpcCardShow", False),
                "materialCard": cards.get("MaterialCardShow", False),
            }

        # ── 构建摘要 ──
        parts = []
        if ctx.get("processRecordId"):
            parts.append(f"加工记录={ctx['processRecordId']}")
        if ctx.get("workOrderId"):
            parts.append(f"工单={ctx['workOrderId']}")
        parts.append(f"就绪状态={ctx['prepareStatusText']}")
        if materials:
            parts.append(f"物料箱={len(materials)}种")
        if check_logs:
            parts.append(f"验证项={len(check_logs)}")

        return {
            "success": True,
            "text": " | ".join(parts),
            "entityId": str(ctx.get("processRecordId", "")),
        }
