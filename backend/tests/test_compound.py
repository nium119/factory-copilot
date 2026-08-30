# -*- coding: utf-8 -*-
"""阶段 B 理解层单元测试：复合任务 LLM 判断 + 异常降级。

验证「复合任务判断归 LLM，动作词计数只作异常降级兜底」。
"""
import pytest

from app.agents.base import BaseAgent


def _agent():
    return object.__new__(BaseAgent)  # 绕过 __init__，只测纯逻辑


class TestIsCompoundIntent:
    @pytest.mark.asyncio
    async def test_compound_true(self, monkeypatch):
        agent = _agent()

        async def fake_chat(**kwargs):
            return "true"

        from app.services import llm_service as _lsm
        monkeypatch.setattr(_lsm.llm_service, "chat_sync", fake_chat)
        assert await agent._is_compound_intent("创建工单并排程", None) is True

    @pytest.mark.asyncio
    async def test_single_step_false(self, monkeypatch):
        agent = _agent()

        async def fake_chat(**kwargs):
            return "false"

        from app.services import llm_service as _lsm
        monkeypatch.setattr(_lsm.llm_service, "chat_sync", fake_chat)
        assert await agent._is_compound_intent("查询工单", None) is False
        assert await agent._is_compound_intent("创建销售订单", None) is False

    @pytest.mark.asyncio
    async def test_fallback_to_verb_count_on_llm_error(self, monkeypatch):
        agent = _agent()

        async def boom(**kwargs):
            raise RuntimeError("LLM 不可用")

        from app.services import llm_service as _lsm
        monkeypatch.setattr(_lsm.llm_service, "chat_sync", boom)
        # 降级兜底：多动作词（创建+并+排程）→ True
        assert await agent._is_compound_intent("创建工单并排程", None) is True
        # 单动作词 → False
        assert await agent._is_compound_intent("查询工单", None) is False
