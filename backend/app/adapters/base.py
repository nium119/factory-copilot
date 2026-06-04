"""ConceptAdapter 基类 — 概念级外部 API 翻译抽象。
每个具体适配器负责一个概念到一个外部系统的协议转换。
"""

from abc import ABC, abstractmethod


class ConceptAdapter(ABC):
    """概念适配器抽象基类。

    子类实现 build_request / parse_response 两个方法，
    完成本体语义 ↔ 外部 API 协议的翻译。
    """

    def __init__(self, concept_name: str):
        self.concept_name = concept_name

    @abstractmethod
    def build_request(self, action: str, args: dict) -> dict:
        """根据本体 action 构建外部 API 请求。

        返回: {path, method, body}
          path:   请求路径（如 /api/production/orders/{id}/start）
          method: HTTP 方法（POST/GET/PUT/DELETE）
          body:   请求体（字段名和值已翻译为外部系统格式）
        """

    @abstractmethod
    def parse_response(self, action: str, data: dict) -> dict:
        """解析外部 API 响应为 Agent 可读结果。

        返回: {success: bool, text: str, entityId: str | None}
          success:  操作是否成功
          text:     人类可读的摘要信息
          entityId: 创建/更新实体的 ID（如适用）
        """
