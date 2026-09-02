"""核心模块单元测试：guardrails / router / parallel_executor"""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


# ═══════════════════════════════════════════════════════════════
# Guardrails — 输入 / 输出安全检查
# ═══════════════════════════════════════════════════════════════

class TestCheckInput:
    def test_valid_message(self):
        from app.agents.guardrails import check_input
        ok, reason = check_input("查询今天的排产计划")
        assert ok is True
        assert reason is None

    def test_empty_message(self):
        from app.agents.guardrails import check_input
        ok, reason = check_input("")
        assert ok is False
        assert "不能为空" in reason

    def test_whitespace_only(self):
        from app.agents.guardrails import check_input
        ok, reason = check_input("   ")
        assert ok is False

    def test_too_long(self):
        from app.agents.guardrails import check_input
        ok, reason = check_input("A" * 5001)
        assert ok is False
        assert "过长" in reason

    def test_too_short(self):
        from app.agents.guardrails import check_input
        ok, reason = check_input("")
        assert ok is False

    def test_sql_injection_rejected(self):
        from app.agents.guardrails import check_input
        ok, reason = check_input("DROP TABLE users")
        assert ok is False
        assert "不安全" in reason

    def test_xss_rejected(self):
        from app.agents.guardrails import check_input
        ok, reason = check_input("<script>alert('xss')</script>")
        assert ok is False
        assert "不安全" in reason

    def test_normal_sql_keyword_passes(self):
        from app.agents.guardrails import check_input
        ok, reason = check_input("查询SQL数据库的连接状态")
        assert ok is True


class TestSanitizeInput:
    def test_trims_whitespace(self):
        from app.agents.guardrails import sanitize_input
        assert sanitize_input("  hello  ") == "hello"

    def test_strips_control_chars(self):
        from app.agents.guardrails import sanitize_input
        result = sanitize_input("he\x00llo\x1f")
        assert result == "hello"

    def test_normal_text_unchanged(self):
        from app.agents.guardrails import sanitize_input
        assert sanitize_input("查询排产计划") == "查询排产计划"


class TestCheckOutput:
    def test_valid_output(self):
        from app.agents.guardrails import check_output
        ok, reason, code = check_output("这是有效的响应")
        assert ok is True

    def test_empty_output(self):
        from app.agents.guardrails import check_output
        ok, reason, code = check_output("")
        assert ok is False
        assert code == "empty"

    def test_too_long_output(self):
        from app.agents.guardrails import check_output
        ok, reason, code = check_output("A" * 20001)
        assert ok is False
        assert code == "too_long"


class TestSanitizeToolOutput:
    def test_string_control_chars(self):
        from app.agents.guardrails import sanitize_tool_output
        assert sanitize_tool_output("he\x00llo") == "hello"

    def test_dict_recursive(self):
        from app.agents.guardrails import sanitize_tool_output
        result = sanitize_tool_output({"a": "va\x00lue", "b": [1, "c\x1f"]})
        assert result == {"a": "value", "b": [1, "c"]}

    def test_non_string_passthrough(self):
        from app.agents.guardrails import sanitize_tool_output
        assert sanitize_tool_output(42) == 42
        assert sanitize_tool_output(None) is None


# ═══════════════════════════════════════════════════════════════
# Router — 意图路由
# ═══════════════════════════════════════════════════════════════

class TestRouteIntent:
    """路由测试 — mock LLM 决策，验证 route_intent 的解析/强化/兜底逻辑本身。

    真实 LLM 的路由质量评估归 eval 套件（tests/test_eval.py，需活服务时跑）。
    """

    @staticmethod
    def _mock_llm(monkeypatch, payload: str):
        """把 llm_service.chat_sync 替换为返回固定 JSON 的 mock。"""
        from app.services import llm_service as ls

        async def fake_chat_sync(**kwargs):
            return payload

        monkeypatch.setattr(ls.llm_service, "chat_sync", fake_chat_sync)

    @pytest.mark.asyncio
    async def test_manual_agent_override(self):
        from app.agents.router import route_intent
        result = await route_intent("随便说点什么", agent_name="scheduling")
        assert result["agent_name"] == "scheduling"
        assert result["confidence"] == 1.0
        assert result["method"] == "manual"

    @pytest.mark.asyncio
    async def test_llm_routes_query_to_domain_agent(self, monkeypatch):
        """LLM 判定查询意图 → 返回对应业务 Agent。"""
        self._mock_llm(monkeypatch, '{"agent_name": "production_management", "intent": "query", "confidence": 0.9}')
        from app.agents.router import route_intent
        result = await route_intent("查询排产计划")
        assert result["agent_name"] == "production_management"
        assert result["intent"] == "query"
        assert 0 < result["confidence"] <= 1.0
        assert result["method"] == "llm"

    @pytest.mark.asyncio
    async def test_llm_routes_equipment_query(self, monkeypatch):
        self._mock_llm(monkeypatch, '{"agent_name": "quality_equipment", "intent": "query", "confidence": 0.85}')
        from app.agents.router import route_intent
        result = await route_intent("设备状态查询")
        assert result["agent_name"] == "quality_equipment"

    @pytest.mark.asyncio
    async def test_llm_markdown_fenced_json_parsed(self, monkeypatch):
        """LLM 返回 ```json 围栏时也能解析。"""
        self._mock_llm(
            monkeypatch,
            '```json\n{"agent_name": "analysis_monitor", "intent": "analysis", "confidence": 0.8}\n```',
        )
        from app.agents.router import route_intent
        result = await route_intent("综合分析一下当前生产情况")
        assert result["agent_name"] == "analysis_monitor"
        assert result["intent"] == "analysis"

    @pytest.mark.asyncio
    async def test_query_intent_analysis_monitor_upgraded(self, monkeypatch):
        """查询意图被 LLM 误归 analysis_monitor 时，强化路由到 production_execution。"""
        self._mock_llm(monkeypatch, '{"agent_name": "analysis_monitor", "intent": "query", "confidence": 0.7}')
        from app.agents.router import route_intent
        result = await route_intent("排产计划中设备运行状态如何")
        assert result["agent_name"] == "production_execution"

    @pytest.mark.asyncio
    async def test_invalid_intent_normalized_to_chat(self, monkeypatch):
        """非法 intent 值归一化为 chat。"""
        self._mock_llm(monkeypatch, '{"agent_name": "analysis_monitor", "intent": "weird", "confidence": 0.5}')
        from app.agents.router import route_intent
        result = await route_intent("这是一个没有关键词的消息")
        assert result["agent_name"] == "analysis_monitor"
        assert result["intent"] == "chat"

    @pytest.mark.asyncio
    async def test_llm_failure_falls_back(self, monkeypatch):
        """LLM 调用异常 → 兜底 analysis_monitor（method=default，confidence 低）。"""
        from app.services import llm_service as ls

        async def broken_chat_sync(**kwargs):
            raise RuntimeError("LLM 不可用")

        monkeypatch.setattr(ls.llm_service, "chat_sync", broken_chat_sync)
        from app.agents.router import route_intent
        result = await route_intent("查询SMT产线的产量", agent_name="auto")
        assert result["agent_name"] == "analysis_monitor"
        assert result["method"] == "default"
        assert result["confidence"] <= 0.5


# ═══════════════════════════════════════════════════════════════
# ParallelExecutor — 并行执行
# ═══════════════════════════════════════════════════════════════

class TestParallelExecutorExecute:
    @pytest.mark.asyncio
    async def test_basic_parallel_execution(self):
        from app.core.parallel_executor import ParallelExecutor, ParallelTask

        mock_agent = MagicMock()
        mock_agent.call_tools = AsyncMock(return_value="result data")

        def resolver(name):
            return mock_agent

        executor = ParallelExecutor(default_timeout=5.0)
        tasks = [
            ParallelTask(task_id="1", agent_name="scheduling", query="q1"),
            ParallelTask(task_id="2", agent_name="equipment", query="q2"),
        ]
        batch = await executor.execute(tasks, agent_resolver=resolver)
        assert batch.total_count == 2
        assert batch.success_count == 2
        assert batch.overall_status == "complete"

    @pytest.mark.asyncio
    async def test_agent_not_found(self):
        from app.core.parallel_executor import ParallelExecutor, ParallelTask

        def resolver(name):
            return None

        executor = ParallelExecutor(default_timeout=5.0)
        tasks = [ParallelTask(task_id="1", agent_name="unknown", query="q")]
        batch = await executor.execute(tasks, agent_resolver=resolver)
        assert batch.results[0].status == "error"
        assert "not found" in batch.results[0].error.lower()

    @pytest.mark.asyncio
    async def test_timeout_produces_partial(self):
        from app.core.parallel_executor import ParallelExecutor, ParallelTask

        async def slow_tool(msg):
            await asyncio.sleep(2.0)
            return "slow result"

        fast_agent = MagicMock()
        fast_agent.call_tools = AsyncMock(return_value="fast")
        slow_agent = MagicMock()
        slow_agent.call_tools = slow_tool

        def resolver(name):
            return slow_agent if name == "slow" else fast_agent

        executor = ParallelExecutor(default_timeout=0.1)
        tasks = [
            ParallelTask(task_id="1", agent_name="fast", query="q"),
            ParallelTask(task_id="2", agent_name="slow", query="q", timeout=0.05),
        ]
        batch = await executor.execute(tasks, agent_resolver=resolver)
        assert batch.success_count == 1
        assert batch.overall_status == "partial"
        timeout_results = [r for r in batch.results if r.status == "timeout"]
        assert len(timeout_results) == 1

    @pytest.mark.asyncio
    async def test_empty_result_records_as_empty(self):
        from app.core.parallel_executor import ParallelExecutor, ParallelTask

        mock_agent = MagicMock()
        mock_agent.call_tools = AsyncMock(return_value=None)

        def resolver(name):
            return mock_agent

        executor = ParallelExecutor()
        tasks = [ParallelTask(task_id="1", agent_name="test", query="q")]
        batch = await executor.execute(tasks, agent_resolver=resolver)
        assert batch.results[0].status in ("empty", "error")

    @pytest.mark.asyncio
    async def test_execute_with_events_parallel_done_always_emitted(self):
        from app.core.parallel_executor import ParallelExecutor, ParallelTask

        mock_agent = MagicMock()
        mock_agent.call_tools = AsyncMock(return_value="ok")

        def resolver(name):
            return mock_agent

        executor = ParallelExecutor(default_timeout=5.0)
        tasks = [ParallelTask(task_id="1", agent_name="test", query="q")]

        events = []
        async for evt_type, evt_data in executor.execute_with_events(tasks, agent_resolver=resolver):
            events.append(evt_type)

        assert "parallel_start" in events
        assert "parallel_task" in events
        assert "parallel_done" in events, "parallel_done must always be emitted (finally block)"
