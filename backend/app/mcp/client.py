"""MCP (Model Context Protocol) 客户端 — stdio 子进程 / HTTP(SSE) 远程 两种传输"""
import asyncio
import json
from typing import Any, Dict, List, Optional

import httpx
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
        self._tool_risks: Dict[str, str] = {}  # 工具风险声明 {tool_name: risk}

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

    async def connect(self, command: str, args: List[str] = None, tool_risks: dict = None) -> None:
        """启动 MCP Server 子进程并完成初始化握手"""
        args = args or []
        self._tool_risks = tool_risks or {}
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

        # 3. 自动注册到 TOOL_SAFETY（含工具风险声明）
        _register_mcp_tools_to_safety(self.server_name, self._tools, self._tool_risks)

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


def _register_mcp_tools_to_safety(server_name: str, tools: Dict[str, MCPTool], tool_risks: dict = None) -> None:
    """将 MCP 发现的工具注册到 TOOL_SAFETY（含风险声明）。

    tool_risks: {tool_name: risk}，声明值覆盖默认 READ；
    声明为 WRITE_APPROVE / CRITICAL 的工具同时注册审批动作到 REQUIRES_APPROVAL。
    """
    from app.agents.settings import TOOL_SAFETY, REQUIRES_APPROVAL
    prefix = f"mcp_{server_name}_"
    tool_risks = tool_risks or {}
    count = 0
    for name, tool in tools.items():
        key = f"{prefix}{name}"
        risk = tool_risks.get(name, "READ")
        if risk not in ("READ", "WRITE_AUDIT", "WRITE_APPROVE", "CRITICAL"):
            risk = "READ"
        TOOL_SAFETY[key] = {"risk": risk, "agent": "analysis_monitor"}
        if risk in ("WRITE_APPROVE", "CRITICAL"):
            REQUIRES_APPROVAL[key] = {
                "name": getattr(tool, "display_name", "") or name,
                "risk": "high" if risk == "CRITICAL" else "medium",
            }
        count += 1
    if count:
        logger.info(f"[MCP] {count} 个工具已注册到 TOOL_SAFETY (prefix={prefix})")


class MCPHttpClient:
    """MCP HTTP(SSE) 传输客户端 — 连接远程 MCP Server，无需本地脚本与子进程。

    传输流程（MCP 2024-11-05 SSE transport）：
      1. GET {url}（Accept: text/event-stream）建立 SSE 流；
      2. 收 endpoint 事件得到 POST 地址（相对路径按 url 的 base 拼接）；
      3. JSON-RPC 请求 POST 到消息端点，应答经 SSE 流 event: message 返回，
         按 id 匹配 pending future。
    与 MCPClient（stdio）同接口：tools / server_info / is_connected / call_tool / close。
    """

    def __init__(self, server_name: str = "default"):
        self.server_name = server_name
        self.url: str = ""
        self._client: Optional[httpx.AsyncClient] = None
        self._reader_task: Optional[asyncio.Task] = None
        self._post_url: str = ""
        self._request_id = 0
        self._pending: Dict[int, asyncio.Future] = {}
        self._tools: Dict[str, MCPTool] = {}
        self._server_info: Dict[str, Any] = {}
        self._closed = False

    @property
    def is_connected(self) -> bool:
        return (
            not self._closed
            and self._reader_task is not None
            and not self._reader_task.done()
        )

    @property
    def tools(self) -> Dict[str, MCPTool]:
        return self._tools

    @property
    def server_info(self) -> Dict[str, Any]:
        return self._server_info

    async def connect(self, url: str, tool_risks: dict = None) -> None:
        """连接远程 MCP Server 并完成初始化握手 + 工具发现。"""
        self.url = url.rstrip("/")
        logger.info(f"[MCP] 连接远程 server: {self.url}")
        self._client = httpx.AsyncClient(timeout=30.0)

        # 1. 建立 SSE 读任务（后台持续解析事件流）
        connected = asyncio.Event()
        self._reader_task = asyncio.create_task(self._sse_reader(connected))
        try:
            await asyncio.wait_for(connected.wait(), timeout=30.0)
        except asyncio.TimeoutError:
            await self.close()
            raise MCPError(f"连接远程 MCP Server 超时: {self.url}（未收到 endpoint 事件）")

        # 2. initialize + tools/list（复用 _request）
        init_result = await self._request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "factory-copilot", "version": "1.0.0"},
        })
        self._server_info = init_result.get("serverInfo", {})
        logger.info(f"[MCP] 初始化完成: {self._server_info.get('name', 'unknown')}")

        tools_result = await self._request("tools/list", {})
        for t in tools_result.get("tools", []):
            self._tools[t["name"]] = MCPTool(
                name=t["name"],
                description=t.get("description", ""),
                input_schema=t.get("inputSchema", {}),
            )
        logger.info(f"[MCP] 发现 {len(self._tools)} 个工具: {list(self._tools.keys())}")

        # 3. 注册到 TOOL_SAFETY（与 stdio 客户端同一套治理）
        _register_mcp_tools_to_safety(self.server_name, self._tools, tool_risks)

    def _resolve_endpoint(self, path: str) -> str:
        """按 URL 解析语义把 endpoint 地址解析为绝对 URL。

        - 绝对 URL（http/https）→ 原样
        - 以 / 开头 → 拼 origin（scheme://host:port），如 /api/mcp/messages
        - 其他相对路径 → 拼同目录（少见，兼容）
        """
        if path.startswith(("http://", "https://")):
            return path
        parts = httpx.URL(self.url)
        origin = f"{parts.scheme}://{parts.host}"
        if parts.port:
            origin += f":{parts.port}"
        if path.startswith("/"):
            return f"{origin}{path}"
        base = self.url.rsplit("/", 1)[0]
        return f"{base}/{path}"

    async def _sse_reader(self, connected: asyncio.Event) -> None:
        """后台读取 SSE 事件流：endpoint 事件记录 POST 地址，message 事件分发应答。"""
        event, data_lines = "", []
        try:
            async with self._client.stream(
                "GET", self.url, headers={"Accept": "text/event-stream"}
            ) as resp:
                if resp.status_code != 200:
                    raise MCPError(f"SSE 连接失败 HTTP {resp.status_code}: {self.url}")
                async for raw in resp.aiter_lines():
                    if raw == "":
                        # 空行 = 一帧结束
                        if event == "endpoint" and data_lines:
                            path = "\n".join(data_lines)
                            self._post_url = self._resolve_endpoint(path)
                            connected.set()
                        elif event == "message" and data_lines:
                            self._dispatch("\n".join(data_lines))
                        event, data_lines = "", []
                        continue
                    if raw.startswith(":"):
                        continue  # 心跳注释行
                    if raw.startswith("event:"):
                        event = raw[len("event:"):].strip()
                    elif raw.startswith("data:"):
                        data_lines.append(raw[len("data:"):].strip())
        except Exception as e:
            if not self._closed:
                logger.warning(f"[MCP] SSE 流断开 ({self.server_name}): {e}")
        finally:
            connected.set()  # 确保连接等待方不挂死
            # 流结束：所有未决请求标记失败
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(MCPError(f"MCP SSE 连接已断开: {self.server_name}"))
            self._pending.clear()

    def _dispatch(self, payload: str) -> None:
        """按 JSON-RPC id 把 SSE 应答分发给等待中的请求。"""
        try:
            response = json.loads(payload)
        except json.JSONDecodeError as e:
            logger.warning(f"[MCP] SSE 应答解析失败: {e}")
            return
        req_id = response.get("id")
        fut = self._pending.pop(req_id, None)
        if fut and not fut.done():
            fut.set_result(response)

    async def _request(self, method: str, params: dict) -> dict:
        """POST JSON-RPC 请求，等待 SSE 流上的对应应答。"""
        if not self.is_connected or not self._post_url:
            raise MCPError(f"远程 MCP Server '{self.server_name}' 未连接")
        self._request_id += 1
        req_id = self._request_id
        request = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}

        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        try:
            resp = await self._client.post(self._post_url, json=request)
            if resp.status_code == 404:
                raise MCPError("MCP 会话已失效（sessionId 不存在），请重新连接")
            if resp.status_code >= 400:
                raise MCPError(f"MCP 消息端点 HTTP {resp.status_code}: {resp.text[:200]}")
            result = await asyncio.wait_for(fut, timeout=60.0)
        except asyncio.TimeoutError:
            raise MCPError(f"等待 MCP 应答超时 (60s): {method}")
        finally:
            self._pending.pop(req_id, None)

        if "error" in result:
            err = result["error"]
            raise MCPError(f"MCP 错误 [{err.get('code', -1)}]: {err.get('message', 'unknown')}")
        return result.get("result", {})

    async def call_tool(self, name: str, arguments: dict = None) -> str:
        """调用 MCP 工具并返回结果文本（与 stdio 客户端同语义）。"""
        tool = self._tools.get(name)
        if not tool:
            raise MCPError(f"工具 '{name}' 不存在于 server '{self.server_name}'")

        logger.info(f"[MCP] 调用工具: {name}({arguments or {}})")
        result = await self._request("tools/call", {"name": name, "arguments": arguments or {}})

        content = result.get("content", [])
        text_parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text_parts.append(item.get("text", ""))
            elif isinstance(item, str):
                text_parts.append(item)
        return "\n".join(text_parts) if text_parts else json.dumps(result, ensure_ascii=False)

    async def close(self) -> None:
        """断开 SSE 流与 HTTP 客户端。"""
        self._closed = True
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await asyncio.wait_for(self._reader_task, timeout=3.0)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                pass
            self._reader_task = None
        if self._client:
            await self._client.aclose()
            self._client = None
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(MCPError("MCP 连接已关闭"))
        self._pending.clear()
        logger.info(f"[MCP] 已断开: {self.server_name}")


class MCPRegistry:
    """MCP Server 注册表 — 管理多个 MCP 连接（stdio 子进程 / HTTP SSE 远程）"""

    def __init__(self):
        self._clients: Dict[str, Any] = {}
        self._tool_map: Dict[str, tuple] = {}  # tool_name → (client, MCPTool)

    async def connect_server(
        self,
        name: str,
        command: str = None,
        args: List[str] = None,
        tool_risks: dict = None,
        url: str = None,
    ) -> Any:
        """连接一个 MCP Server。

        url 非空走 HTTP(SSE) 远程传输（无需本地脚本）；否则 stdio 子进程
        （command + args）。tool_risks 为工具风险声明 {tool_name: risk}。
        """
        if name in self._clients:
            await self._clients[name].close()
        if url:
            client = MCPHttpClient(server_name=name)
            await client.connect(url, tool_risks)
        else:
            client = MCPClient(server_name=name)
            await client.connect(command, args, tool_risks)
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
