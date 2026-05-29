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
    @pytest.mark.asyncio
    async def test_manual_agent_override(self):
        from app.agents.router import route_intent
        result = await route_intent("随便说点什么", agent_name="scheduling")
        assert result["agent_name"] == "scheduling"
        assert result["confidence"] == 1.0
        assert result["method"] == "manual"

    @pytest.mark.asyncio
    async def test_keyword_match_single_domain(self):
        from app.agents.router import route_intent
        result = await route_intent("查询排产计划")
        assert result["agent_name"] == "scheduling"
        assert result["method"] == "keyword"
        assert result["confidence"] == 0.85

    @pytest.mark.asyncio
    async def test_keyword_match_equipment(self):
        from app.agents.router import route_intent
        result = await route_intent("设备状态查询")
        assert result["agent_name"] == "equipment"
        assert result["method"] == "keyword"

    @pytest.mark.asyncio
    async def test_explicit_collab_keyword(self):
        from app.agents.router import route_intent
        result = await route_intent("综合分析一下当前生产情况")
        assert result["agent_name"] == "general"
        assert result["use_agent"] is True
        assert result["method"] == "explicit_collab"

    @pytest.mark.asyncio
    async def test_multi_domain_triggers_collab(self):
        from app.agents.router import route_intent
        result = await route_intent("排产计划中设备运行状态如何")
        # Multi-domain now routes to ontology pipeline (use_agent=False)
        # for cross-concept query support, not agent collaboration
        assert result["use_agent"] is False
        assert result["method"] == "multi_domain"

    @pytest.mark.asyncio
    async def test_fallback_to_general(self):
        from app.agents.router import route_intent
        result = await route_intent("这是一个没有关键词的消息")
        assert result["agent_name"] == "general"
        assert result["method"] not in ("keyword", "manual")

    @pytest.mark.asyncio
    async def test_auto_keyword_respected(self):
        from app.agents.router import route_intent
        result = await route_intent("查询SMT产线的产量", agent_name="auto")
        assert result["agent_name"] in ("scheduling", "general", "quality")


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
