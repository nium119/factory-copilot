# -*- coding: utf-8 -*-
"""默认规划器：把 L2 意图分类结果统一成 LoopPlan（理解层唯一出口）。

落实「理解归 LLM，执行归确定性」——本规划器不做任何执行，只负责把
「消息 + 候选工具」理解成统一决定（chat / ask / tool / multi_step），
执行交给 Executor。

classify_fn 是可注入的 L2 分类函数（生产环境传 BaseAgent._llm_classify_action），
这样规划器本身零副作用、可纯单元测试。
"""
from typing import Any, Awaitable, Callable, Optional

from app.agents.loop import LoopPlan, Observation


class DefaultPlanner:
    """默认规划器：L2 分类 → LoopPlan 映射。"""

    def __init__(self, classify_fn: Callable[..., Awaitable[tuple]]):
        # classify_fn(message, candidates, model_name) -> (fn_name, method, confidence)
        self.classify_fn = classify_fn

    async def plan(self, observation: Observation) -> LoopPlan:
        candidates = observation.candidate_tools or []
        fn_name, method, confidence = await self.classify_fn(
            observation.message, candidates, observation.model_name,
        )
        return self._to_plan(fn_name)

    @staticmethod
    def _to_plan(fn_name: Optional[str]) -> LoopPlan:
        """L2 分类结果 → 统一决定（纯映射，可独立测试）。"""
        if fn_name == "CHAT":
            return LoopPlan(kind="chat", reason="闲聊/寒暄/讨论，自由对话")
        if fn_name == "UNSUPPORTED":
            # 操作类意图但无对应工具 → 反问澄清（执行层走能力发现）
            return LoopPlan(kind="ask", reason="操作类意图无直接工具，需反问澄清")
        if not fn_name or fn_name == "NONE":
            # 分析类无精确匹配 → 多步规划（执行层走 DynamicPlanner）
            return LoopPlan(kind="multi_step", reason="无精确匹配，走多步规划")
        return LoopPlan(kind="tool", tool_name=fn_name, reason=f"匹配到工具 {fn_name}")
