"""安全护栏 + 工具安全分级 + 审批流 + 审计配置"""

# ==============================================================================
# 审批流配置
# ==============================================================================
# base.py / 审批流使用：标记需要用户确认后才能执行的操作。
# 每个操作包含显示名称和风险等级（high / medium / low）。
# ==============================================================================

REQUIRES_APPROVAL = {
    "andon_stop_line":   {"name": "停线操作",     "risk": "high"},
    "andon_escalate":    {"name": "安灯升级",     "risk": "medium"},
    "schedule_change":   {"name": "排产变更",     "risk": "high"},
    "wo_start":          {"name": "工单开工",     "risk": "medium"},
    "andon_create":      {"name": "创建安灯报警", "risk": "medium"},
    "ws_fa_confirm":     {"name": "首件确认",     "risk": "medium"},
    "ws_self_inspect":   {"name": "质量自检",     "risk": "low"},
    "wo_complete":       {"name": "工单完工报工", "risk": "medium"},
}

# ==============================================================================
# 安全护栏
# ==============================================================================
# guardrails.py 使用：在消息进入 LLM 前后进行安全检查。
# ==============================================================================

GUARDRAILS = {
    "max_input_length": 5000,
    "min_input_length": 1,
    "max_output_length": 20000,
    "sensitive_patterns": [
        r'(?:sql|delete|drop|truncate|alter)\s+(?:from|table|database)',
        r'<script[^>]*>.*?</script>',
        r'(?:javascript|vbscript)\s*:',
    ],
}

# ==============================================================================
# 工具安全分级注册表
# ==============================================================================
# guardrails.py / safe_tool_call() 使用：所有 Agent 工具函数的安全分级。
#   READ           — 只读查询，直接执行
#   WRITE_AUDIT    — 写入操作，执行 + 审计日志
#   WRITE_APPROVE  — 写入操作，需审批弹窗确认
#   CRITICAL       — 破坏性操作，需审批 + 审计
# ==============================================================================

TOOL_SAFETY = {
    # ── READ ──
    "query_schedule":            {"risk": "READ", "agent": "scheduling"},
    "query_capacity":            {"risk": "READ", "agent": "scheduling"},
    "suggest_schedule":          {"risk": "READ", "agent": "scheduling"},
    "optimize_schedule":         {"risk": "READ", "agent": "scheduling"},
    "query_quality_report":      {"risk": "READ", "agent": "quality"},
    "query_quality_summary":     {"risk": "READ", "agent": "quality"},
    "analyze_defects":           {"risk": "READ", "agent": "quality"},
    "query_checkpoints":         {"risk": "READ", "agent": "quality"},
    "query_equipment":           {"risk": "READ", "agent": "equipment"},
    "query_equipment_summary":   {"risk": "READ", "agent": "equipment"},
    "diagnose_fault":            {"risk": "READ", "agent": "equipment"},
    "query_inventory":           {"risk": "READ", "agent": "inventory"},
    "query_inventory_summary":   {"risk": "READ", "agent": "inventory"},
    "check_shortage":            {"risk": "READ", "agent": "inventory"},
    "query_process_route":       {"risk": "READ", "agent": "process"},
    "query_process_params":      {"risk": "READ", "agent": "process"},
    "suggest_optimization":      {"risk": "READ", "agent": "process"},
    "check_material_readiness":  {"risk": "READ", "agent": "production_prep"},
    "check_equipment_readiness": {"risk": "READ", "agent": "production_prep"},
    "check_mold_readiness":      {"risk": "READ", "agent": "production_prep"},
    "query_quality_standard":    {"risk": "READ", "agent": "production_prep"},
    "query_sop":                 {"risk": "READ", "agent": "production_prep"},
    "query_process_card":        {"risk": "READ", "agent": "production_prep"},
    "check_quality_checkpoints": {"risk": "READ", "agent": "production_prep"},
    "check_work_order_readiness":{"risk": "READ", "agent": "production_prep"},
    "get_workstation_info":      {"risk": "READ", "agent": "workstation"},
    "get_current_work_order":    {"risk": "READ", "agent": "workstation"},
    "query_sop_ws":              {"risk": "READ", "agent": "workstation"},
    "query_process_params_ws":   {"risk": "READ", "agent": "workstation"},
    "check_material_status":     {"risk": "READ", "agent": "workstation"},
    "query_active_andons":       {"risk": "READ", "agent": "andon"},
    "query_andon_history":       {"risk": "READ", "agent": "andon"},
    "get_andon_stats":           {"risk": "READ", "agent": "andon"},
    "query_kpi_targets":         {"risk": "READ", "agent": "monitor"},
    "query_kpi_actuals":         {"risk": "READ", "agent": "monitor"},
    "query_kpi_summary":         {"risk": "READ", "agent": "monitor"},
    "query_kpi_trend":           {"risk": "READ", "agent": "monitor"},

    # ── WRITE_AUDIT ──
    "report_production":  {"risk": "WRITE_AUDIT", "agent": "workstation",
                           "action_name": "产量上报", "action_key": "ws_report_prod"},
    "request_material":   {"risk": "WRITE_AUDIT", "agent": "workstation",
                           "action_name": "领料申请", "action_key": "ws_request_mat"},
    "report_abnormal":    {"risk": "WRITE_AUDIT", "agent": "workstation",
                           "action_name": "异常上报", "action_key": "ws_abnormal"},
    "operator_signin":    {"risk": "WRITE_AUDIT", "agent": "workstation",
                           "action_name": "人员签到", "action_key": "ws_signin"},
    "equipment_check":    {"risk": "WRITE_AUDIT", "agent": "workstation",
                           "action_name": "设备点检", "action_key": "ws_equip_check"},

    # ── WRITE_APPROVE ──
    "start_work_order":      {"risk": "WRITE_APPROVE", "agent": "workstation",
                              "action_name": "工单开工", "action_key": "wo_start"},
    "complete_work_order":   {"risk": "WRITE_APPROVE", "agent": "workstation",
                              "action_name": "工单完工报工", "action_key": "wo_complete"},
    "create_andon_alert":    {"risk": "WRITE_APPROVE", "agent": "andon",
                              "action_name": "创建安灯报警", "action_key": "andon_create"},
    "escalate_andon":        {"risk": "WRITE_APPROVE", "agent": "andon",
                              "action_name": "安灯升级", "action_key": "andon_escalate"},
    "first_article_confirm": {"risk": "WRITE_APPROVE", "agent": "workstation",
                              "action_name": "首件确认", "action_key": "ws_fa_confirm"},
    "self_inspection":       {"risk": "WRITE_APPROVE", "agent": "workstation",
                              "action_name": "质量自检", "action_key": "ws_self_inspect"},

    # ── CRITICAL ──
    "handle_line_stop": {"risk": "CRITICAL", "agent": "andon",
                         "action_name": "停线操作", "action_key": "andon_stop_line"},
}

# ==============================================================================
# 审计日志配置
# ==============================================================================

AUDIT_CONFIG = {
    "enabled": True,
    "log_file": "logs/audit.log",
    "retention_days": 90,
    "log_full_args": False,
    "log_full_result": False,
}
