"""协作触发关键词 + 领域查询模板 + 显示限制 + 超时配置

所有配置从 config/collaboration.yaml 加载。
"""

from app.core.config_loader import load_yaml

_cfg = load_yaml("collaboration")

COLLABORATION_KEYWORDS = _cfg.get("collaboration_keywords", [])
IMPLICIT_COLLAB_KEYWORDS = _cfg.get("implicit_collab_keywords", [])
COLLAB_DOMAIN_QUERIES = _cfg.get("collab_domain_queries", {})
COLLAB_DISPLAY_LIMITS = _cfg.get("collab_display_limits", {})
COLLAB_TIMEOUT = _cfg.get("collab_timeout", {})
