"""A2A Agent 注册表 — 管理多个外部 Agent 的 HTTP 连接

完全仿照 MCPRegistry 模式（mcp/client.py L198-247）：
- _clients: name → A2AClient
- connect_agent / close_agent / close_all / connected_agents
额外维护 auto_collab 开关（阶段二：外部 Agent 是否加入自动协作池，默认关）。
"""
from typing import Any, Dict, List, Optional

from app.a2a.client import A2AClient, A2AError
from app.a2a.protocol import Task
from app.core.logger import log


class A2ARegistry:
    """A2A Agent 注册表 — 外部 Agent 连接的单一入口"""

    def __init__(self):
        self._clients: Dict[str, A2AClient] = {}
        self._auto_collab: Dict[str, bool] = {}

    # ────────── 连接管理 ──────────

    async def connect_agent(self, name: str, url: str, display_name: str = "", auto_collab: bool = False, timeout: float = 10.0) -> A2AClient:
        """连接外部 Agent：创建 client → 拉取 Agent Card → 存注册表"""
        import asyncio
        client = A2AClient(agent_name=name, display_name=display_name)
        try:
            await asyncio.wait_for(client.connect(url, timeout=timeout), timeout=timeout + 3.0)
        except (asyncio.TimeoutError, A2AError) as e:
            raise A2AError(f"外部 Agent '{name}' 连接失败: {e}") from e
        # 旧连接先断开（幂等重连）
        old = self._clients.get(name)
        if old:
            await old.close()
        self._clients[name] = client
        self._auto_collab[name] = auto_collab
        return client

    async def close_agent(self, name: str) -> None:
        """断开指定 Agent"""
        client = self._clients.pop(name, None)
        self._auto_collab.pop(name, None)
        if client:
            await client.close()

    async def close_all(self) -> None:
        """断开所有外部 Agent 连接"""
        for name, client in list(self._clients.items()):
            try:
                await client.close()
            except Exception as e:
                log.warning(f"[A2A] 关闭 {name} 异常: {e}")
        self._clients.clear()
        self._auto_collab.clear()
        log.info("[A2A] 已断开所有外部 Agent")

    def get_client(self, name: str) -> Optional[A2AClient]:
        return self._clients.get(name)

    def is_connected(self, name: str) -> bool:
        client = self._clients.get(name)
        return bool(client and client.is_connected)

    @property
    def connected_agents(self) -> List[Dict[str, Any]]:
        """已连接 Agent 列表，含 Agent Card 摘要"""
        result = []
        for name, client in self._clients.items():
            if not client.is_connected:
                continue
            card = client.agent_card
            result.append({
                "name": name,
                "display_name": client.display_name,
                "url": client.url,
                "connected": True,
                "skills_count": len(card.skills) if card else 0,
                "skills": [s.model_dump() for s in card.skills] if card else [],
                "description": card.description if card else "",
                "auto_collab": self._auto_collab.get(name, False),
            })
        return result

    # ────────── 自动协作开关（阶段二） ──────────

    def set_auto_collab(self, name: str, enabled: bool) -> None:
        if name in self._clients:
            self._auto_collab[name] = enabled

    def get_auto_collab(self, name: str) -> bool:
        return self._auto_collab.get(name, False)

    def auto_collab_agents(self) -> List[str]:
        """已连接且启用自动协作的外部 Agent 名"""
        return [name for name, enabled in self._auto_collab.items() if enabled and self.is_connected(name)]

    # ────────── 任务操作 ──────────

    async def send_task(self, agent_name: str, message: str, context_id: str = "", timeout: float = 30.0) -> Task:
        client = self.get_client(agent_name)
        if not client or not client.is_connected:
            raise A2AError(f"外部 Agent '{agent_name}' 未连接")
        return await client.send_task(message, context_id=context_id, timeout=timeout)

    async def get_task(self, agent_name: str, task_id: str) -> Task:
        client = self.get_client(agent_name)
        if not client or not client.is_connected:
            raise A2AError(f"外部 Agent '{agent_name}' 未连接")
        return await client.get_task(task_id)

    async def cancel_task(self, agent_name: str, task_id: str) -> Task:
        client = self.get_client(agent_name)
        if not client or not client.is_connected:
            raise A2AError(f"外部 Agent '{agent_name}' 未连接")
        return await client.cancel_task(task_id)


# 全局单例
a2a_registry = A2ARegistry()
