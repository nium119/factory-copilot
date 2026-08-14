"""Agent 业务逻辑配置中心 - 子模块索引

按功能域拆分为 7 个子模块：
  - collaboration  — 协作触发 + 领域查询 + 显示限制 + 超时
  - complexity     — 查询复杂度评分 + 模型选择
  - guardrails     — 审批流 + 安全护栏 + 工具安全分级 + 审计
  - resilience     — 推理配置 + 重试策略
  - resource       — 资源感知优化
  - domain         — 领域映射（安灯/工序/企业查询/反射）
  - model          — LLM 模型选择配置

所有符号在顶层重导出，保持向后兼容：from app.agents.settings import X
"""

from app.agents.settings.collaboration import (
    COLLAB_DISPLAY_LIMITS,
    COLLAB_DOMAIN_QUERIES,
    COLLAB_TIMEOUT,
    COLLABORATION_KEYWORDS,
    IMPLICIT_COLLAB_KEYWORDS,
)
from app.agents.settings.complexity import (
    COMPLEXITY_KEYWORDS,
    COMPLEXITY_LENGTH_THRESHOLDS,
    COMPLEXITY_MULTI_DOMAIN_BONUS,
    COMPLEXITY_RANGE,
    MODEL_SELECTION_MAP,
    MODEL_SELECTION_THRESHOLDS,
)
from app.agents.settings.domain import (
    ABNORMAL_TYPES,
    ANDON_TYPE_MAP,
    DEFAULT_ABNORMAL_TYPE,
    DEFAULT_ANDON_TYPE,
    DEFAULT_ESCALATION_LEVEL,
    ENTERPRISE_QUERY_PATTERNS,
    ESCALATION_LEVEL_MAP,
    INSPECTION_ITEMS_EQUIPMENT,
    INSPECTION_ITEMS_QUALITY,
    PROCESS_KEYWORDS,
    REFLECTION_ACTIONABLE_KEYWORDS,
    SHIFT_TYPES,
)
from app.agents.settings.guardrails import (
    AUDIT_CONFIG,
    GUARDRAILS,
    REQUIRES_APPROVAL,
    TOOL_SAFETY,
)
from app.agents.settings.resilience import (
    REASONING_CONFIG,
    RETRY_CONFIG,
)
from app.agents.settings.resource import (
    MODEL_COST_TIERS,
    RESOURCE_THRESHOLDS,
    RESOURCE_TIER_CONCURRENCY,
)

__all__ = [
    # collaboration
    "COLLABORATION_KEYWORDS",
    "IMPLICIT_COLLAB_KEYWORDS",
    "COLLAB_DOMAIN_QUERIES",
    "COLLAB_DISPLAY_LIMITS",
    "COLLAB_TIMEOUT",
    # complexity
    "COMPLEXITY_KEYWORDS",
    "COMPLEXITY_LENGTH_THRESHOLDS",
    "COMPLEXITY_MULTI_DOMAIN_BONUS",
    "COMPLEXITY_RANGE",
    "MODEL_SELECTION_THRESHOLDS",
    "MODEL_SELECTION_MAP",
    # guardrails
    "REQUIRES_APPROVAL",
    "GUARDRAILS",
    "TOOL_SAFETY",
    "AUDIT_CONFIG",
    # resilience
    "REASONING_CONFIG",
    "RETRY_CONFIG",
    # resource
    "RESOURCE_THRESHOLDS",
    "RESOURCE_TIER_CONCURRENCY",
    "MODEL_COST_TIERS",
    # domain
    "ANDON_TYPE_MAP",
    "DEFAULT_ANDON_TYPE",
    "ESCALATION_LEVEL_MAP",
    "DEFAULT_ESCALATION_LEVEL",
    "REFLECTION_ACTIONABLE_KEYWORDS",
    "PROCESS_KEYWORDS",
    "SHIFT_TYPES",
    "ABNORMAL_TYPES",
    "DEFAULT_ABNORMAL_TYPE",
    "INSPECTION_ITEMS_QUALITY",
    "INSPECTION_ITEMS_EQUIPMENT",
    "ENTERPRISE_QUERY_PATTERNS",
]
