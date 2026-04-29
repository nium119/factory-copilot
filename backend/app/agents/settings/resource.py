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
    "token_budget_per_hour": 100000,
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
