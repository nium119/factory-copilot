"""
Chat API - 仅保留模型列表端点
所有流式对话统一走 /messages/stream（含 DB 持久化、记忆注入）
"""
from fastapi import APIRouter

router = APIRouter(prefix="/chat", tags=["聊天"])


@router.get("/models", summary="获取可用模型列表")
async def get_models():
    """从 DB 模型配置读取已启用的模型，包含 enable_thinking 标识"""
    from app.api.model_config import _load_config, BUILTIN_MODELS
    cfg = await _load_config()
    db_models = cfg.get("models", {})
    models = []
    for m in BUILTIN_MODELS:
        if m.get("type", "chat") != "chat":
            continue
        um = db_models.get(m["name"], {})
        if um.get("enabled", False):
            models.append({
                "key": m["name"],
                "label": m["label"],
                "enable_thinking": um.get("enable_thinking", m.get("enable_thinking", False)),
            })
    # 用户自定义模型（默认视为 chat 类型）
    for name, um in db_models.items():
        if name not in {m["name"] for m in BUILTIN_MODELS} and um.get("enabled"):
            models.append({
                "key": name,
                "label": um.get("label", name),
                "enable_thinking": um.get("enable_thinking", False),
            })
    return models
