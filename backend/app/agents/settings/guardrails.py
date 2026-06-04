"""安全护栏 + 工具安全分级 + 审批流 + 审计配置

所有配置从 config/tool_safety.yaml 加载。
"""

from app.core.config_loader import load_yaml

_cfg = load_yaml("tool_safety")

TOOL_SAFETY = _cfg.get("tool_safety", {})
REQUIRES_APPROVAL = _cfg.get("requires_approval", {})
GUARDRAILS = _cfg.get("guardrails", {})
AUDIT_CONFIG = _cfg.get("audit", {})
