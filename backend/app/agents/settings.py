"""
Agent 业务逻辑配置中心 — 硬编码，Git 管理

本文件集中管理所有 Agent 运行时涉及的常量、阈值、映射表和模板。
与 agent_config.py 的分工：
  - agent_config.py → Agent 元数据（显示名、图标、颜色、关键词、排序）
  - settings.py     → 业务逻辑常量（协作词、复杂度、评估、规划、护栏等）

新增 Agent 或新增业务规则时，优先在此文件中添加配置，
避免在 agent 代码里散放硬编码字符串和数字。

按功能分区，每个区域用分隔注释标明：
  ├── 协作触发
  ├── 查询复杂度 & 模型选择
  ├── 审批流
  ├── 评估系统
  ├── Planner 规划
  ├── 安全护栏
  ├── 重试配置
  ├── 实体提取映射
  ├── 工位 / 生产准备
  ├── 协作显示限制
  ├── 企业查询
  ├── 评估提示词
  └── 反馈
"""

# ==============================================================================
# 协作触发关键词
# ==============================================================================
# collaborator.py / general.py 使用：判断是否需要多 Agent 协作。
# 两组关键词，匹配任一即触发协作模式。
# ==============================================================================

# 显式协作关键词：用户明确要求"整体查一下"、"汇总"等 → 直接触发协作
COLLABORATION_KEYWORDS = [
    "整体情况", "综合分析", "全面", "协作", "全部查一下", "汇总",
    "总体", "全局", "综合一下", "所有", "全部", "汇总一下",
]

# 隐式协作关键词：用户问"生产线怎么样？"、"今天情况如何？"
# 这类宽泛问题 → 需要多个 Agent 联合回答
IMPLICIT_COLLAB_KEYWORDS = [
    "生产线", "产线", "车间", "工厂",
    "今天", "今日", "目前", "现在", "当前状况", "当前情况",
    "怎么样", "什么状况", "情况如何", "生产情况", "运行状况",
    "运营", "概览", "看板",
]

# ==============================================================================
# 协作 Agent 领域查询模板
# ==============================================================================
# collaborator.py / general.py 使用：当触发协作时，将用户消息转换为
# 每个 Agent 的具体查询语句。key 为 Agent 名，value 为拼接后发给该 Agent 的查询。
# ==============================================================================

COLLAB_DOMAIN_QUERIES = {
    "scheduling":      "查询当前排产计划和产能情况",
    "equipment":       "查询设备运行状态和故障信息",
    "quality":         "质量概况和合格率",
    "inventory":       "查询物料库存和齐套情况",
    "process":         "查询工艺路线和参数",
    "production_prep": "查询生产准备检查情况",
    "monitor":         "查询 KPI 目标达成情况与趋势",
}
# 注：andon/workstation 通过关键词匹配仍可触发定向协作，不纳入默认列表

# ==============================================================================
# 查询复杂度评分 & 模型选择
# ==============================================================================
# router.py 使用：根据用户消息的关键词和长度综合打分，
# 自动选择最合适的模型，节省成本和响应时间。
#
# 评分逻辑：
#   1. 长度分：消息越短越简单
#   2. 关键词分：命中 COMPLEXITY_KEYWORDS 则加分
#   3. 多领域加分：命中 2+ 不同领域 → +3 分
#   4. 最终得分截断到 [1, 10]
#   5. <=3 → qwen-turbo（快速、便宜）
#      4-6 → 默认模型
#      >6  → qwen3.6-plus（更强、更贵）
# ==============================================================================

# 关键词 → 复杂度分数映射。分数越高，表示该词代表的查询越复杂。
# 3分 = 高复杂度（协作类）、2分 = 中复杂度（多领域）、1分 = 低复杂度（简单查询）
COMPLEXITY_KEYWORDS = {
    "协作": 3, "综合": 3, "分析": 3, "全面": 3,
    "排产": 2, "设备": 2, "质检": 2, "物料": 2, "库存": 2,
    "计划": 2, "产线": 2, "工单": 2, "产能": 2,
    "为什么": 2, "如何": 2, "建议": 2, "优化": 2,
    "是什么": 1, "多少": 1, "查一下": 1, "状态": 1,
}

# 文本长度阈值：低于 short 加分少，高于 long 加分多
COMPLEXITY_LENGTH_THRESHOLDS = {"short": 20, "long": 50}

# 当消息命中 2 个及以上不同领域时的额外加分值
COMPLEXITY_MULTI_DOMAIN_BONUS = 3

# 最终得分合法范围
COMPLEXITY_RANGE = (1, 10)

# 模型选择阈值：得分落在哪个区间就选对应模型
# simple_max  → 得分 <= 3  选简单模型
# medium_max  → 得分 <= 6  选默认模型
#             → 得分 >  6  选复杂模型
MODEL_SELECTION_THRESHOLDS = {
    "simple_max": 3,   # <=3 → turbo
    "medium_max": 6,   # 4-6 → default
}

# 得分区间 → 实际模型名的映射
MODEL_SELECTION_MAP = {
    "simple":  "qwen-turbo",
    "complex": "qwen3.6-plus",
}

# ==============================================================================
# 审批流配置
# ==============================================================================
# collaborator.py / base.py 使用：标记需要用户确认后才能执行的操作。
# 每个操作包含显示名称和风险等级（high / medium / low）。
# 前端可根据 risk 级别展示不同强度的确认弹窗。
# ==============================================================================

REQUIRES_APPROVAL = {
    "andon_stop_line": {
        "name": "停线操作",
        "risk": "high",     # 高风险 → 必须审批
    },
    "andon_escalate": {
        "name": "安灯升级",
        "risk": "medium",   # 中风险 → 需审批
    },
    "schedule_change": {
        "name": "排产变更",
        "risk": "high",     # 高风险 → 必须审批
    },
    "wo_start": {
        "name": "工单开工",
        "risk": "medium",
    },
    "andon_create": {
        "name": "创建安灯报警",
        "risk": "medium",
    },
    "ws_fa_confirm": {
        "name": "首件确认",
        "risk": "medium",
    },
    "ws_self_inspect": {
        "name": "质量自检",
        "risk": "low",
    },
    "wo_complete": {
        "name": "工单完工报工",
        "risk": "medium",
    },
}

# ==============================================================================
# 评估系统 — 排产优化评估标准
# ==============================================================================
# evaluation.py 使用：对排产优化方案进行多维度评分。
# 每个维度有：
#   name        str  维度名称（前端显示用）
#   weight      float  权重，所有维度权重之和 = 1.0
#   target_gt   float  目标下限（> 该值为达标）
#   target_lte  float  目标上限（<= 该值为达标）
#   target_label str  人类可读的目标描述
# ==============================================================================

EVALUATION_CRITERIA = {
    "scheduling": [
        {
            "name": "产线平衡率",
            "weight": 0.30,
            "target_gt": 85,
            "target_label": ">85%",
        },
        {
            "name": "设备利用率",
            "weight": 0.25,
            "target_gt": 80,
            "target_label": ">80%",
        },
        {
            "name": "交期达成率",
            "weight": 0.25,
            "target_gt": 95,
            "target_label": ">95%",
        },
        {
            "name": "换线次数",
            "weight": 0.10,
            "target_lte": 3,
            "target_label": "<=3次/天",
        },
        {
            "name": "在制品数量",
            "weight": 0.10,
            "target_lte": 50,
            "target_label": "最小化",
        },
    ],
}

# 各维度的"优秀"和"良好"分数线，1-5 分制
# 用于将原始指标值映射为评分
EVAL_SCORE_THRESHOLDS = {
    "balance_rate":       {"excellent": 85, "good": 75},
    "equipment_utilization": {"excellent": 80, "good": 70},
    "delivery_rate":      {"excellent": 95, "good": 85},
    "changeovers":        {"excellent": 3, "good": 5},
    "wip_count":          {"excellent": 50, "good": 100},
}

# 排产优化总分阈值：低于此分时，evaluation.py 触发"需要优化"路径
EVAL_OPTIMIZATION_THRESHOLD = 4.0  # 1-5 分制，低于 4 分需要优化

# ==============================================================================
# Planner 规划任务定义
# ==============================================================================
# planner.py 使用：将生产准备场景动态拆分为可独立执行的任务清单。
#
# AVAILABLE_TASKS 结构：
#   key (str): 任务标识，必须与 tools 中的函数名对应
#   name (str): 中文显示名
#   keywords (list): 命中这些关键词的任务会被优先选择
#   priority (int): 优先级，数值越小越先执行
#                    0 = 最高优先级（综合检查优先）
#
# FALLBACK_TASKS: 当用户消息未命中任何关键词时，兜底执行的基础检查
# ==============================================================================

AVAILABLE_TASKS = {
    # 综合工单检查：最高优先级，一次性检查所有子项
    "work_order": {
        "name": "工单综合检查",
        "keywords": ["齐套检查", "准备检查", "投产准备", "工单准备", "全面", "综合"],
        "priority": 0,
    },
    # 物料齐套：检查 BOM 清单与线边仓库存
    "material": {
        "name": "物料齐套检查",
        "keywords": ["物料", "齐套", "缺料", "BOM", "库存"],
        "priority": 1,
    },
    # 设备状态：确认设备是否在线、是否有点检记录
    "equipment": {
        "name": "设备状态确认",
        "keywords": ["设备", "点检", "状态", "OEE"],
        "priority": 2,
    },
    # 模具准备：检查模具/治具/钢网是否齐备
    "mold": {
        "name": "模具准备检查",
        "keywords": ["模具", "治具", "钢网"],
        "priority": 3,
    },
    # 质检标准：获取产品质检要求、良率目标
    "quality": {
        "name": "质检标准查询",
        "keywords": ["质检", "检验", "标准", "良率", "SPC"],
        "priority": 4,
    },
    # SOP 查询：获取工序对应的作业指导书
    "sop": {
        "name": "SOP 查询",
        "keywords": ["SOP", "作业指导", "操作规程"],
        "priority": 5,
    },
    # 工艺卡：获取工艺参数（炉温、链速等）
    "process_card": {
        "name": "工艺卡配置",
        "keywords": ["工艺卡", "工艺参数", "炉温", "链速"],
        "priority": 6,
    },
}

# 用户消息未命中任何关键词时，默认执行的基础检查任务
FALLBACK_TASKS = [
    {"key": "material", "name": "物料齐套检查", "priority": 1},
    {"key": "equipment", "name": "设备状态确认", "priority": 2},
]

# ==============================================================================
# 安全护栏
# ==============================================================================
# guardrails.py 使用：在消息进入 LLM 前后进行安全检查。
#   输入检查：长度限制 + 敏感内容过滤（SQL 注入、XSS）
#   输出检查：长度限制 + 基本结构验证
# ==============================================================================

GUARDRAILS = {
    "max_input_length": 5000,    # 输入消息最大字符数，超长拒绝
    "min_input_length": 1,       # 最小字符数，空消息拒绝
    "max_output_length": 20000,  # LLM 输出最大字符数，超长截断
    # 输入敏感模式正则列表：命中则拒绝，防止注入攻击
    "sensitive_patterns": [
        r'(?:sql|delete|drop|truncate|alter)\s+(?:from|table|database)',  # SQL 注入
        r'<script[^>]*>.*?</script>',                                     # XSS 攻击
        r'(?:javascript|vbscript)\s*:',                                   # JS 协议注入
    ],
}

# ==============================================================================
# 工具安全分级注册表
# ==============================================================================
# guardrails.py / safe_tool_call() 使用：所有 Agent 工具函数的安全分级。
#   READ           — 只读查询，直接执行，无审计
#   WRITE_AUDIT    — 写入操作，执行 + 审计日志（不阻塞操作员）
#   WRITE_APPROVE  — 写入操作，需审批弹窗确认后执行 + 审计
#   CRITICAL       — 破坏性操作，需审批 + 审计（如停线）
# ==============================================================================

TOOL_SAFETY = {
    # ── READ ──
    "query_schedule":           {"risk": "READ", "agent": "scheduling"},
    "query_capacity":           {"risk": "READ", "agent": "scheduling"},
    "suggest_schedule":         {"risk": "READ", "agent": "scheduling"},
    "optimize_schedule":        {"risk": "READ", "agent": "scheduling"},
    "query_quality_report":     {"risk": "READ", "agent": "quality"},
    "query_quality_summary":    {"risk": "READ", "agent": "quality"},
    "analyze_defects":          {"risk": "READ", "agent": "quality"},
    "query_checkpoints":        {"risk": "READ", "agent": "quality"},
    "query_equipment":          {"risk": "READ", "agent": "equipment"},
    "query_equipment_summary":  {"risk": "READ", "agent": "equipment"},
    "diagnose_fault":           {"risk": "READ", "agent": "equipment"},
    "query_inventory":          {"risk": "READ", "agent": "inventory"},
    "query_inventory_summary":  {"risk": "READ", "agent": "inventory"},
    "check_shortage":           {"risk": "READ", "agent": "inventory"},
    "query_process_route":      {"risk": "READ", "agent": "process"},
    "query_process_params":     {"risk": "READ", "agent": "process"},
    "suggest_optimization":     {"risk": "READ", "agent": "process"},
    "check_material_readiness": {"risk": "READ", "agent": "production_prep"},
    "check_equipment_readiness":{"risk": "READ", "agent": "production_prep"},
    "check_mold_readiness":     {"risk": "READ", "agent": "production_prep"},
    "query_quality_standard":   {"risk": "READ", "agent": "production_prep"},
    "query_sop":                {"risk": "READ", "agent": "production_prep"},
    "query_process_card":       {"risk": "READ", "agent": "production_prep"},
    "check_quality_checkpoints":{"risk": "READ", "agent": "production_prep"},
    "check_work_order_readiness":{"risk": "READ", "agent": "production_prep"},
    "get_workstation_info":     {"risk": "READ", "agent": "workstation"},
    "get_current_work_order":   {"risk": "READ", "agent": "workstation"},
    "query_sop_ws":             {"risk": "READ", "agent": "workstation"},
    "query_process_params_ws":  {"risk": "READ", "agent": "workstation"},
    "check_material_status":    {"risk": "READ", "agent": "workstation"},
    "query_active_andons":      {"risk": "READ", "agent": "andon"},
    "query_andon_history":      {"risk": "READ", "agent": "andon"},
    "get_andon_stats":          {"risk": "READ", "agent": "andon"},
    "query_kpi_targets":        {"risk": "READ", "agent": "monitor"},
    "query_kpi_actuals":        {"risk": "READ", "agent": "monitor"},
    "query_kpi_summary":        {"risk": "READ", "agent": "monitor"},
    "query_kpi_trend":          {"risk": "READ", "agent": "monitor"},

    # ── WRITE_AUDIT ──
    "report_production":        {"risk": "WRITE_AUDIT", "agent": "workstation",
                                 "action_name": "产量上报", "action_key": "ws_report_prod"},
    "request_material":         {"risk": "WRITE_AUDIT", "agent": "workstation",
                                 "action_name": "领料申请", "action_key": "ws_request_mat"},
    "report_abnormal":          {"risk": "WRITE_AUDIT", "agent": "workstation",
                                 "action_name": "异常上报", "action_key": "ws_abnormal"},
    "operator_signin":          {"risk": "WRITE_AUDIT", "agent": "workstation",
                                 "action_name": "人员签到", "action_key": "ws_signin"},
    "equipment_check":          {"risk": "WRITE_AUDIT", "agent": "workstation",
                                 "action_name": "设备点检", "action_key": "ws_equip_check"},

    # ── WRITE_APPROVE ──
    "start_work_order":         {"risk": "WRITE_APPROVE", "agent": "workstation",
                                 "action_name": "工单开工", "action_key": "wo_start"},
    "complete_work_order":      {"risk": "WRITE_APPROVE", "agent": "workstation",
                                 "action_name": "工单完工报工", "action_key": "wo_complete"},
    "create_andon_alert":       {"risk": "WRITE_APPROVE", "agent": "andon",
                                 "action_name": "创建安灯报警", "action_key": "andon_create"},
    "escalate_andon":           {"risk": "WRITE_APPROVE", "agent": "andon",
                                 "action_name": "安灯升级", "action_key": "andon_escalate"},
    "first_article_confirm":    {"risk": "WRITE_APPROVE", "agent": "workstation",
                                 "action_name": "首件确认", "action_key": "ws_fa_confirm"},
    "self_inspection":          {"risk": "WRITE_APPROVE", "agent": "workstation",
                                 "action_name": "质量自检", "action_key": "ws_self_inspect"},

    # ── CRITICAL ──
    "handle_line_stop":         {"risk": "CRITICAL", "agent": "andon",
                                 "action_name": "停线操作", "action_key": "andon_stop_line"},
}

# ==============================================================================
# 推理技术配置
# ==============================================================================
# equipment.py / quality.py 使用：控制结构化推理步骤的生成。
# 为故障诊断和根因分析提供分步推理框架（Observe→Diagnose→Cross-check→Recommend）。

REASONING_CONFIG = {
    # 是否启用结构化推理步骤（emit reasoning_step SSE 事件）
    "enabled": True,
    # 推理步骤的默认最大数量
    "max_steps": 4,
    # 设备故障诊断推理步骤定义
    "equipment_diagnosis_steps": [
        {"key": "observe",    "label": "症状观察",   "icon": "🔍"},
        {"key": "diagnose",   "label": "根因诊断",   "icon": "🔬"},
        {"key": "crosscheck", "label": "交叉验证",   "icon": "🔗"},
        {"key": "recommend",  "label": "修复建议",   "icon": "✅"},
    ],
    # 质量根因分析推理步骤定义
    "quality_root_cause_steps": [
        {"key": "identify",   "label": "缺陷识别",   "icon": "📊"},
        {"key": "classify",   "label": "4M1E 分类", "icon": "🏷️"},
        {"key": "rootcause",  "label": "5-Why 追溯", "icon": "🎯"},
        {"key": "recommend",  "label": "改善措施",   "icon": "✅"},
    ],
    # 深度思考自动触发：这些关键词出现时 force enable_thinking=True
    "auto_think_keywords": {
        "equipment": ["故障", "诊断", "停机", "异常", "影响"],
        "quality":   ["缺陷", "不良", "根因", "分析", "改善"],
    },
}

# ==============================================================================
# 审计日志配置
# ==============================================================================
# guardrails.py / AuditLogger 使用：控制审计行为。

AUDIT_CONFIG = {
    "enabled": True,
    "log_file": "logs/audit.log",
    "retention_days": 90,
    "log_full_args": False,
    "log_full_result": False,
}

# ==============================================================================
# 重试与超时配置
# ==============================================================================
# base.py 使用：工具调用失败时的自动重试策略。
# ==============================================================================

RETRY_CONFIG = {
    "max_retries": 2,         # 最大重试次数（总共最多尝试 3 次：1 原始 + 2 重试）
    "empty_result_delay": 0.5,  # 工具返回空值后重试的等待时间（秒）
    "exception_delay": 1.0,   # 工具抛异常后重试的等待时间（秒）
    "exponential_backoff_base": 0.5,  # 指数退避基础延迟（秒）
    "exponential_backoff_max": 8.0,   # 指数退避最大延迟（秒）
    "use_exponential_backoff": True,  # 是否启用指数退避（False = 固定延迟）
}

# ==============================================================================
# 熔断器配置
# ==============================================================================
# error_handler.py 使用：连续失败 N 次后熔断，冷却后进入半开试探。

CIRCUIT_BREAKER_CONFIG = {
    "failure_threshold": 5,     # 连续失败 N 次后熔断
    "cooldown_seconds": 30.0,   # 熔断后冷却时间（秒）
    "half_open_limit": 1,       # 半开状态下最多允许的试探请求数
}

# ==============================================================================
# 实体提取映射
# ==============================================================================
# andon.py / andon_tools.py 使用：
# 从用户消息中提取安灯类型和升级级别。
# ==============================================================================

# 安灯异常类型映射。匹配顺序为顺序遍历，第一个命中即返回。
ANDON_TYPE_MAP = {
    "物料": "物料",
    "设备": "设备",
    "质量": "质量",
    "工艺": "工艺",
}
# 未命中任何关键词时的默认异常类型
DEFAULT_ANDON_TYPE = "设备"

# 安灯升级级别映射。用户说"经理"、"生产经理"都映射为 manager
ESCALATION_LEVEL_MAP = {
    "经理": "manager",
    "生产经理": "manager",
    "总监": "director",
    "生产总监": "director",
    "副总": "vp",
    "生产副总": "vp",
}
# 未指定升级级别时的默认级别（线长，即不升级）
DEFAULT_ESCALATION_LEVEL = "线长"

# 升级级别的中文显示名，用于前端提示
ESCALATION_LEVEL_DISPLAY = {
    "manager":  "生产经理",
    "director": "生产总监",
    "vp":       "生产副总",
}

# ==============================================================================
# 安灯升级 / 停线反思关键词
# ==============================================================================
# quality.py / scheduling.py 的 reflect() 方法使用。
# 用于检查 AI 生成的响应中是否包含"可操作的改进建议"。
# ==============================================================================

REFLECTION_ACTIONABLE_KEYWORDS = {
    # 质检反思：响应中应包含建议、改进、优化方向等关键词
    "quality": ["建议", "改进", "优化", "原因", "措施"],
    # 排产反思：需要更复杂的结构
    "scheduling": {
        # 排产响应中必须包含产线或工单标识
        "response_check": ["SMT", "DIP", "组装", "产线", "WO-", "工单"],
        # 排产建议中必须包含推荐性措辞
        "suggestion_check": ["建议", "推荐", "可以", "优先"],
    },
}

# ==============================================================================
# 工位 / 生产准备提取关键词
# ==============================================================================
# workstation.py / production_prep.py 使用：
# 用于正则提取工序名、班次、异常类型等信息。
# ==============================================================================

# 工序名称列表，用于从消息中提取工序关键词
# 匹配顺序：遍历列表，第一个命中即返回
PROCESS_KEYWORDS = ["SMT", "DIP", "组装", "贴片", "插件", "波峰焊", "回流焊"]

# 班次类型列表
SHIFT_TYPES = ["白班", "夜班", "中班"]

# 工位异常类型列表
ABNORMAL_TYPES = ["质量异常", "设备异常", "物料异常"]

# 未指定异常类型时的默认值
DEFAULT_ABNORMAL_TYPE = "其他异常"

# 自检检查项 — 质量维度
INSPECTION_ITEMS_QUALITY = ["外观", "尺寸", "功能"]

# 自检检查项 — 设备维度
INSPECTION_ITEMS_EQUIPMENT = ["设备运行状态", "安全防护", "环境参数"]

# ==============================================================================
# 协作数据显示限制
# ==============================================================================
# general.py / collaborator.py 使用：控制协作模式下各 Agent 结果
# 在前端的显示长度，避免超长响应影响用户体验。
# ==============================================================================

COLLAB_DISPLAY_LIMITS = {
    "max_result_preview": 500,   # 单个 Agent 结果最大预览字符数
    "max_schedule_items": 5,     # 故障诊断场景下最多显示 5 条排产信息
}

# 并行执行超时配置（ParallelExecutor 使用）
COLLAB_TIMEOUT = {
    "per_task": 10.0,            # 单个 Agent 工具调用超时（秒）
    "total_timeout": 45.0,       # 整个批次总超时（秒）
}

# ==============================================================================
# 企业查询意图模式
# ==============================================================================
# general.py 使用：从用户消息中提取企业名称。
# 正则匹配"查询企业XXX"、"XXX公司信息"等模式。
# ==============================================================================

ENTERPRISE_QUERY_PATTERNS = [
    r'查询企业(.+)', r'查找企业(.+)', r'搜索企业(.+)',
    r'企业查询(.+)', r'(.+)企业信息', r'(.+)工商信息',
    r'(.+)的工商信息', r'了解(.+)公司', r'(.+)公司信息',
    r'查一下(.+)公司',
]

# ==============================================================================
# 评估系统提示词
# ==============================================================================
# evaluation.py 使用：发送给 LLM 的评估指令，要求 AI 对响应进行
# 多维度打分（准确性、完整性、相关性、可读性）。
# ==============================================================================

EVAL_SYSTEM_PROMPT = """你是一个 AI 响应质量评估器。请从以下维度评估给定的响应：
1. **准确性**：回答是否准确、无幻觉
2. **完整性**：是否覆盖了用户问题的所有方面
3. **相关性**：是否与问题直接相关
4. **可读性**：结构是否清晰、语言是否通顺

请以 JSON 格式返回评估结果：
{"accuracy": 1-5, "completeness": 1-5, "relevance": 1-5, "readability": 1-5, "overall": 1-5, "reason": "评估理由"}"""

# ==============================================================================
# 反馈评分范围
# ==============================================================================
# eval API 使用：用户反馈评分的合法范围。
# 用于校验前端提交的 feedback 值是否在有效区间内。
# ==============================================================================

FEEDBACK_SCORE_RANGE = (1, 5)  # 最小 1 分，最大 5 分

# ==============================================================================
# 制造 KPI 目标注册表
# ==============================================================================
# monitor_tools.py / MonitorAgent 使用：定义各领域的 KPI 目标值和告警阈值。
# 每项 KPI 包含：target（目标值）、unit（单位）、direction（优化方向）、
# warning_threshold（黄色告警）、critical_threshold（红色告警）、domain（所属领域）。
# ==============================================================================

MANUFACTURING_KPIS = {
    # ── 设备领域 ──
    "oee": {
        "name": "OEE 设备综合效率",
        "target": 85.0, "unit": "%", "direction": "higher_better",
        "warning_threshold": 75.0, "critical_threshold": 65.0,
        "domain": "equipment",
    },
    "equipment_uptime": {
        "name": "设备开机率",
        "target": 95.0, "unit": "%", "direction": "higher_better",
        "warning_threshold": 90.0, "critical_threshold": 85.0,
        "domain": "equipment",
    },
    "mtbf": {
        "name": "平均故障间隔 (MTBF)",
        "target": 200.0, "unit": "小时", "direction": "higher_better",
        "warning_threshold": 120.0, "critical_threshold": 80.0,
        "domain": "equipment",
    },
    "mttr": {
        "name": "平均修复时间 (MTTR)",
        "target": 30.0, "unit": "分钟", "direction": "lower_better",
        "warning_threshold": 60.0, "critical_threshold": 90.0,
        "domain": "equipment",
    },

    # ── 质量领域 ──
    "yield_rate": {
        "name": "一次合格率",
        "target": 98.0, "unit": "%", "direction": "higher_better",
        "warning_threshold": 96.0, "critical_threshold": 94.0,
        "domain": "quality",
    },
    "defect_rate": {
        "name": "不良率",
        "target": 2.0, "unit": "%", "direction": "lower_better",
        "warning_threshold": 5.0, "critical_threshold": 8.0,
        "domain": "quality",
    },
    "cpk": {
        "name": "过程能力指数 (Cpk)",
        "target": 1.33, "unit": "", "direction": "higher_better",
        "warning_threshold": 1.0, "critical_threshold": 0.67,
        "domain": "quality",
    },

    # ── 排产领域 ──
    "delivery_rate": {
        "name": "交期达成率",
        "target": 95.0, "unit": "%", "direction": "higher_better",
        "warning_threshold": 90.0, "critical_threshold": 85.0,
        "domain": "scheduling",
    },
    "balance_rate": {
        "name": "产线平衡率",
        "target": 85.0, "unit": "%", "direction": "higher_better",
        "warning_threshold": 75.0, "critical_threshold": 65.0,
        "domain": "scheduling",
    },
    "changeover_time": {
        "name": "平均换线时间",
        "target": 30.0, "unit": "分钟", "direction": "lower_better",
        "warning_threshold": 45.0, "critical_threshold": 60.0,
        "domain": "scheduling",
    },

    # ── 库存领域 ──
    "inventory_turnover": {
        "name": "库存周转率",
        "target": 12.0, "unit": "次/月", "direction": "higher_better",
        "warning_threshold": 8.0, "critical_threshold": 5.0,
        "domain": "inventory",
    },
    "shortage_rate": {
        "name": "缺料率",
        "target": 0.5, "unit": "%", "direction": "lower_better",
        "warning_threshold": 2.0, "critical_threshold": 5.0,
        "domain": "inventory",
    },

    # ── 安灯领域 ──
    "andon_response_time": {
        "name": "安灯平均响应时间",
        "target": 5.0, "unit": "分钟", "direction": "lower_better",
        "warning_threshold": 10.0, "critical_threshold": 15.0,
        "domain": "andon",
    },
    "andon_resolve_time": {
        "name": "安灯平均解决时间",
        "target": 30.0, "unit": "分钟", "direction": "lower_better",
        "warning_threshold": 60.0, "critical_threshold": 90.0,
        "domain": "andon",
    },

    # ── 生产领域 ──
    "production_output": {
        "name": "产线日产出",
        "target": 1000.0, "unit": "件/天", "direction": "higher_better",
        "warning_threshold": 850.0, "critical_threshold": 700.0,
        "domain": "production",
    },
}

# KPI 状态计算函数：根据实际值 vs 目标/阈值返回状态
def get_kpi_status(kpi_key: str, actual_value: float) -> str:
    """根据 KPI 实际值返回状态：on_track / warning / critical"""
    kpi = MANUFACTURING_KPIS.get(kpi_key)
    if not kpi:
        return "unknown"
    direction = kpi["direction"]
    warn = kpi["warning_threshold"]
    crit = kpi["critical_threshold"]
    target = kpi["target"]

    if direction == "higher_better":
        if actual_value >= target:
            return "on_track"
        elif actual_value >= warn:
            return "warning"
        elif actual_value >= crit:
            return "critical"
        else:
            return "critical"
    else:  # lower_better
        if actual_value <= target:
            return "on_track"
        elif actual_value <= warn:
            return "warning"
        elif actual_value <= crit:
            return "critical"
        else:
            return "critical"
