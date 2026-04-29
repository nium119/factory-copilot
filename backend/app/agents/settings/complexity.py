"""查询复杂度评分 & 模型选择"""

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
MODEL_SELECTION_THRESHOLDS = {
    "simple_max": 3,   # <=3 → turbo
    "medium_max": 6,   # 4-6 → default
}

# 得分区间 → 实际模型名的映射
MODEL_SELECTION_MAP = {
    "simple":  "qwen-turbo",
    "complex": "qwen3.6-plus",
}
