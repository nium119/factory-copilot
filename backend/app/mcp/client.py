"""MCP (Model Context Protocol) 客户端 — stdio JSON-RPC 最小化实现"""
import asyncio
import json
from typing import Any, Dict, List, Optional

from loguru import logger


class MCPError(Exception):
    """MCP 协议错误"""
    pass


class MCPTool:
    """MCP 发现的工具描述"""
    def __init__(self, name: str, description: str = "", input_schema: dict = None):
        self.name = name
        self.description = description
        self.input_schema = input_schema or {}

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


class MCPClient:
    """MCP stdio 客户端 — 通过子进程与 MCP Server 通信"""

    def __init__(self, server_name: str = "default"):
        self.server_name = server_name
        self.process: Optional[asyncio.subprocess.Process] = None
        self._request_id = 0
        self._tools: Dict[str, MCPTool] = {}
        self._initialized = False
        self._server_info: Dict[str, Any] = {}

    @property
    def is_connected(self) -> bool:
        return self.process is not None and self.process.returncode is None

    @property
    def tools(self) -> Dict[str, MCPTool]:
        return self._tools

    @property
    def server_info(self) -> Dict[str, Any]:
        return self._server_info

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    async def connect(self, command: str, args: List[str] = None) -> None:
        """启动 MCP Server 子进程并完成初始化握手"""
        args = args or []
        logger.info(f"[MCP] 连接 server: {command} {' '.join(args)}")

        self.process = await asyncio.create_subprocess_exec(
            command, *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**__import__('os').environ, "PYTHONIOENCODING": "utf-8"},
        )

        # 1. 发送 initialize 请求
        init_result = await self._request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "factory-copilot", "version": "1.0.0"},
        })
        self._server_info = init_result.get("serverInfo", {})
        self._initialized = True
        logger.info(f"[MCP] 初始化完成: {self._server_info.get('name', 'unknown')}")

        # 2. 发现工具
        tools_result = await self._request("tools/list", {})
        tool_list = tools_result.get("tools", [])
        for t in tool_list:
            tool = MCPTool(
                name=t["name"],
                description=t.get("description", ""),
                input_schema=t.get("inputSchema", {}),
            )
            self._tools[t["name"]] = tool
        logger.info(f"[MCP] 发现 {len(self._tools)} 个工具: {list(self._tools.keys())}")

        # 3. 自动注册到 TOOL_SAFETY
        _register_mcp_tools_to_safety(self.server_name, self._tools)

    async def _request(self, method: str, params: dict) -> dict:
        """发送 JSON-RPC 请求并等待响应"""
        if not self.process or self.process.returncode is not None:
            raise MCPError(f"MCP Server '{self.server_name}' 未连接")

        req_id = self._next_id()
        request = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params,
        }
        payload = json.dumps(request, ensure_ascii=False) + "\n"

        try:
            self.process.stdin.write(payload.encode("utf-8"))
            await self.process.stdin.drain()
        except Exception as e:
            raise MCPError(f"写入请求失败: {e}")

        # 读取响应（一行一个 JSON 对象）
        try:
            line = await asyncio.wait_for(self.process.stdout.readline(), timeout=30.0)
            if not line:
                raise MCPError("Server 已关闭输出流")
            response = json.loads(line.decode("utf-8"))
        except asyncio.TimeoutError:
            raise MCPError("请求超时 (30s)")
        except json.JSONDecodeError as e:
            raise MCPError(f"响应 JSON 解析失败: {e}")

        if "error" in response:
            err = response["error"]
            raise MCPError(f"MCP 错误 [{err.get('code', -1)}]: {err.get('message', 'unknown')}")

        return response.get("result", {})

    async def call_tool(self, name: str, arguments: dict = None) -> str:
        """调用 MCP 工具并返回结果文本"""
        tool = self._tools.get(name)
        if not tool:
            raise MCPError(f"工具 '{name}' 不存在于 server '{self.server_name}'")

        logger.info(f"[MCP] 调用工具: {name}({arguments or {}})")
        result = await self._request("tools/call", {
            "name": name,
            "arguments": arguments or {},
        })

        # 提取文本内容
        content = result.get("content", [])
        text_parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text_parts.append(item.get("text", ""))
            elif isinstance(item, str):
                text_parts.append(item)
        return "\n".join(text_parts) if text_parts else json.dumps(result, ensure_ascii=False)

    async def close(self) -> None:
        """关闭 MCP Server 连接"""
        if self.process:
            try:
                self.process.stdin.close()
            except Exception:
                logger.debug(f"[MCP] stdin close failed for {self.server_name}")
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()
            self.process = None
            self._initialized = False
            logger.info(f"[MCP] 已断开: {self.server_name}")


def _register_mcp_tools_to_safety(server_name: str, tools: Dict[str, MCPTool]) -> None:
    """将 MCP 发现的工具注册到 TOOL_SAFETY"""
    from app.agents.settings import TOOL_SAFETY
    prefix = f"mcp_{server_name}_"
    count = 0
    for name, tool in tools.items():
        key = f"{prefix}{name}"
        if key not in TOOL_SAFETY:
            TOOL_SAFETY[key] = {"risk": "READ", "agent": "general"}
            count += 1
    if count:
        logger.info(f"[MCP] {count} 个工具已注册到 TOOL_SAFETY (prefix={prefix})")


class MCPRegistry:
    """MCP Server 注册表 — 管理多个 MCP 连接"""

    def __init__(self):
        self._clients: Dict[str, MCPClient] = {}
        self._tool_map: Dict[str, tuple] = {}  # tool_name → (client, MCPTool)

    async def connect_server(self, name: str, command: str, args: List[str] = None) -> MCPClient:
        """连接一个 MCP Server"""
        if name in self._clients:
            await self._clients[name].close()
        client = MCPClient(server_name=name)
        await client.connect(command, args)
        self._clients[name] = client
        # 更新工具映射
        for tool_name, tool in client.tools.items():
            key = f"mcp_{name}_{tool_name}"
            self._tool_map[key] = (client, tool)
        return client

    def get_tool_names(self) -> List[str]:
        return list(self._tool_map.keys())

    def get_client_for_tool(self, tool_name: str) -> Optional[MCPClient]:
        entry = self._tool_map.get(tool_name)
        return entry[0] if entry else None

    async def call_tool(self, tool_name: str, arguments: dict = None) -> str:
        """通过工具名自动路由到正确的 MCP Server"""
        entry = self._tool_map.get(tool_name)
        if not entry:
            raise MCPError(f"未找到 MCP 工具: {tool_name}")
        client, _ = entry
        # 去掉 mcp_{server}_ 前缀得到原始工具名
        prefix = f"mcp_{client.server_name}_"
        original_name = tool_name[len(prefix):]
        return await client.call_tool(original_name, arguments)

    async def close_all(self) -> None:
        for name, client in list(self._clients.items()):
            await client.close()
        self._clients.clear()
        self._tool_map.clear()

    @property
    def connected_servers(self) -> List[Dict[str, Any]]:
        return [
            {"name": c.server_name, "info": c.server_info, "tool_count": len(c.tools)}
            for c in self._clients.values() if c.is_connected
        ]


# 全局注册表
mcp_registry = MCPRegistry()
