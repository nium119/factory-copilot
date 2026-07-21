"""模型配置 - 从 DB 读取，无需硬编码"""
from typing import Any, Dict


def get_model_config(model_name: str) -> Dict[str, Any]:
    """从 DB 模型配置获取模型参数。未配置时返回空。"""
    try:
        from app.db import run_async

        async def _load():
            from app.db import get_db
            async for session in get_db():
                from app.repositories.namespace_config_repo import NamespaceConfigRepository
                repo = NamespaceConfigRepository(session)
                cfg = (await repo.get("_system", "model_config")) or {}
                models = cfg.get("models", {})
                return models.get(model_name, {})

        m = run_async(_load()) or {}
        # 未启用的模型也允许调用（已在前端下拉过滤）
        return {
            "provider": m.get("provider", "custom"),
            "api_base": m.get("api_url", ""),
            "model_name": model_name,
            "enable_thinking": m.get("enable_thinking", False),
            "max_tokens": m.get("max_tokens", 2000),
            "name": m.get("label", model_name),
            "enabled": m.get("enabled", False),
        }
    except Exception:
        return {
            "provider": "custom",
            "api_base": "",
            "model_name": model_name,
            "enable_thinking": False,
            "max_tokens": 2000,
            "name": model_name,
        }


def get_api_key(provider: str, model_name: str = "") -> str:
    """获取 API 密钥。仅从 DB 模型配置读取，不兜底。未启用返回空。"""
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
                    if not m.get("enabled", False):
                        return ""
                    return m.get("api_key", "")

            return run_async(_load()) or ""
        except Exception:
            return ""
    return ""
