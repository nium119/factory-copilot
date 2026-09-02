"""Action 执行网关测试 — FC /api/actions 端点（直通执行、回环拒绝、namespace 防呆）。"""
import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock

from app.main import app

client = TestClient(app)

# 假签名表：query（无确认）+ create（需确认）
FAKE_SIGS = {
    "WorkOrder_query": {
        "functionName": "WorkOrder_query", "conceptName": "WorkOrder",
        "actionName": "query", "description": "查工单", "params": [
            {"name": "code", "label": "工单号", "type": "string", "required": False},
        ], "requiresConfirmation": False,
    },
    "WorkOrder_create": {
        "functionName": "WorkOrder_create", "conceptName": "WorkOrder",
        "actionName": "create", "description": "建工单", "params": [
            {"name": "code", "label": "工单号", "type": "string", "required": True},
        ], "requiresConfirmation": True,
    },
    "mcp_os_search": {
        "functionName": "mcp_os_search", "conceptName": "os",
        "source": "mcp", "description": "MCP 回环工具",
    },
}


@pytest.fixture
def fake_executor(monkeypatch):
    """替换 action_executor 的签名表与执行方法。"""
    from app.services.action_executor import action_executor

    executed = []

    async def fake_exec(tool_name, arguments, user_id="", preflight=None):
        executed.append({"tool": tool_name, "arguments": arguments, "user_id": user_id})
        return {"result": [[f"{tool_name} ok"]], "rowCount": 1, "source": "neo4j"}

    monkeypatch.setattr(action_executor, "_sigs", dict(FAKE_SIGS))
    monkeypatch.setattr(action_executor, "execute_structured_async", fake_exec)
    return executed


def _auth(monkeypatch):
    """绕过全局 JWT 中间件（单测进程内无真实用户体系）。"""
    from app.core import middleware as mw
    monkeypatch.setattr(mw.AuthMiddleware, "dispatch", lambda self, req, nxt: nxt(req))


class TestListActions:
    def test_list_contains_signature(self, fake_executor, monkeypatch):
        _auth(monkeypatch)
        r = client.get("/api/actions")
        assert r.status_code == 200
        data = r.json()
        names = [t["name"] for t in data["tools"]]
        assert "WorkOrder_query" in names
        assert "mcp_os_search" not in names, "MCP 回环工具不应出现在可执行清单"

        create = next(t for t in data["tools"] if t["name"] == "WorkOrder_create")
        assert create["requiresConfirmation"] is True
        assert create["params"][0]["name"] == "code"


class TestExecuteAction:
    def test_execute_query(self, fake_executor, monkeypatch):
        _auth(monkeypatch)
        r = client.post("/api/actions/execute", json={
            "tool": "WorkOrder_query", "params": {"code": "WO-001"},
        })
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["rowCount"] == 1
        assert data["skipped_confirmation"] is False
        # 参数带 _skip_approval 直通标记
        assert fake_executor[0]["arguments"]["_skip_approval"] is True
        assert fake_executor[0]["arguments"]["code"] == "WO-001"
        assert fake_executor[0]["user_id"] == "mcp_service"

    def test_execute_write_marks_skipped(self, fake_executor, monkeypatch):
        """需确认的 action 被直通执行，响应透明标记 skipped_confirmation。"""
        _auth(monkeypatch)
        r = client.post("/api/actions/execute", json={
            "tool": "WorkOrder_create", "params": {"code": "WO-NEW"},
            "user_id": "alice",
        })
        assert r.status_code == 200
        assert r.json()["skipped_confirmation"] is True
        assert fake_executor[0]["user_id"] == "alice"

    def test_unknown_tool_404(self, fake_executor, monkeypatch):
        _auth(monkeypatch)
        r = client.post("/api/actions/execute", json={"tool": "Nope_query", "params": {}})
        assert r.status_code == 404

    def test_mcp_loopback_rejected(self, fake_executor, monkeypatch):
        _auth(monkeypatch)
        r = client.post("/api/actions/execute", json={"tool": "mcp_os_search", "params": {}})
        assert r.status_code == 400

    def test_namespace_mismatch_409(self, fake_executor, monkeypatch):
        _auth(monkeypatch)
        from app.services.ontology_service import ontology_service
        monkeypatch.setattr(
            type(ontology_service), "meta",
            property(lambda self: {"namespace": "manufacturing"}),
        )
        r = client.post("/api/actions/execute", json={
            "tool": "WorkOrder_query", "params": {}, "namespace": "contract",
        })
        assert r.status_code == 409

    def test_namespace_match_passes(self, fake_executor, monkeypatch):
        _auth(monkeypatch)
        from app.services.ontology_service import ontology_service
        monkeypatch.setattr(
            type(ontology_service), "meta",
            property(lambda self: {"namespace": "manufacturing"}),
        )
        r = client.post("/api/actions/execute", json={
            "tool": "WorkOrder_query", "params": {}, "namespace": "manufacturing",
        })
        assert r.status_code == 200

    def test_executor_exception_returns_error(self, fake_executor, monkeypatch):
        _auth(monkeypatch)
        from app.services.action_executor import action_executor

        async def boom(*a, **kw):
            raise RuntimeError("Neo4j down")

        monkeypatch.setattr(action_executor, "execute_structured_async", boom)
        r = client.post("/api/actions/execute", json={"tool": "WorkOrder_query"})
        assert r.status_code == 200
        assert r.json()["ok"] is False
        assert "Neo4j down" in r.json()["error"]
