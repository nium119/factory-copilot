"""概念适配器注册中心 — 概念到适配器类的映射。

每个需要调用外部 API 的概念在此注册适配器。
不依赖 YAML 配置 — 所有翻译逻辑在适配器代码中。
"""

from app.core.logger import log

# {概念名: 适配器类路径}
# 示例: {"WorkOrder": "app.adapters.mes_workorder.WorkOrderMESAdapter"}
_ADAPTER_REGISTRY: dict[str, str] = {}


def register_adapter(concept_name: str, class_path: str):
    """注册概念的适配器。

    class_path 示例: "app.adapters.mes_workorder.WorkOrderMESAdapter"
    """
    _ADAPTER_REGISTRY[concept_name] = class_path
    log.info(f"[AdapterRegistry] 已注册: {concept_name} -> {class_path}")


def get_adapter_class(concept_name: str):
    """获取概念的适配器类，未注册时返回 None。

    动态导入注册的类路径。
    """
    class_path = _ADAPTER_REGISTRY.get(concept_name)
    if not class_path:
        return None
    try:
        parts = class_path.rsplit(".", 1)
        if len(parts) != 2:
            log.warning(f"[AdapterRegistry] 无效的类路径: {class_path}")
            return None
        mod = __import__(parts[0], fromlist=[parts[1]])
        return getattr(mod, parts[1])
    except Exception as e:
        log.warning(f"[AdapterRegistry] 加载适配器失败 {concept_name}: {e}")
        return None


def get_all_backends() -> dict:
    """返回所有已注册适配器的概念列表。

    返回: {概念名: {backend: "api"}}
    """
    return {name: {"backend": "api"} for name in _ADAPTER_REGISTRY}


def auto_register_adapters():
    """扫描 app.adapters 包，自动注册所有适配器（排除基类）。"""
    try:
        from app.adapters import mes_workorder
        register_adapter("WorkOrder", "app.adapters.mes_workorder.WorkOrderMESAdapter")
    except ImportError:
        pass
