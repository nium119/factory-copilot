"""YAML 配置加载器 — 从 backend/config/ 读取 YAML 配置文件。"""

from pathlib import Path
from typing import Any

import yaml

_CONFIG_DIR = Path(__file__).parent.parent.parent / "config"


def load_yaml(name: str) -> dict[str, Any]:
    """加载 YAML 配置文件并返回内容。"""
    path = _CONFIG_DIR / f"{name}.yaml"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}
