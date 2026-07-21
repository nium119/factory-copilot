"""模型配置 API — 管理模型列表 + 默认选择"""
from fastapi import APIRouter

router = APIRouter(prefix="/config/models", tags=["模型配置"])

# 内置模型（API Key 由用户配）
BUILTIN_MODELS = [
    {"name": "qwen-turbo", "label": "通义千问 Turbo（快速）", "provider": "dashscope", "api_url": "https://dashscope.aliyuncs.com/compatible-mode/v1"},
    {"name": "qwen-plus", "label": "通义千问 Plus（均衡）", "provider": "dashscope", "api_url": "https://dashscope.aliyuncs.com/compatible-mode/v1"},
    {"name": "qwen3.6-plus", "label": "千问 3.6 Plus（深度）", "provider": "dashscope", "api_url": "https://dashscope.aliyuncs.com/compatible-mode/v1"},
    {"name": "deepseek-v3", "label": "DeepSeek V3", "provider": "deepseek", "api_url": "https://api.deepseek.com/v1"},
    {"name": "deepseek-r1", "label": "DeepSeek R1", "provider": "deepseek", "api_url": "https://api.deepseek.com/v1"},
]

DEFAULT_SELECTION = {
    "decision_model": "qwen-turbo",
    "summary_model": "qwen-turbo",
    "default_model": "qwen-plus",
}

async def _load_config():
    from app.api.chains import _load_config as lc
    return (await lc("_system", "model_config")) or {}

async def _save_config(cfg: dict):
    from app.api.chains import _save_config as sc
    await sc("_system", "model_config", cfg)


@router.get("", summary="获取模型配置")
async def get_model_config():
    cfg = await _load_config()
    user_models = cfg.get("models", {})
    selection = cfg.get("selection", {})
    # 合并内置模型和用户覆盖
    models = []
    for m in BUILTIN_MODELS:
        um = user_models.get(m["name"], {})
        models.append({
            **m,
            "api_key": um.get("api_key", ""),
            "api_url": um.get("api_url", m["api_url"]),
            "enabled": um.get("enabled", m["name"] in ["qwen-turbo", "qwen-plus", "qwen3.6-plus"]),
        })
    return {
        "ok": True,
        "models": models,
        "selection": {**DEFAULT_SELECTION, **selection},
    }


@router.put("", summary="更新模型配置")
async def update_model_config(data: dict):
    models_data = {}
    for m in data.get("models", []):
        models_data[m["name"]] = {
            "api_key": m.get("api_key", ""),
            "api_url": m.get("api_url", ""),
            "enabled": m.get("enabled", False),
        }
    await _save_config({
        "models": models_data,
        "selection": data.get("selection", {}),
    })
    # 同步内存配置
    from app.agents.settings.model import MODEL_CONFIG
    cfg = await _load_config()
    MODEL_CONFIG.update({**DEFAULT_SELECTION, **cfg.get("selection", {})})
    return {"ok": True, "message": "已保存，即时生效"}
