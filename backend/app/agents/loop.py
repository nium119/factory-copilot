# -*- coding: utf-8 -*-
"""统一 Agent 循环骨架：观察 → 规划 → 执行 → 反馈。

阶段 A 第 2 步的地基，落实架构文档的「理解归 LLM，执行归确定性」分层：

- Observation：观察层上下文（消息 + 历史 + 候选工具 + 记忆/检索）
- LoopPlan：规划器的统一决定（理解层唯一出口，单步/多步/反问/闲聊都归一为 kind）
- LoopTracer：循环轮次留痕（可观测性基础，后续接 tracing）
- Planner / Executor / Reflector：三个可插拔接口（Protocol）
- AgentLoop：循环骨架，带轮次上限，串起「观察→规划→执行→反馈」

执行层仍复用现有 action_executor / DynamicPlanner，本模块只定义骨架与契约，
不重写现有编排，保证现有对话不回归；后续阶段逐步把
``base.py._standard_process`` 的启发式收编进 Planner 实现。
"""
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Optional, Protocol
from time import perf_counter

from app.core.logger import log

# 循环轮次上限：理解层至多重试 N 次，防止规划-反馈死循环
MAX_LOOP_ROUNDS = 4

# 规划器可能产出的决定类型
PLAN_KINDS = ("chat", "ask", "tool", "multi_step", "graph", "collab", "unsupported", "done")


@dataclass
class Observation:
    """观察层上下文：循环每一轮看到的全部输入。"""

    message: str
    session_id: str = "default"
    user_id: str = ""
    model_name: Optional[str] = None
    namespace: str = ""
    history_messages: list = field(default_factory=list)
    rag_context: dict = field(default_factory=dict)
    candidate_tools: list = field(default_factory=list)


@dataclass
class LoopPlan:
    """规划器（理解层）的统一决定。

    kind 取值：
    - chat        自由对话，不调工具
    - ask         意图模糊，反问用户澄清（question 为反问文案）
    - tool        单步工具（tool_name + params）
    - multi_step  多步计划（steps 为计划步骤列表）
    - unsupported 无对应写操作（能力边界外）
    - done        结束循环（反馈器判定完成/无需继续）
    """

    kind: str
    question: str = ""
    tool_name: str = ""
    params: dict = field(default_factory=dict)
    steps: list = field(default_factory=list)
    reason: str = ""

    def __post_init__(self):
        if self.kind not in PLAN_KINDS:
            raise ValueError(f"未知计划类型: {self.kind!r}，合法值为 {PLAN_KINDS}")


@dataclass
class StepResult:
    """执行/反馈单步的结果（供反馈器判断是否继续循环）。"""

    ok: bool
    detail: str = ""
    data: Any = None


class LoopTracer:
    """循环轮次留痕：记录每轮 kind、耗时、简述，供可观测性/审计使用。"""

    def __init__(self):
        self.rounds: list[dict] = []

    def record(self, round_no: int, kind: str, detail: str, elapsed_ms: float) -> None:
        self.rounds.append({
            "round": round_no,
            "kind": kind,
            "detail": detail[:200],
            "elapsed_ms": round(elapsed_ms, 2),
        })

    def summary(self) -> str:
        if not self.rounds:
            return "无循环记录"
        parts = [f"第{r['round']}轮[{r['kind']}]{r['detail']}({r['elapsed_ms']}ms)"
                 for r in self.rounds]
        return " → ".join(parts)

    def kind_sequence(self) -> list:
        """各轮 kind 序列（测试断言用）。"""
        return [r["kind"] for r in self.rounds]


class Planner(Protocol):
    """规划器接口：观察 → 统一决定。"""

    async def plan(self, observation: Observation) -> LoopPlan: ...


class Executor(Protocol):
    """执行器接口：按计划执行，流式产出 (事件类型, 事件数据)。"""

    async def execute(self, plan: LoopPlan, observation: Observation) -> AsyncIterator[tuple]: ...


class Reflector(Protocol):
    """反馈器接口：据执行结果决定是否继续循环，返回下一轮计划（kind=done 则结束）。"""

    async def reflect(self, plan: LoopPlan, observation: Observation,
                      results: list) -> LoopPlan: ...


class AgentLoop:
    """统一 Agent 循环骨架：观察 → 规划 → 执行 → 反馈，带轮次上限。

    规划器（理解）与执行器（确定性）解耦；反馈器可空（无反馈则执行一轮即止）。
    """

    def __init__(self, planner: Planner, executor: Executor,
                 reflector: Optional[Reflector] = None,
                 max_rounds: int = MAX_LOOP_ROUNDS,
                 tracer: Optional[LoopTracer] = None):
        self.planner = planner
        self.executor = executor
        self.reflector = reflector
        self.max_rounds = max(1, max_rounds)
        self.tracer = tracer or LoopTracer()

    async def run(self, observation: Observation) -> AsyncIterator[tuple]:
        """跑一轮或多轮循环，流式产出执行事件，直到 done 或达到轮次上限。

        planner 只负责初始计划；之后每一步由 reflector「反思执行结果 → 给出下一步计划」，
        直到 reflector 返回 done 或达到轮次上限（ReAct 反思循环）。
        """
        plan = await self.planner.plan(observation)
        for round_no in range(1, self.max_rounds + 1):
            _t0 = perf_counter()
            self.tracer.record(round_no, plan.kind, plan.reason or plan.tool_name,
                               (perf_counter() - _t0) * 1000)
            log.info(f"[Loop] 第{round_no}轮 kind={plan.kind} reason={plan.reason[:80]!r}")

            if plan.kind == "done":
                return

            results: list = []
            async for evt in self.executor.execute(plan, observation):
                results.append(evt)
                yield evt

            if self.reflector is None:
                return

            plan = await self.reflector.reflect(plan, observation, results)

        log.warning(f"[Loop] 达到轮次上限 {self.max_rounds}，强制结束")
