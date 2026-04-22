"""模型配置 - 支持多个模型提供商"""
from typing import Dict, Any
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
    
    # #OpenAI
    # "openai": {
    #     "api_base": "https://api.openai.com/v1",
    #     "models": {
    #         "gpt-3.5-turbo": {
    #             "name": "GPT-3.5 Turbo",
    #             "enable_thinking": False,
    #             "max_tokens": 2000,
    #         },
    #         "gpt-4": {
    #             "name": "GPT-4",
    #             "enable_thinking": False,
    #             "max_tokens": 4000,
    #         },
    #     }
    # },
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

def get_api_key(provider: str) -> str:
    """
    获取API密钥
    
    Args:
        provider: 提供商名称
        
    Returns:
        API密钥
    """
    # 从settings获取对应的API密钥
    key_mapping = {
        "qwen": settings.DASHSCOPE_API_KEY,
        "deepseek": settings.DEEPSEEK_API_KEY,
        "openai": settings.OPENAI_API_KEY,
    }
    
    return key_mapping.get(provider, settings.AGENT_API_KEY)
