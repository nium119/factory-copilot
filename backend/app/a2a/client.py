"""A2A HTTP 客户端 — 与外部 Agent 通过 HTTP + SSE 通信

仿 MCPClient 生命周期：connect（握手发现 Agent Card）/ send_task / get_task / cancel_task / close。
传输为 JSON-RPC 2.0 over HTTP，SSE 用于任务进度流式。
"""
import json
from typing import Any, AsyncGenerator, Dict, List, Optional

import httpx

from app.a2a.protocol import AgentCard, Task, send_task_params
from app.core.logger import log


class A2AError(Exception):
    """A2A 协议错误"""
    pass


class A2AClient:
    """A2A HTTP 客户端 — 连接单个外部 Agent"""

    def __init__(self, agent_name: str = "default"):
        self.agent_name = agent_name
        self.url: str = ""
        self._agent_card: Optional[AgentCard] = None
        self._connected: bool = False
        self._http_client: Optional[httpx.AsyncClient] = None

    # ────────── 状态 ──────────

    @property
    def is_connected(self) -> bool:
        return self._connected and self._http_client is not None

    @property
    def agent_card(self) -> Optional[AgentCard]:
        return self._agent_card

    @property
    def display_name(self) -> str:
        return self._agent_card.name if self._agent_card else self.agent_name

    # ────────── 生命周期 ──────────

    async def connect(self, url: str, timeout: float = 10.0) -> AgentCard:
        """连接外部 Agent：拉取 Agent Card 并校验端点可达"""
        self.url = url.rstrip("/")
        self._http_client = httpx.AsyncClient(timeout=httpx.Timeout(timeout, read=timeout))
        try:
            card_url = f"{self.url}/.well-known/agent-card.json"
            resp = await self._http_client.get(card_url)
            resp.raise_for_status()
            data = resp.json()
            card = AgentCard(**data)
            if not card.name:
                raise A2AError("Agent Card 缺少 name 字段")
            if not card.url:
                card.url = self.url  # 以连接地址兜底
            self._agent_card = card
            self._connected = True
            log.info(f"[A2A] {self.agent_name} 已连接: {self.url} (skills={len(card.skills)})")
            return card
        except Exception as e:
            self._connected = False
            if self._http_client:
                await self._http_client.aclose()
                self._http_client = None
            if isinstance(e, A2AError):
                raise
            raise A2AError(f"连接外部 Agent {self.agent_name} 失败 ({self.url}): {e}") from e

    async def close(self) -> None:
        """关闭 HTTP 连接"""
        self._connected = False
        if self._http_client:
            try:
                await self._http_client.aclose()
            except Exception as e:
                log.debug(f"[A2A] {self.agent_name} 关闭连接异常: {e}")
            self._http_client = None
        log.info(f"[A2A] {self.agent_name} 已断开")

    # ────────── 协议内部 ──────────

    def _endpoint(self, method: str) -> str:
        """从 Agent Card endpoints 映射取任务端点路径，缺省用默认路径"""
        if self._agent_card and self._agent_card.endpoints:
            path = self._agent_card.endpoints.get(method)
            if path:
                return f"{self.url}{path}"
        return f"{self.url}/{method}"

    def _require_connected(self) -> httpx.AsyncClient:
        if not self.is_connected or self._http_client is None:
            raise A2AError(f"外部 Agent '{self.agent_name}' 未连接")
        return self._http_client

    async def _rpc(self, method: str, params: dict, timeout: float) -> Dict[str, Any]:
        """发送 JSON-RPC 请求，返回 result（不含 error）"""
        client = self._require_connected()
        request = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        try:
            resp = await client.post(
                self._endpoint(method),
                json=request,
                timeout=httpx.Timeout(timeout, read=timeout),
            )
            resp.raise_for_status()
            body = resp.json()
        except httpx.HTTPError as e:
            raise A2AError(f"外部 Agent '{self.agent_name}' 请求失败: {e}") from e
        except (ValueError, json.JSONDecodeError) as e:
            raise A2AError(f"外部 Agent '{self.agent_name}' 响应非 JSON: {e}") from e

        if body.get("error"):
            err = body["error"]
            raise A2AError(f"外部 Agent '{self.agent_name}' 错误 [{err.get('code', -1)}]: {err.get('message', 'unknown')}")
        result = body.get("result") or {}
        if not isinstance(result, dict):
            raise A2AError(f"外部 Agent '{self.agent_name}' 响应 result 非对象")
        return result

    # ────────── 任务操作 ──────────

    async def send_task(self, message: str, session_id: str = "", timeout: float = 30.0) -> Task:
        """tasks/send：发送任务并返回最终 Task（阻塞到完成或失败）"""
        params = send_task_params(message, session_id)
        result = await self._rpc("tasks/send", params, timeout)
        return Task(**result)

    async def send_task_subscribe(
        self,
        message: str,
        session_id: str = "",
        timeout: float = 60.0,
    ) -> AsyncGenerator[tuple, None]:
        """tasks/sendSubscribe：发送任务并以 SSE 流式接收进度事件。

        yield (event_type, data_dict)，event_type 如 status-update / artifact-update。
        """
        client = self._require_connected()
        params = send_task_params(message, session_id)
        request = {"jsonrpc": "2.0", "id": 1, "method": "tasks/sendSubscribe", "params": params}
        try:
            async with client.stream(
                "POST",
                self._endpoint("tasks/sendSubscribe"),
                json=request,
                headers={"Accept": "text/event-stream"},
                timeout=httpx.Timeout(timeout, read=timeout),
            ) as resp:
                resp.raise_for_status()
                event_type = ""
                data_lines: List[str] = []
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line:  # 空行 = 事件结束
                        if event_type or data_lines:
                            data = json.loads("".join(data_lines)) if data_lines else {}
                            yield event_type or "message", data
                        event_type = ""
                        data_lines = []
                    elif line.startswith("event:"):
                        event_type = line[len("event:"):].strip()
                    elif line.startswith("data:"):
                        data_lines.append(line[len("data:"):].strip())
        except httpx.HTTPError as e:
            raise A2AError(f"外部 Agent '{self.agent_name}' SSE 流异常: {e}") from e
        except (ValueError, json.JSONDecodeError) as e:
            raise A2AError(f"外部 Agent '{self.agent_name}' SSE 数据解析失败: {e}") from e

    async def get_task(self, task_id: str, timeout: float = 15.0) -> Task:
        """tasks/get：查询任务状态"""
        result = await self._rpc("tasks/get", {"id": task_id}, timeout)
        return Task(**result)

    async def list_tasks(self, session_id: str = "", status: Optional[str] = None, timeout: float = 15.0) -> List[Task]:
        """tasks/list：列出任务"""
        params: Dict[str, Any] = {"sessionId": session_id}
        if status:
            params["status"] = status
        result = await self._rpc("tasks/list", params, timeout)
        tasks = result.get("tasks", []) if isinstance(result, dict) else []
        return [Task(**t) for t in tasks if isinstance(t, dict)]

    async def cancel_task(self, task_id: str, timeout: float = 15.0) -> Task:
        """tasks/cancel：取消任务"""
        result = await self._rpc("tasks/cancel", {"id": task_id}, timeout)
        return Task(**result)
