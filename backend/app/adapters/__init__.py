"""概念适配器 — 每个概念的外部 API 翻译逻辑。

每个使用外部服务的概念必须有注册的适配器。
不依赖 YAML 映射 — 所有翻译逻辑在适配器代码中。
"""

from app.adapters.base import ConceptAdapter

__all__ = ["ConceptAdapter"]
