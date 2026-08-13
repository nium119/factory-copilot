"""资源感知优化 — Resource-Aware Optimization"""

# resource_monitor.py / parallel_executor.py / general.py / router.py 使用。
# 基于系统负载动态调整并发度、模型选择和 API 调用频率。
#
# ResourceTier 四级：
#   OPTIMAL     — 资源充裕，无限流
#   NORMAL      — 正常负载，默认并发限制
#   CONSTRAINED — 资源紧张，降低并发 + 切换到预算模型
#   CRITICAL    — 资源严重不足，严格限流 + 强制预算模型

RESOURCE_THRESHOLDS = {
    "max_concurrent_requests": 10,
    "constrained_at": 6,
    "critical_at": 9,
    "max_api_calls_per_minute": 30,
    "token_budget_per_hour": 500000,
    # Agent 分析预算（DynamicPlanner 可靠性强化，前端「资源阈值」面板可调）
    "planner_max_steps": 6,        # 单次智能分析最大步骤数（替代硬编码 MAX_STEPS=6）
    "planner_time_budget_s": 60,   # 单次分析执行时间预算（秒），超限强制汇总
    "planner_max_llm_calls": 12,   # 单次分析 LLM 调用上限（计划/评审/填槽/反思/汇总合计）
    "planner_summary_max_chars": 1500,  # 汇总报告字数上限（控制输出长度 → 汇总耗时），前端「资源阈值」可调
}

RESOURCE_TIER_CONCURRENCY = {
    "optimal": 0,        # 0 = unlimited
    "normal": 6,
    "constrained": 3,
    "critical": 1,
}

MODEL_COST_TIERS = {
    "budget": "qwen-turbo",
    "standard": "qwen-plus",
    "premium": "qwen3.6-plus",
}
