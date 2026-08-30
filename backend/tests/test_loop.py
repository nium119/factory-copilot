# -*- coding: utf-8 -*-
"""统一 Agent 循环骨架纯单元测试（不依赖 Neo4j / LLM）。

验证阶段 A 第 2 步的骨架契约：计划类型校验、单轮/多轮调度、
轮次上限、反馈器继续/结束、留痕。
"""
import pytest

from app.agents.loop import (
    AgentLoop,
    LoopPlan,
    LoopTracer,
    Observation,
)


# ═══════════════════════════════════════════════════════════════
# LoopPlan 契约
# ═══════════════════════════════════════════════════════════════

class TestLoopPlan:
    def test_valid_kinds(self):
        for kind in ("chat", "ask", "tool", "multi_step", "graph", "unsupported", "done"):
            assert LoopPlan(kind=kind).kind == kind

    def test_invalid_kind_raises(self):
        with pytest.raises(ValueError):
            LoopPlan(kind="not_a_plan")

    def test_defaults(self):
        p = LoopPlan(kind="tool")
        assert p.tool_name == ""
        assert p.params == {}
        assert p.steps == []
        assert p.question == ""


# ═══════════════════════════════════════════════════════════════
# 桩实现：Planner / Executor / Reflector
# ═══════════════════════════════════════════════════════════════

class _Planner:
    def __init__(self, plans):
        # plans: 每次调用依次返回的计划队列
        self.plans = list(plans)
        self.calls = 0

    async def plan(self, observation):
        self.calls += 1
        return self.plans.pop(0) if self.plans else LoopPlan(kind="done")


class _Executor:
    def __init__(self):
        self.executed = []

    async def execute(self, plan, observation):
        self.executed.append(plan.kind)
        yield ("tool_start", plan.tool_name)
        yield ("tool_result", f"结果:{plan.tool_name}")


class _Reflector:
    def __init__(self, plans):
        self.plans = list(plans)

    async def reflect(self, plan, observation, results):
        return self.plans.pop(0) if self.plans else LoopPlan(kind="done")


def _obs():
    return Observation(message="测试消息", session_id="s1")


# ═══════════════════════════════════════════════════════════════
# AgentLoop 调度
# ═══════════════════════════════════════════════════════════════

class TestAgentLoop:
    @pytest.mark.asyncio
    async def test_single_round_no_reflector(self):
        planner = _Planner([LoopPlan(kind="tool", tool_name="A_query")])
        executor = _Executor()
        loop = AgentLoop(planner=planner, executor=executor)

        events = [evt async for evt in loop.run(_obs())]
        assert [t for t, _ in events] == ["tool_start", "tool_result"]
        assert executor.executed == ["tool"]

    @pytest.mark.asyncio
    async def test_done_plan_short_circuits(self):
        planner = _Planner([LoopPlan(kind="done")])
        executor = _Executor()
        loop = AgentLoop(planner=planner, executor=executor)

        events = [evt async for evt in loop.run(_obs())]
        assert events == []
        assert executor.executed == []  # done 不触发执行

    @pytest.mark.asyncio
    async def test_reflector_continues_then_done(self):
        planner = _Planner([LoopPlan(kind="tool", tool_name="step1")])
        executor = _Executor()
        reflector = _Reflector([
            LoopPlan(kind="tool", tool_name="step2"),  # 第一轮反馈 → 继续
            LoopPlan(kind="done"),                    # 第二轮反馈 → 结束
        ])
        loop = AgentLoop(planner=planner, executor=executor, reflector=reflector)

        events = [evt async for evt in loop.run(_obs())]
        # 两轮执行：step1 与 step2 各产出两个事件
        assert executor.executed == ["tool", "tool"]
        assert [t for t, _ in events] == ["tool_start", "tool_result",
                                          "tool_start", "tool_result"]

    @pytest.mark.asyncio
    async def test_max_rounds_stops_loop(self):
        planner = _Planner([LoopPlan(kind="tool", tool_name="x")])
        executor = _Executor()
        # 反馈器永不结束 → 应被轮次上限强制截断
        reflector = _Reflector([LoopPlan(kind="tool", tool_name="x")] * 100)
        loop = AgentLoop(planner=planner, executor=executor, reflector=reflector,
                         max_rounds=3)

        events = [evt async for evt in loop.run(_obs())]
        assert executor.executed == ["tool", "tool", "tool"]
        assert len(loop.tracer.rounds) == 3
        assert len(events) == 6

    @pytest.mark.asyncio
    async def test_tracer_records_rounds(self):
        planner = _Planner([LoopPlan(kind="ask", question="你要哪种？", reason="意图模糊")])
        executor = _Executor()
        loop = AgentLoop(planner=planner, executor=executor)

        _ = [evt async for evt in loop.run(_obs())]
        assert loop.tracer.kind_sequence() == ["ask"]
        assert loop.tracer.rounds[0]["kind"] == "ask"
        assert "意图模糊" in loop.tracer.summary()

    def test_tracer_empty_summary(self):
        t = LoopTracer()
        assert t.summary() == "无循环记录"
        assert t.kind_sequence() == []
