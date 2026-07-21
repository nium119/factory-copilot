"""模型配置 API — 管理默认模型、决策模型、汇总模型"""
from fastapi import APIRouter

router = APIRouter(prefix="/config/models", tags=["模型配置"])

# 默认配置
DEFAULT_CONFIG = {
    "decision_model": "qwen-turbo",    # L2 分类/决策
    "summary_model": "qwen-turbo",     # DynamicPlanner 汇总
    "default_model": "qwen-plus",      # 通用/分析默认
}

# 可用模型列表
AVAILABLE_MODELS = [
    {"value": "qwen-turbo", "label": "通义千问 Turbo（快速）"},
    {"value": "qwen-plus", "label": "通义千问 Plus（均衡）"},
    {"value": "qwen3.6-plus", "label": "千问 3.6 Plus（深度）"},
    {"value": "deepseek-v3", "label": "DeepSeek V3（深度推理）"},
    {"value": "deepseek-r1", "label": "DeepSeek R1（深度思考）"},
]

async def _load_config():
    from app.api.chains import _load_config as lc
    return (await lc("_system", "model_config")) or {}

async def _save_config(cfg: dict):
    from app.api.chains import _save_config as sc
    await sc("_system", "model_config", cfg)


@router.get("", summary="获取模型配置")
async def get_model_config():
    cfg = await _load_config()
    return {
        "ok": True,
        "config": {**DEFAULT_CONFIG, **cfg},
        "available": AVAILABLE_MODELS,
    }


@router.put("", summary="更新模型配置")
async def update_model_config(data: dict):
    await _save_config(data.get("config", {}))
    # 同步更新到 agent settings
    from app.agents.settings.model import MODEL_CONFIG
    cfg = await _load_config()
    MODEL_CONFIG.update({**DEFAULT_CONFIG, **cfg})
    return {"ok": True, "message": "已保存，即时生效"}
