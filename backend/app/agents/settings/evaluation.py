"""评估系统 + Planner 规划任务 + 反馈"""

# ==============================================================================
# 评估系统 — 排产优化评估标准
# ==============================================================================

EVALUATION_CRITERIA = {
    "scheduling": [
        {"name": "产线平衡率", "weight": 0.30, "target_gt": 85, "target_label": ">85%"},
        {"name": "设备利用率", "weight": 0.25, "target_gt": 80, "target_label": ">80%"},
        {"name": "交期达成率", "weight": 0.25, "target_gt": 95, "target_label": ">95%"},
        {"name": "换线次数",   "weight": 0.10, "target_lte": 3, "target_label": "<=3次/天"},
        {"name": "在制品数量", "weight": 0.10, "target_lte": 50, "target_label": "最小化"},
    ],
}

EVAL_SCORE_THRESHOLDS = {
    "balance_rate":          {"excellent": 85, "good": 75},
    "equipment_utilization": {"excellent": 80, "good": 70},
    "delivery_rate":         {"excellent": 95, "good": 85},
    "changeovers":           {"excellent": 3, "good": 5},
    "wip_count":             {"excellent": 50, "good": 100},
}

EVAL_OPTIMIZATION_THRESHOLD = 4.0

# ==============================================================================
# 评估系统提示词
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

FEEDBACK_SCORE_RANGE = (1, 5)

# ==============================================================================
# Planner 规划任务定义
# ==============================================================================

AVAILABLE_TASKS = {
    "work_order": {
        "name": "工单综合检查",
        "keywords": ["齐套检查", "准备检查", "投产准备", "工单准备", "全面", "综合"],
        "priority": 0,
    },
    "material": {
        "name": "物料齐套检查",
        "keywords": ["物料", "齐套", "缺料", "BOM", "库存"],
        "priority": 1,
    },
    "equipment": {
        "name": "设备状态确认",
        "keywords": ["设备", "点检", "状态", "OEE"],
        "priority": 2,
    },
    "mold": {
        "name": "模具准备检查",
        "keywords": ["模具", "治具", "钢网"],
        "priority": 3,
    },
    "quality": {
        "name": "质检标准查询",
        "keywords": ["质检", "检验", "标准", "良率", "SPC"],
        "priority": 4,
    },
    "sop": {
        "name": "SOP 查询",
        "keywords": ["SOP", "作业指导", "操作规程"],
        "priority": 5,
    },
    "process_card": {
        "name": "工艺卡配置",
        "keywords": ["工艺卡", "工艺参数", "炉温", "链速"],
        "priority": 6,
    },
}

FALLBACK_TASKS = [
    {"key": "material", "name": "物料齐套检查", "priority": 1},
    {"key": "equipment", "name": "设备状态确认", "priority": 2},
]
