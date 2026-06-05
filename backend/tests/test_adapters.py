"""适配器单元测试 — build_request + parse_response 全覆盖。

测试所有 24 个概念适配器的请求构建和响应解析，
使用 mock 数据验证适配器逻辑正确性。
"""
import pytest
from unittest.mock import patch

from app.services.concept_backend_config_service import auto_register_adapters, get_adapter_class


# ── 初始化 ──────────────────────────────────────────────────────

def _ensure_registered():
    """确保适配器已注册，避免重复注册。"""
    auto_register_adapters()


def _adapter(concept_name: str):
    """获取适配器实例。"""
    _ensure_registered()
    cls = get_adapter_class(concept_name)
    assert cls is not None, f"适配器未注册: {concept_name}"
    return cls(concept_name)


# ═══════════════════════════════════════════════════════════════════
# WorkStation 适配器
# ═══════════════════════════════════════════════════════════════════

class TestWorkStationAdapter:
    """工位适配器 — login/logout/getExecutionContext/query"""

    def test_login_builds_correct_request(self):
        a = _adapter("WorkStation")
        req = a.build_request("login", {"id": "WS-001", "empCode": "E001", "plantCode": "P01"})
        assert req["method"] == "POST"
        assert "/Login" in req["path"]
        assert req["body"]["WorkStationCode"] == "WS-001"
        assert req["body"]["EmpCode"] == "E001"

    def test_logout_builds_correct_request(self):
        a = _adapter("WorkStation")
        req = a.build_request("logout", {"id": "WS-001"})
        assert req["method"] == "POST"
        assert "/Logout" in req["path"]

    def test_get_execution_context_builds_correct_request(self):
        a = _adapter("WorkStation")
        req = a.build_request("getExecutionContext", {"id": "WS-001", "empCode": "E001"})
        assert req["method"] == "GET"
        assert "/ExecuteInfo" in req["path"]
        assert req["body"]["workStationId"] == "WS-001"

    def test_parse_execute_info_full_response(self):
        a = _adapter("WorkStation")
        data = {
            "data": {
                "processRecordId": "PR-001",
                "prepareStatus": 2,
                "isFormula": False,
                "mesLineStockMaterialListLocations": [
                    {"materialCode": "M001", "materialName": "钢材", "batchNo": "B001", "qty": 100, "mesStockStatus": 1, "positionCode": "A1"}
                ],
                "workStationProcessRecord": {"id": "R001", "workOrderMainId": "WO-001", "processName": "焊接", "qty": 200, "completedQty": 0, "status": "进行中"},
                "buttonsVisibility": {"btnRecordReport": True, "btnRecordPause": True},
                "mesPrepareCheckLogs": [{"checkType": "模具", "checkName": "上模", "mouldCode": "M-001", "statusConfirm": True}]
            }
        }
        result = a.parse_response("getExecutionContext", data)
        assert result["success"] is True
        assert "PR-001" in result["text"]
        assert "WO-001" in result["text"]
        assert "已就绪" in result["text"]

    def test_parse_execute_info_not_ready(self):
        a = _adapter("WorkStation")
        data = {"data": {"prepareStatus": 0, "processRecordId": ""}}
        result = a.parse_response("getExecutionContext", data)
        assert result["success"] is True
        assert "未就绪" in result["text"]

    def test_parse_paginated_workstations(self):
        a = _adapter("WorkStation")
        data = {"rows": [{"workStationCode": "WS-01", "workStationName": "工位1"}]}
        result = a.parse_response("query", data)
        assert result["success"] is True
        assert "1 个工位" in result["text"]


# ═══════════════════════════════════════════════════════════════════
# WorkOrderTask 适配器
# ═══════════════════════════════════════════════════════════════════

class TestWorkOrderTaskAdapter:
    """工单任务适配器 — 生产执行核心流程"""

    def test_query_builds_get_request(self):
        a = _adapter("WorkOrderTask")
        req = a.build_request("query", {"workcenterId": "WC-01"})
        assert req["method"] == "GET"
        assert "/LinePlan/list" in req["path"]

    def test_start_task_with_flow_card_id(self):
        a = _adapter("WorkOrderTask")
        req = a.build_request("startTask", {"flowCardId": "FC-001"})
        assert req["method"] == "POST"
        assert "/processFlowStart" in req["path"]
        assert req["body"]["id"] == "FC-001"

    def test_start_task_auto_create_flow_card(self):
        """P1 优化: 无 flowCardId 时自动路由到 createProcessFlow"""
        a = _adapter("WorkOrderTask")
        req = a.build_request("startTask", {"workOrderId": "WO-001", "cardId": "CARD-001"})
        assert req["method"] == "POST"
        assert "/createProcessFlow" in req["path"]
        assert req["body"]["workOrderNo"] == "WO-001"
        assert req["body"]["cardNo"] == "CARD-001"

    def test_start_task_missing_params_returns_hint(self):
        a = _adapter("WorkOrderTask")
        req = a.build_request("startTask", {})
        assert "_hint" in req["body"]

    def test_verify_material_builds_get_request(self):
        a = _adapter("WorkOrderTask")
        req = a.build_request("verifyMaterial", {"materialCode": "M001", "batchNo": "B001"})
        assert req["method"] == "GET"
        assert "/CheckMaterialCode" in req["path"]
        assert req["body"]["materialCode"] == "M001"

    def test_load_material_builds_post_request(self):
        a = _adapter("WorkOrderTask")
        req = a.build_request("loadMaterial", {"materialCode": "M001", "batchNo": "B001", "qty": 100})
        assert req["method"] == "POST"
        assert "/RecordMaterialConfirm" in req["path"]
        assert req["body"]["qty"] == 100

    def test_consum_material_builds_post_request(self):
        a = _adapter("WorkOrderTask")
        req = a.build_request("consumMaterial", {"materialCode": "M001", "qty": 30, "batchNo": "B001"})
        assert req["method"] == "POST"
        assert "/RecordConsumpMaterialConfirm" in req["path"]

    def test_complete_task_includes_complete_flag(self):
        a = _adapter("WorkOrderTask")
        req = a.build_request("completeTask", {"id": "REC-001", "completedQty": 100})
        assert "/RecordReport" in req["path"]
        dto = req["body"]["recordCardDtos"][0]
        assert dto["isComplete"] is True
        assert dto["qualifiedQty"] == 100

    def test_report_progress_no_complete_flag(self):
        a = _adapter("WorkOrderTask")
        req = a.build_request("reportProgress", {"id": "REC-001", "completedQty": 50})
        dto = req["body"]["recordCardDtos"][0]
        assert dto.get("isComplete") is not True

    def test_suspend_resume_need_record_id(self):
        a = _adapter("WorkOrderTask")
        req = a.build_request("suspendTask", {"id": "REC-001"})
        assert "/RecordPause" in req["path"]
        assert req["body"]["recordId"] == "REC-001"

        req = a.build_request("resumeTask", {"id": "REC-001"})
        assert "/RecordContinue" in req["path"]

    def test_parse_task_list_response(self):
        a = _adapter("WorkOrderTask")
        data = [{"no": "T001", "MESStatus": "进行中", "qty": 100, "completedQty": 50}]
        result = a.parse_response("query", data)
        assert result["success"] is True
        assert "1 条任务" in result["text"]

    def test_parse_post_success(self):
        a = _adapter("WorkOrderTask")
        result = a.parse_response("completeTask", {"id": "T001", "success": True})
        assert result["success"] is True
        assert "已完工" in result["text"]


# ═══════════════════════════════════════════════════════════════════
# WorkOrder 适配器
# ═══════════════════════════════════════════════════════════════════

class TestWorkOrderAdapter:
    """工单适配器 — CRUD + 状态管理"""

    def test_create_builds_post(self):
        a = _adapter("WorkOrder")
        req = a.build_request("create", {"id": "WO-001", "materialCode": "M001", "qty": 200})
        assert req["method"] == "POST"
        assert "/add" in req["path"]

    def test_start_production_with_numeric_id(self):
        a = _adapter("WorkOrder")
        req = a.build_request("startProduction", {"id": "123"})
        assert req["body"]["ids"] == [123]

    def test_start_production_with_non_numeric_id_falls_back(self):
        """健壮性: 非数字 ID 降级为字符串"""
        a = _adapter("WorkOrder")
        req = a.build_request("startProduction", {"id": "WO-STR-001"})
        assert req["body"]["ids"] == ["WO-STR-001"]

    def test_cancel_builds_delete_with_query_param(self):
        a = _adapter("WorkOrder")
        req = a.build_request("cancel", {"id": "WO-001"})
        assert req["method"] == "DELETE"
        assert "id=WO-001" in req["path"]

    def test_parse_paginated_workorders(self):
        a = _adapter("WorkOrder")
        data = {"rows": [{"workOrderMainId": "ID1", "workOrderNo": "WO-001", "materialName": "产品A", "planQty": 100, "orderStatus": "生产中"}]}
        result = a.parse_response("query", data)
        assert result["success"] is True
        assert "1 条工单" in result["text"]


# ═══════════════════════════════════════════════════════════════════
# GenericQueryAdapter — P2 纯查询概念
# ═══════════════════════════════════════════════════════════════════

class TestGenericQueryAdapter:
    """通用查询适配器 — 11 个概念的 query 端点"""

    @pytest.mark.parametrize("concept,expected_path_segment", [
        ("BOM",                      "/Bom/getBomList"),
        ("BOMItem",                  "/Bom/getBomDetailList"),
        ("WorkOrderBOM",             "/MPS/MO/getWorkOrderBom"),
        ("WorkOrderBOMItem",         "/MPS/MO/getWorkOrderBom"),
        ("ProcessRouting",           "/MPS/Routing/list"),
        ("ProcessCard",              "/ProcessCardRecord/getPages"),
        ("ProductionPreparation",    "/Preparation/getPages"),
        ("InspectionPoint",          "/ToCheck/CheckPoints"),
        ("QualityDefect",            "/QCMApi/Unqualified/List"),
        ("LineStockWarehouse",       "/LineStock/Warehouse/getPages"),
    ])
    def test_query_builds_correct_path(self, concept, expected_path_segment):
        a = _adapter(concept)
        req = a.build_request("query", {"id": "T001"})
        assert req["method"] == "GET"
        assert expected_path_segment in req["path"]

    def test_non_query_action_returns_empty_path(self):
        a = _adapter("BOM")
        req = a.build_request("create", {"id": "T001"})
        assert req["path"] == ""

    def test_field_translation(self):
        a = _adapter("BOM")
        req = a.build_request("query", {"id": "BOM-001", "name": "主BOM"})
        assert "bomNo" in str(req["body"])
        assert req["body"]["bomNo"] == "BOM-001"

    def test_parse_paginated_response(self):
        a = _adapter("BOM")
        data = {"rows": [{"no": "B001", "name": "主BOM"}]}
        result = a.parse_response("query", data)
        assert result["success"] is True
        assert "1 条" in result["text"]

    def test_parse_array_response(self):
        a = _adapter("InspectionPoint")
        data = [{"checkPointCode": "CP01", "checkPointName": "尺寸检查"}]
        result = a.parse_response("query", data)
        assert result["success"] is True

    def test_parse_error_response(self):
        a = _adapter("BOM")
        result = a.parse_response("query", {"error": "数据库错误"})
        assert result["success"] is False
        assert "数据库错误" in result["text"]


# ═══════════════════════════════════════════════════════════════════
# 其他适配器 — Equipment / Employee / Material / 等
# ═══════════════════════════════════════════════════════════════════

class TestOtherAdapters:
    """P1 适配器的基本验证"""

    def test_equipment_query_and_change_status(self):
        a = _adapter("Equipment")
        req = a.build_request("query", {})
        assert "/Equipment/getPages" in req["path"]

        req = a.build_request("changeStatus", {"id": "EQ-001", "status": "维修中"})
        assert req["method"] == "POST"

    def test_employee_query(self):
        a = _adapter("Employee")
        req = a.build_request("query", {"workshop": "装配车间"})
        assert "/HRIS/EmpList" in req["path"]
        assert req["method"] == "GET"

    def test_material_query(self):
        a = _adapter("Material")
        req = a.build_request("query", {"type": "原材料"})
        assert "/MaterialExtend" in req["path"]

    def test_quality_check_query_and_record(self):
        a = _adapter("QualityCheck")
        req = a.build_request("query", {})
        assert "/PqcRecord" in req["path"]

        req = a.build_request("record", {"id": "QC-001", "qualifiedQty": 100, "scrapQty": 5})
        assert req["method"] == "POST"

    def test_work_center_query(self):
        a = _adapter("WorkCenter")
        req = a.build_request("query", {})
        assert "/WorkCenter/getPages" in req["path"]

    def test_line_stock_inventory_query(self):
        a = _adapter("LineStockInventory")
        req = a.build_request("query", {})
        assert "/Stock/getStockPages" in req["path"]

    def test_line_stock_transaction_query_and_create(self):
        a = _adapter("LineStockTransaction")
        req = a.build_request("query", {})
        assert "/getInOutStockPages" in req["path"]

        req = a.build_request("create", {"materialCode": "M001", "qty": 50})
        assert req["method"] == "POST"


# ═══════════════════════════════════════════════════════════════════
# Mould / Tooling 适配器
# ═══════════════════════════════════════════════════════════════════

class TestMouldToolingAdapters:
    """模具/工装适配器 — 使用真实 MES 端点"""

    def test_mould_query(self):
        a = _adapter("Mould")
        req = a.build_request("query", {})
        assert "/GetActiveMoulds" in req["path"]
        assert req["method"] == "GET"

    def test_mould_assign(self):
        a = _adapter("Mould")
        req = a.build_request("assign", {"id": "M-001", "equipmentId": "EQ-001"})
        assert "/saveMouldStation" in req["path"]
        assert req["method"] == "POST"

    def test_mould_return(self):
        a = _adapter("Mould")
        req = a.build_request("returnMould", {"id": "M-001"})
        assert "/DownRecordMould" in req["path"]

    def test_tooling_query(self):
        a = _adapter("Tooling")
        req = a.build_request("query", {})
        assert "/RecordTool" in req["path"]

    def test_tooling_assign(self):
        a = _adapter("Tooling")
        req = a.build_request("assign", {"id": "T-001"})
        assert "/saveToolingStation" in req["path"]


# ═══════════════════════════════════════════════════════════════════
# ProcessFlowCard 适配器
# ═══════════════════════════════════════════════════════════════════

class TestProcessFlowCardAdapter:
    """流转卡适配器"""

    def test_query(self):
        a = _adapter("ProcessFlowCard")
        req = a.build_request("query", {})
        assert "/ProcessFlowCard" in req["path"]
        assert req["method"] == "GET"

    def test_create(self):
        a = _adapter("ProcessFlowCard")
        req = a.build_request("create", {"workOrderId": "WO-001", "cardId": "CARD-001"})
        assert "/createProcessFlow" in req["path"]
        assert req["method"] == "POST"

    def test_complete(self):
        a = _adapter("ProcessFlowCard")
        req = a.build_request("complete", {"id": "FC-001"})
        assert "/processFlowEnd" in req["path"]


# ═══════════════════════════════════════════════════════════════════
# 适配器注册完整性
# ═══════════════════════════════════════════════════════════════════

class TestAdapterRegistry:
    """验证所有 24 个适配器正常注册"""

    ALL_CONCEPTS = [
        "WorkOrder", "WorkOrderTask", "Equipment", "QualityCheck",
        "Employee", "WorkStation", "Material", "WorkCenter",
        "LineStockInventory", "LineStockTransaction",
        "BOM", "BOMItem", "WorkOrderBOM", "WorkOrderBOMItem",
        "ProcessRouting", "ProcessOperation", "ProcessCard",
        "ProductionPreparation", "InspectionPoint", "QualityDefect",
        "LineStockWarehouse", "Mould", "Tooling", "ProcessFlowCard",
    ]

    def test_all_24_adapters_registered(self):
        _ensure_registered()
        for concept in self.ALL_CONCEPTS:
            cls = get_adapter_class(concept)
            assert cls is not None, f"未注册: {concept}"

    def test_all_adapters_build_query_request(self):
        _ensure_registered()
        for concept in self.ALL_CONCEPTS:
            a = _adapter(concept)
            req = a.build_request("query", {"id": "TEST"})
            assert "path" in req
            assert "method" in req
            assert req["path"] != "", f"{concept}.query 返回空路径"

    @pytest.mark.parametrize("concept", ALL_CONCEPTS)
    def test_all_adapters_parse_success_response(self, concept):
        a = _adapter(concept)
        result = a.parse_response("query", {"id": "123", "success": True})
        assert result.get("success", True) is True
