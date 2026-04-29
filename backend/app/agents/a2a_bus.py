"""A2A 通信总线 — 内存消息队列，支持 Agent 间直接通信（预留对接外部 Agent）"""
import asyncio
import time
from typing import Dict, Optional, Callable, Awaitable, Any
from dataclasses import dataclass, field

from app.core.logger import log
from app.agents.a2a_protocol import A2AMessage, A2AMessageType, A2ADelegation


@dataclass
class _PendingRequest:
    """内部待处理请求"""
    msg: A2AMessage
    future: asyncio.Future = field(default_factory=asyncio.Future)


class A2ABus:
    """Agent 间通信总线 — 单例，内存实现。预留外部 Agent 对接。"""

    def __init__(self):
        self._agents: Dict[str, Any] = {}  # agent_name → handler callable

    def register(self, agent_name: str, handler: Callable[[A2AMessage], Awaitable[str]]) -> None:
        """注册 Agent 到总线"""
        self._agents[agent_name] = handler

    def unregister(self, agent_name: str) -> None:
        """从总线注销"""
        self._agents.pop(agent_name, None)

    async def send(self, msg: A2AMessage, timeout: float = 30.0) -> Optional[str]:
        """发送消息并等待响应"""
        if msg.msg_type == A2AMessageType.BROADCAST:
            return await self._broadcast(msg, timeout)

        handler = self._agents.get(msg.to_agent)
        if not handler:
            log.warning(f"[A2A] 目标 Agent '{msg.to_agent}' 未注册到总线")
            return None

        log.info(f"[A2A] {msg.from_agent} → {msg.to_agent}: {msg.content[:60]}")
        try:
            result = await asyncio.wait_for(handler(msg), timeout=timeout)
            return result
        except asyncio.TimeoutError:
            log.warning(f"[A2A] {msg.from_agent} → {msg.to_agent} 超时 ({timeout}s)")
            return None
        except Exception as e:
            log.error(f"[A2A] {msg.from_agent} → {msg.to_agent} 异常: {e}")
            return None

    async def _broadcast(self, msg: A2AMessage, timeout: float = 30.0) -> str:
        """广播消息到所有注册 Agent"""
        results = {}
        tasks = []
        agent_names = []
        for name, handler in self._agents.items():
            if name == msg.from_agent:
                continue
            agent_names.append(name)
            tasks.append(asyncio.wait_for(handler(msg), timeout=timeout))

        gathered = await asyncio.gather(*tasks, return_exceptions=True)
        for name, result in zip(agent_names, gathered):
            if isinstance(result, Exception):
                results[name] = f"[错误: {result}]"
            else:
                results[name] = result

        lines = [f"广播结果 (from {msg.from_agent}):"]
        for name, result in results.items():
            lines.append(f"### {name}\n{result}")
        return "\n\n".join(lines)

    async def delegate(
        self,
        from_agent: str,
        to_agent: str,
        task: str,
        context: Optional[dict] = None,
        timeout: float = 30.0,
    ) -> A2ADelegation:
        """委托：from_agent 将任务委托给 to_agent 并获取结果"""
        t0 = time.time()
        delegation = A2ADelegation(
            from_agent=from_agent,
            to_agent=to_agent,
            task=task,
        )

        msg = A2AMessage(
            msg_type=A2AMessageType.DELEGATE,
            from_agent=from_agent,
            to_agent=to_agent,
            content=task,
            context=context or {},
            correlation_id=delegation.delegation_id,
        )

        delegation.status = "running"
        result = await self.send(msg, timeout=timeout)
        delegation.elapsed_ms = (time.time() - t0) * 1000

        if result is None:
            delegation.status = "timeout"
            delegation.error = "超时"
        elif result.startswith("[错误:"):
            delegation.status = "error"
            delegation.error = result
        else:
            delegation.status = "success"
            delegation.result = result

        log.info(
            f"[A2A] 委托完成: {from_agent}→{to_agent} "
            f"状态={delegation.status} 耗时={delegation.elapsed_ms:.0f}ms"
        )
        return delegation


# 全局 A2A 总线实例
a2a_bus = A2ABus()
