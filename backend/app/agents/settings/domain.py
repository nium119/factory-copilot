"""领域映射：安灯类型/升级、反射关键词、工位工序、企业查询模式

所有配置从 config/domain.yaml 加载。
"""

from app.core.config_loader import load_yaml

_cfg = load_yaml("domain")

ANDON_TYPE_MAP = _cfg.get("andon_type_map", {})
DEFAULT_ANDON_TYPE = _cfg.get("default_andon_type", "设备")
ESCALATION_LEVEL_MAP = _cfg.get("escalation_level_map", {})
DEFAULT_ESCALATION_LEVEL = _cfg.get("default_escalation_level", "线长")
REFLECTION_ACTIONABLE_KEYWORDS = _cfg.get("reflection_actionable_keywords", {})
PROCESS_KEYWORDS = _cfg.get("process_keywords", [])
SHIFT_TYPES = _cfg.get("shift_types", [])
ABNORMAL_TYPES = _cfg.get("abnormal_types", [])
DEFAULT_ABNORMAL_TYPE = _cfg.get("default_abnormal_type", "其他异常")
INSPECTION_ITEMS_QUALITY = _cfg.get("inspection_items_quality", [])
INSPECTION_ITEMS_EQUIPMENT = _cfg.get("inspection_items_equipment", [])
ENTERPRISE_QUERY_PATTERNS = _cfg.get("enterprise_query_patterns", [])
