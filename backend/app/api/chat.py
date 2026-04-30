"""
Chat API - 仅保留模型列表端点
所有流式对话统一走 /messages/stream（含 DB 持久化、记忆注入）
"""
from fastapi import APIRouter

from app.core.model_config import MODEL_PROVIDERS

router = APIRouter(prefix="/chat", tags=["聊天"])


@router.get("/models", summary="获取可用模型列表")
async def get_models():
    models = []
    for provider, config in MODEL_PROVIDERS.items():
        for model_key, model_info in config["models"].items():
            models.append({
                "key": model_key,
                "label": model_info["name"],
                "provider": provider,
                "enable_thinking": model_info.get("enable_thinking", False)
            })
    return models
