"""模型配置 API — 管理模型列表 + 默认选择"""
from fastapi import APIRouter

router = APIRouter(prefix="/config/models", tags=["模型配置"])

# 内置模型（API Key 由用户配）
BUILTIN_MODELS = [
    {"name": "qwen-turbo", "label": "千问 Turbo（最快决策）", "provider": "qwen", "api_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "enable_thinking": False, "max_tokens": 2000},
    {"name": "qwen-plus", "label": "千问 Plus（均衡）", "provider": "qwen", "api_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "enable_thinking": False, "max_tokens": 4000},
    {"name": "qwen3.7-plus", "label": "千问 3.7 Plus（最新旗舰）", "provider": "qwen", "api_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "enable_thinking": False, "max_tokens": 8000},
    {"name": "qwen3.6-plus", "label": "千问 3.6 Plus（深度推理）", "provider": "qwen", "api_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "enable_thinking": True, "max_tokens": 8000},
    {"name": "qwen3.6-flash", "label": "千问 3.6 Flash（快速推理）", "provider": "qwen", "api_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "enable_thinking": False, "max_tokens": 4000},
    {"name": "deepseek-v4-pro", "label": "DeepSeek V4 Pro（旗舰推理）", "provider": "deepseek", "api_url": "https://api.deepseek.com/v1", "enable_thinking": True, "max_tokens": 128000},
    {"name": "deepseek-v4-flash", "label": "DeepSeek V4 Flash（快速）", "provider": "deepseek", "api_url": "https://api.deepseek.com/v1", "enable_thinking": False, "max_tokens": 128000},
]

# 新项目首次启动时的默认启用模型
DEFAULT_ENABLED_MODELS = ["qwen-turbo", "qwen-plus", "qwen3.7-plus", "qwen3.6-plus", "qwen3.6-flash"]

DEFAULT_SELECTION = {
    "decision_model": "qwen-turbo",
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
            "enable_thinking": um.get("enable_thinking", m.get("enable_thinking", False)),
            "max_tokens": um.get("max_tokens", m.get("max_tokens", 2000)),
            "enabled": um.get("enabled", m["name"] in DEFAULT_ENABLED_MODELS),
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
            "enable_thinking": m.get("enable_thinking", False),
            "max_tokens": m.get("max_tokens", 2000),
        }
    await _save_config({
        "models": models_data,
        "selection": data.get("selection", {}),
    })
    # 同步内存配置 + 刷新缓存
    from app.agents.settings.model import MODEL_CONFIG
    from app.core.model_config import invalidate_cache
    cfg = await _load_config()
    MODEL_CONFIG.update({**DEFAULT_SELECTION, **cfg.get("selection", {})})
    invalidate_cache()
    return {"ok": True, "message": "已保存，即时生效"}


@router.post("/{name}/test", summary="测试模型连接")
async def test_model(name: str):
    """用配置的 Key + URL 发一个简单请求验证连通性"""
    from app.api.model_config import _load_config, BUILTIN_MODELS
    cfg = await _load_config()
    models = cfg.get("models", {})
    m = models.get(name, {})
    api_key = m.get("api_key", "")
    api_url = m.get("api_url", "")
    # 找内置模型的默认 URL
    if not api_url:
        for bm in BUILTIN_MODELS:
            if bm["name"] == name:
                api_url = bm["api_url"]
                break
    if not api_key:
        return {"ok": False, "message": "未配置 API Key"}
    if not api_url:
        return {"ok": False, "message": "未配置 API 地址"}

    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{api_url.rstrip('/')}/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            if resp.status_code in (200, 401):  # 401 = Key 有效但权限不足也算连通
                return {"ok": True, "message": f"连接成功 (HTTP {resp.status_code})", "status": resp.status_code}
            return {"ok": False, "message": f"HTTP {resp.status_code}: {resp.text[:200]}"}
    except Exception as e:
        return {"ok": False, "message": f"连接失败: {str(e)[:200]}"}
