"""推理配置 + 重试策略 + 熔断器"""

# ==============================================================================
# 推理技术配置
# ==============================================================================
# equipment.py / quality.py 使用：控制结构化推理步骤的生成。

REASONING_CONFIG = {
    "enabled": True,
    "max_steps": 4,
    "equipment_diagnosis_steps": [
        {"key": "observe",    "label": "症状观察", "icon": "🔍"},
        {"key": "diagnose",   "label": "根因诊断", "icon": "🔬"},
        {"key": "crosscheck", "label": "交叉验证", "icon": "🔗"},
        {"key": "recommend",  "label": "修复建议", "icon": "✅"},
    ],
    "quality_root_cause_steps": [
        {"key": "identify",   "label": "缺陷识别", "icon": "📊"},
        {"key": "classify",   "label": "4M1E 分类", "icon": "🏷️"},
        {"key": "rootcause",  "label": "5-Why 追溯", "icon": "🎯"},
        {"key": "recommend",  "label": "改善措施", "icon": "✅"},
    ],
    "auto_think_keywords": {
        "equipment": ["故障", "诊断", "停机", "异常", "影响"],
        "quality":   ["缺陷", "不良", "根因", "分析", "改善"],
    },
}

# ==============================================================================
# 重试与超时配置
# ==============================================================================
# base.py 使用：工具调用失败时的自动重试策略。

RETRY_CONFIG = {
    "max_retries": 2,
    "empty_result_delay": 0.5,
    "exception_delay": 1.0,
    "exponential_backoff_base": 0.5,
    "exponential_backoff_max": 8.0,
    "use_exponential_backoff": True,
}

# ==============================================================================
# 熔断器配置
# ==============================================================================
# error_handler.py 使用：连续失败 N 次后熔断，冷却后进入半开试探。

CIRCUIT_BREAKER_CONFIG = {
    "failure_threshold": 5,
    "cooldown_seconds": 30.0,
    "half_open_limit": 1,
}
