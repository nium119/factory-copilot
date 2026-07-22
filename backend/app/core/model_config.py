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
        db_models = cfg.get("models", {})
        # 合入内置模型的 provider（DB 只存 api_key/enabled，不存 provider）
        try:
            from app.api.model_config import BUILTIN_MODELS
            for m in BUILTIN_MODELS:
                if m["name"] in db_models:
                    db_models[m["name"]].setdefault("provider", m["provider"])
        except Exception:
            pass
        _cache = db_models
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


def _load_selection() -> dict:
    """加载 selection 配置（含 embedding_provider）"""
    try:
        from app.db import run_async
        async def _load():
            from app.db import get_db
            async for session in get_db():
                from app.repositories.namespace_config_repo import NamespaceConfigRepository
                repo = NamespaceConfigRepository(session)
                cfg = (await repo.get("_system", "model_config")) or {}
                return cfg.get("selection", {})
        return run_async(_load()) or {}
    except Exception:
        return {}


def get_embedding_key() -> str:
    """获取 embedding API Key，根据配置的 provider 查找"""
    provider = get_embedding_provider()
    return get_api_key(provider=provider)


def get_embedding_model() -> str:
    """获取 embedding 模型名，默认 text-embedding-v3"""
    from app.api.model_config import DEFAULT_SELECTION
    sel = _load_selection()
    return sel.get("embedding_model", DEFAULT_SELECTION.get("embedding_model", "text-embedding-v3"))


def get_embedding_provider() -> str:
    """获取 embedding provider，默认 qwen"""
    from app.api.model_config import DEFAULT_SELECTION
    sel = _load_selection()
    return sel.get("embedding_provider", DEFAULT_SELECTION.get("embedding_provider", "qwen"))


# Embedding provider 注册表：provider → (factory_fn, default_model)
_EMBEDDING_REGISTRY = {}

def _register_embedding_providers():
    if _EMBEDDING_REGISTRY:
        return
    # 阿里云 DashScope
    def _make_dashscope(model, key):
        from langchain_community.embeddings import DashScopeEmbeddings
        return DashScopeEmbeddings(model=model, dashscope_api_key=key)
    _EMBEDDING_REGISTRY["qwen"] = (_make_dashscope, "text-embedding-v3")
    # OpenAI
    def _make_openai(model, key):
        from langchain_openai import OpenAIEmbeddings
        return OpenAIEmbeddings(model=model, api_key=key)
    _EMBEDDING_REGISTRY["openai"] = (_make_openai, "text-embedding-3-small")
    # 本地 BGE (需要 langchain_community + 本地模型路径)
    # _EMBEDDING_REGISTRY["bge"] = (_make_bge, "bge-large-zh-v1.5")


def create_embedding():
    """根据配置创建 embedding 实例。provider 可从注册表扩展。"""
    _register_embedding_providers()
    provider = get_embedding_provider()
    model = get_embedding_model()
    key = get_embedding_key()
    if not key:
        return None

    entry = _EMBEDDING_REGISTRY.get(provider)
    if entry:
        factory_fn, default_model = entry
        return factory_fn(model or default_model, key)
    else:
        # 未知 provider → 尝试作为 langchain 类名动态加载
        from loguru import logger
        logger.warning(f"[Embedding] 未知 provider '{provider}'，已注册: {list(_EMBEDDING_REGISTRY.keys())}")
        return None


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
