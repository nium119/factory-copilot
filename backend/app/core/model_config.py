"""模型配置 - 从 DB 读取，内存缓存避免每次 LLM 调用都查库"""
import time
from typing import Any, Dict

_cache: Dict[str, Any] = {}
_cache_ts: float = 0
_CACHE_TTL = 60


def _load_all_models() -> dict:
    global _cache, _cache_ts
    now = time.time()
    if _cache and now - _cache_ts < _CACHE_TTL:
        return _cache
    try:
        from app.db import run_async

        async def _load():
            from app.db import get_db
            async for session in get_db():
                from app.repositories.namespace_config_repo import NamespaceConfigRepository
                repo = NamespaceConfigRepository(session)
                return (await repo.get("_system", "model_config")) or {}

        cfg = run_async(_load()) or {}
        _cache = cfg.get("models", {})
        _cache_ts = now
    except Exception:
        if not _cache:
            _cache = {}
    return _cache


def invalidate_cache():
    global _cache_ts
    _cache_ts = 0


def get_model_config(model_name: str) -> Dict[str, Any]:
    models = _load_all_models()
    m = models.get(model_name, {})
    # enabled 默认 True：迁移只存了 api_key，enabled 由前端 API 层控制默认值
    return {
        "provider": m.get("provider", "custom"),
        "api_base": m.get("api_url", ""),
        "model_name": model_name,
        "enable_thinking": m.get("enable_thinking", False),
        "max_tokens": m.get("max_tokens", 2000),
        "name": m.get("label", model_name),
        "enabled": m.get("enabled", True),
    }


def get_api_key(provider: str = "", model_name: str = "") -> str:
    if model_name:
        models = _load_all_models()
        m = models.get(model_name, {})
        return m.get("api_key", "")
    # 没指定模型 → 返回该 provider 下第一个有 key 的
    if provider:
        models = _load_all_models()
        for name, m in models.items():
            if m.get("provider") == provider and m.get("api_key"):
                return m["api_key"]
    return ""
