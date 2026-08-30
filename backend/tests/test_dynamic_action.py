# -*- coding: utf-8 -*-
"""阶段 B 单元测试：DynamicPlanner._plan_steps 正确保留写操作步骤（type=action）。

修复前：写操作步骤被 concept_label_map 过滤 + type 硬编码 query/find_similar 而丢弃，
导致「创建并排程」即使规划出写操作步骤，执行层也收不到。本测试锁定该修复。
"""
import json
from unittest.mock import MagicMock

import pytest

from app.agents.compiler.dynamic import DynamicPlanner


def _make_planner():
    skill = MagicMock()
    skill.name = "WorkOrder"
    skill.concept = "WorkOrder"
    skill.concept_label = "工单"

    runtime = MagicMock()
    runtime.skills = [skill]
    runtime.skill_catalog_text = "工单 WorkOrder"
    runtime.relation_graph_text = ""
    runtime.chains = []

    planner = DynamicPlanner(runtime)
    planner._mcp_tools = {}
    return planner


@pytest.mark.asyncio
async def test_plan_steps_preserves_write_actions(monkeypatch):
    planner = _make_planner()

    llm_json = {
        "steps": [
            {"concept": "WorkOrder", "type": "action", "action": "WorkOrder_create",
             "params": {"id": "WO-TEST", "qty": 10}, "reason": "创建工单"},
            {"concept": "WorkOrder", "type": "action", "action": "WorkOrder_schedule",
             "params": {}, "reason": "排程"},
        ],
        "ask": None,
        "options": None,
    }

    async def fake_chat(**kwargs):
        return json.dumps(llm_json, ensure_ascii=False)

    from app.services import llm_service as _lsm
    monkeypatch.setattr(_lsm.llm_service, "chat_sync", fake_chat)
    # 跳过需求覆盖评审（避免二次 LLM 调用）
    async def fake_review(message, steps):
        return steps
    monkeypatch.setattr(planner, "_review_plan", fake_review)

    steps, ask, ask_options = await planner._plan_steps("创建工单并排程", [])

    assert ask is None
    assert len(steps) == 2, f"写操作步骤应被保留，实际 {steps}"
    assert steps[0]["type"] == "action"
    assert steps[0]["action"] == "WorkOrder_create"
    assert steps[0]["params"] == {"id": "WO-TEST", "qty": 10}
    assert steps[1]["type"] == "action"
    assert steps[1]["action"] == "WorkOrder_schedule"
