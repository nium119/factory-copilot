"""MCP 试调端点测试 — POST /api/mcp/servers/{name}/call（链路验证入口）。"""
import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _auth(monkeypatch):
    from app.core import middleware as mw
    monkeypatch.setattr(mw.AuthMiddleware, "dispatch", lambda self, req, nxt: nxt(req))


class FakeTool:
    def __init__(self, name):
        self.name = name
        self.description = "测试工具"


class FakeClient:
    def __init__(self, tools, connected=True):
        self._tools = tools
        self._connected = connected

    @property
    def tools(self):
        return self._tools

    @property
    def is_connected(self):
        return self._connected

    async def call_tool(self, name, arguments=None):
        return f"工具 {name} 执行成功: {arguments}"


class TestCallEndpoint:
    def test_not_connected_404(self, monkeypatch):
        _auth(monkeypatch)
        r = client.post("/api/mcp/servers/os/call", json={"tool": "search_concepts", "arguments": {}})
        assert r.status_code == 404
        assert "未连接" in r.json()["detail"]

    def test_full_tool_name_and_result(self, monkeypatch):
        _auth(monkeypatch)
        from app.mcp import mcp_registry
        fake = FakeClient({"search_concepts": FakeTool("search_concepts")})
        monkeypatch.setitem(mcp_registry._clients, "os", fake)
        r = client.post("/api/mcp/servers/os/call", json={
            "tool": "mcp_os_search_concepts", "arguments": {"keyword": "工单"},
        })
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        # 响应报告的是 MCP Server 端原始工具名（无前缀）
        assert data["tool"] == "search_concepts"
        assert "工单" in data["result"]

    def test_short_name_direct(self, monkeypatch):
        _auth(monkeypatch)
        from app.mcp import mcp_registry
        fake = FakeClient({"search_concepts": FakeTool("search_concepts")})
        monkeypatch.setitem(mcp_registry._clients, "os", fake)
        r = client.post("/api/mcp/servers/os/call", json={"tool": "search_concepts"})
        assert r.status_code == 200
        assert r.json()["tool"] == "search_concepts"

    def test_unknown_tool_404_with_available_list(self, monkeypatch):
        _auth(monkeypatch)
        from app.mcp import mcp_registry
        fake = FakeClient({"search_concepts": FakeTool("search_concepts")})
        monkeypatch.setitem(mcp_registry._clients, "os", fake)
        r = client.post("/api/mcp/servers/os/call", json={"tool": "nope"})
        assert r.status_code == 404
        assert "search_concepts" in r.json()["detail"]

    def test_execution_error_returns_ok_false(self, monkeypatch):
        _auth(monkeypatch)
        from app.mcp import mcp_registry

        class BoomClient(FakeClient):
            async def call_tool(self, name, arguments=None):
                raise RuntimeError("连接中断")

        monkeypatch.setitem(mcp_registry._clients, "os", BoomClient({"x": FakeTool("x")}))
        r = client.post("/api/mcp/servers/os/call", json={"tool": "x"})
        assert r.status_code == 200
        assert r.json()["ok"] is False
        assert "连接中断" in r.json()["error"]
