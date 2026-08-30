# -*- coding: utf-8 -*-
"""默认规划器纯单元测试：L2 分类结果 → LoopPlan 映射。

验证「任务规划器」把意图理解统一成决定（chat/ask/tool/multi_step），
classify_fn 用桩注入，零 LLM/Neo4j 依赖。
"""
import pytest

from app.agents.loop import LoopPlan, Observation
from app.agents.planner import DefaultPlanner


def _obs(msg="创建单据", tools=None):
    return Observation(message=msg, candidate_tools=tools or [])


class TestToPlan:
    def test_chat(self):
        p = DefaultPlanner._to_plan("CHAT")
        assert p.kind == "chat"

    def test_unsupported_maps_to_ask(self):
        p = DefaultPlanner._to_plan("UNSUPPORTED")
        assert p.kind == "ask"
        assert "反问" in p.reason

    def test_none_maps_to_multi_step(self):
        assert DefaultPlanner._to_plan(None).kind == "multi_step"
        assert DefaultPlanner._to_plan("NONE").kind == "multi_step"

    def test_tool_name(self):
        p = DefaultPlanner._to_plan("WorkOrder_query")
        assert p.kind == "tool"
        assert p.tool_name == "WorkOrder_query"


class TestPlan:
    @pytest.mark.asyncio
    async def test_plan_uses_classify_fn(self):
        captured = {}

        async def classify(message, candidates, model_name):
            captured.update(message=message, candidates=candidates, model_name=model_name)
            return ("WorkOrder_query", "llm", 0.9)

        planner = DefaultPlanner(classify)
        obs = _obs("查询工单", tools=[{"name": "WorkOrder_query"}])
        plan = await planner.plan(obs)

        assert captured["message"] == "查询工单"
        assert captured["candidates"] == [{"name": "WorkOrder_query"}]
        assert plan.kind == "tool"
        assert plan.tool_name == "WorkOrder_query"

    @pytest.mark.asyncio
    async def test_plan_multi_step_when_no_tool(self):
        async def classify(message, candidates, model_name):
            return (None, "llm", 0.0)

        planner = DefaultPlanner(classify)
        plan = await planner.plan(_obs("综合分析"))
        assert plan.kind == "multi_step"
