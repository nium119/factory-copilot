"""外部 A2A 协作多轮延续单元测试（B2）。"""
import types

from app.models.message import MessageRole
from app.services.a2a_collab import match_external_continuation, match_external_continuation_llm
from app.services.history_projection import project_turn


def _ext_collab_history(content="能耗报告..."):
    msg = types.SimpleNamespace()
    msg.role = MessageRole.ASSISTANT
    msg.content = content
    msg.metadata_dict = {
        "agent_name": "external_a2a",
        "collab_agents": [{"name": "energy_demo", "display_name": "能耗监测模块"}],
    }
    return [project_turn(msg)]


def _patch_llm(monkeypatch, reply):
    """mock llm_service.chat_sync 返回固定结果。"""
    from app.services import llm_service as mod

    async def fake_chat_sync(**kwargs):
        return reply

    monkeypatch.setattr(mod.llm_service, "chat_sync", fake_chat_sync)


# ── 确定性指代延续 ──

def test_no_history():
    assert match_external_continuation("其它产线的", None) == []


def test_coref_continuation_hits():
    m = match_external_continuation("你试一下其它产线的", _ext_collab_history())
    assert [r["name"] for r in m] == ["energy_demo"]


def test_new_query_not_continuation():
    assert match_external_continuation("查询产线 P002 的能耗", _ext_collab_history()) == []


def test_no_coref_word_not_continuation():
    assert match_external_continuation("能耗", _ext_collab_history()) == []


def test_non_collab_history_not_continuation():
    msg = types.SimpleNamespace()
    msg.role = MessageRole.ASSISTANT
    msg.content = "P001 的能耗如下..."
    msg.metadata_dict = {"agent_name": "analysis_monitor", "agent_info": {"display_name": "智能分析"}}
    assert match_external_continuation("其它产线的", [project_turn(msg)]) == []


# ── LLM 短值延续（语义判断） ──

async def test_short_code_llm_continuation_true(monkeypatch):
    _patch_llm(monkeypatch, "true")
    hist = _ext_collab_history("请指定您想查询的产线编码，例如：查询产线 L02 的能耗")
    m = await match_external_continuation_llm("L02", hist)
    assert [r["name"] for r in m] == ["energy_demo"]


async def test_short_code_llm_continuation_false(monkeypatch):
    _patch_llm(monkeypatch, "false")
    m = await match_external_continuation_llm("ECN2026-002", _ext_collab_history())
    assert m == []


async def test_short_code_llm_skips_coref():
    # 指代词由确定性延续处理，LLM 判断不重复触发
    m = await match_external_continuation_llm("其它产线的", _ext_collab_history())
    assert m == []


async def test_short_code_llm_no_collab(monkeypatch):
    _patch_llm(monkeypatch, "true")
    msg = types.SimpleNamespace()
    msg.role = MessageRole.ASSISTANT
    msg.content = "P001 的能耗如下..."
    msg.metadata_dict = {"agent_name": "analysis_monitor", "agent_info": {"display_name": "智能分析"}}
    m = await match_external_continuation_llm("L02", [project_turn(msg)])
    assert m == []
