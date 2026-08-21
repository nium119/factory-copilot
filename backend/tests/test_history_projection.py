"""结构化历史投影单元测试（B1）。"""
import types

from app.models.message import MessageRole
from app.services.history_projection import TURN_META_KEY, project_history, project_turn, recent_turns


def _msg(role, content, meta=None):
    m = types.SimpleNamespace()
    m.role = role
    m.content = content
    m.metadata_dict = meta or {}
    return m


def test_user_message_projects_to_human():
    u = project_turn(_msg(MessageRole.USER, "查询产线 P001 的能耗"))
    assert u.type == "human"
    assert u.content == "查询产线 P001 的能耗"


def test_assistant_with_agent_info():
    a = project_turn(_msg(
        MessageRole.ASSISTANT, "P001 的能耗如下...",
        {"agent_name": "analysis_monitor", "agent_info": {"display_name": "智能分析"}, "is_dynamic": True},
    ))
    assert a.type == "ai"
    assert a.content.startswith("[本轮由 智能分析 处理]")
    turn = a.additional_kwargs[TURN_META_KEY]
    assert turn["agent_name"] == "analysis_monitor"
    assert turn["agent_label"] == "智能分析"
    assert turn["is_dynamic"] is True


def test_assistant_with_collab_agents():
    a = project_turn(_msg(
        MessageRole.ASSISTANT, "能耗报告...",
        {"agent_name": "external_a2a", "collab_agents": [{"name": "energy_demo", "display_name": "能耗监测模块"}]},
    ))
    turn = a.additional_kwargs[TURN_META_KEY]
    assert turn["agent_label"] == "能耗监测模块"
    assert turn["tool"] == "energy_demo"


def test_assistant_without_identity():
    a = project_turn(_msg(MessageRole.ASSISTANT, "好的。", {}))
    assert a.content == "好的。"
    assert a.additional_kwargs[TURN_META_KEY]["agent_label"] == ""


def test_project_history_skips_system():
    msgs = [
        _msg(MessageRole.USER, "你好"),
        _msg(MessageRole.SYSTEM, '{"tool": "x"}'),
        _msg(MessageRole.ASSISTANT, "回复", {"agent_name": "a", "agent_info": {"display_name": "A"}}),
    ]
    hist = project_history(msgs)
    assert [h.type for h in hist] == ["human", "ai"]


def test_recent_turns_empty():
    assert recent_turns(None) == []
    assert recent_turns([]) == []


def test_recent_turns_extracts_and_orders():
    hist = project_history([
        _msg(MessageRole.USER, "查询能耗"),
        _msg(MessageRole.ASSISTANT, "r1", {"agent_name": "external_a2a", "collab_agents": [{"name": "energy_demo", "display_name": "能耗监测模块"}]}),
        _msg(MessageRole.USER, "其它产线的"),
        _msg(MessageRole.ASSISTANT, "r2", {"agent_name": "analysis_monitor", "agent_info": {"display_name": "智能分析"}}),
    ])
    turns = recent_turns(hist)
    # 只含 assistant 的 turn，最近的在最后（正序）
    assert len(turns) == 2
    assert turns[0]["agent_label"] == "能耗监测模块"
    assert turns[0]["tool"] == "energy_demo"
    assert turns[1]["agent_label"] == "智能分析"


def test_recent_turns_limit():
    hist = project_history([
        _msg(MessageRole.ASSISTANT, "r", {"agent_name": "a1", "agent_info": {"display_name": "A1"}}),
        _msg(MessageRole.ASSISTANT, "r", {"agent_name": "a2", "agent_info": {"display_name": "A2"}}),
        _msg(MessageRole.ASSISTANT, "r", {"agent_name": "a3", "agent_info": {"display_name": "A3"}}),
    ])
    turns = recent_turns(hist, limit=2)
    assert [t["agent_label"] for t in turns] == ["A2", "A3"]
