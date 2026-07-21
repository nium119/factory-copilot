"""模型配置 - 支持多个模型提供商"""
from typing import Any, Dict

from app.core.config import settings

# 模型提供商配置
MODEL_PROVIDERS = {
    # 阿里云百炼
    "qwen": {
        "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": {
            "qwen3.6-plus": {
                "name": "Qwen 3.6 Plus",
                "enable_thinking": True,
                "max_tokens": 2000,
            },
            "qwen-turbo": {
                "name": "Qwen Turbo",
                "enable_thinking": False,
                "max_tokens": 2000,
            },
            "qwen-plus": {
                "name": "Qwen Plus",
                "enable_thinking": False,
                "max_tokens": 2000,
            },
            "qwen-max": {
                "name": "Qwen Max",
                "enable_thinking": False,
                "max_tokens": 4000,
            },
        }
    },

    # DeepSeek
    "deepseek": {
        "api_base": "https://api.deepseek.com/v1",
        "models": {
            "deepseek-reasoner": {
                "name": "DeepSeek R1",
                "enable_thinking": True,
                "max_tokens": 4000,
            },
            "deepseek-chat": {
                "name": "DeepSeek Chat",
                "enable_thinking": False,
                "max_tokens": 4000,
            },
        }
    },
}

def get_model_config(model_name: str) -> Dict[str, Any]:
    """
    获取模型配置

    Args:
        model_name: 模型名称

    Returns:
        模型配置字典
    """
    # 遍历所有提供商查找模型
    for provider, config in MODEL_PROVIDERS.items():
        if model_name in config["models"]:
            model_config = config["models"][model_name]
            return {
                "provider": provider,
                "api_base": config["api_base"],
                "model_name": model_name,
                "enable_thinking": model_config.get("enable_thinking", False),
                "max_tokens": model_config.get("max_tokens", 2000),
                "name": model_config.get("name", model_name),
            }

    # 默认配置
    return {
        "provider": "custom",
        "api_base": settings.AGENT_API_BASE,
        "model_name": model_name,
        "enable_thinking": False,
        "max_tokens": settings.AGENT_MAX_TOKENS,
        "name": model_name,
    }

def get_api_key(provider: str, model_name: str = "") -> str:
    """获取 API 密钥。优先从 DB 配置读取（模型粒度），否则用 settings 兜底。"""
    # 优先从 DB 模型配置读取
    if model_name:
        try:
            from app.db import run_async
            async def _load():
                from app.db import get_db
                async for session in get_db():
                    from app.repositories.namespace_config_repo import NamespaceConfigRepository
                    repo = NamespaceConfigRepository(session)
                    cfg = (await repo.get("_system", "model_config")) or {}
                    models = cfg.get("models", {})
                    m = models.get(model_name, {})
                    key = m.get("api_key", "")
                    if key:
                        return key
                return ""
            db_key = run_async(_load())
            if db_key:
                return db_key
        except Exception:
            pass
    # fallback 到 settings
    key_mapping = {
        "qwen": settings.DASHSCOPE_API_KEY,
        "dashscope": settings.DASHSCOPE_API_KEY,
        "deepseek": settings.DEEPSEEK_API_KEY,
    }
    return key_mapping.get(provider, settings.OPENAI_API_KEY or "")
