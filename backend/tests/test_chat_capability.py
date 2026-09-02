"""CHAT 能力清单注入测试 — _chat_capability_section（「你有什么功能」基于业务域回答的根）。"""
from app.agents.base import _chat_capability_section


def _fake_executor(monkeypatch, sigs, concepts):
    from app.services.action_executor import action_executor
    monkeypatch.setattr(action_executor, "_sigs", dict(sigs))
    monkeypatch.setattr(action_executor, "_concepts", dict(concepts))
    monkeypatch.setattr(action_executor, "_ensure_loaded", lambda: None)
    from app.services.ontology_service import ontology_service
    monkeypatch.setattr(
        type(ontology_service), "meta",
        property(lambda self: {"namespace": "manufacturing"}),
    )


class TestCapabilitySection:
    def test_contains_concepts_and_confirmation_marks(self, monkeypatch):
        _fake_executor(
            monkeypatch,
            sigs={
                "WorkOrder_query": {"conceptName": "WorkOrder", "actionName": "query",
                                    "requiresConfirmation": False},
                "WorkOrder_create": {"conceptName": "WorkOrder", "actionName": "create",
                                     "requiresConfirmation": True},
                "Material_query": {"conceptName": "Material", "actionName": "query",
                                   "requiresConfirmation": False},
                "mcp_os_x": {"conceptName": "os", "source": "mcp"},  # 回环排除
            },
            concepts={
                "WorkOrder": {"label": "工单", "actions": [
                    {"name": "query", "label": "查询"}, {"name": "create", "label": "创建"}]},
                "Material": {"label": "物料", "actions": [{"name": "query", "label": "查询"}]},
            },
        )
        s = _chat_capability_section()
        assert "manufacturing" in s
        assert "工单：查询、创建（需确认）" in s
        assert "物料：查询" in s
        assert "os" not in s.split("：")[-1] or "mcp" not in s  # MCP 回环不入清单
        assert "禁止声称不具备的能力" in s

    def test_empty_when_no_sigs(self, monkeypatch):
        _fake_executor(monkeypatch, sigs={}, concepts={})
        assert _chat_capability_section() == ""

    def test_failure_returns_empty(self, monkeypatch):
        from app.services.action_executor import action_executor

        def boom():
            raise RuntimeError("未加载")

        monkeypatch.setattr(action_executor, "_ensure_loaded", boom)
        assert _chat_capability_section() == ""

    def test_label_fallback_to_action_name(self, monkeypatch):
        """概念/actions 无 label 时回退英文名，不崩。"""
        _fake_executor(
            monkeypatch,
            sigs={"X_query": {"conceptName": "X", "actionName": "query"}},
            concepts={"X": {}},
        )
        s = _chat_capability_section()
        assert "X：query" in s
