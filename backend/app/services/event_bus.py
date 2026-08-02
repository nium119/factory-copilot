"""全局 SSE 事件总线 — 审批状态变更实时广播。"""
import asyncio
import json
from typing import AsyncGenerator

from app.core.logger import log


class EventBus:
    """简单的 SSE 广播器。维护活跃连接列表，向所有连接广播事件。"""

    def __init__(self):
        self._queues: list[asyncio.Queue] = []

    async def subscribe(self) -> AsyncGenerator[str, None]:
        """订阅事件流。返回 SSE 格式的字符串异步生成器。"""
        queue: asyncio.Queue = asyncio.Queue()
        self._queues.append(queue)
        try:
            # 发送初始心跳
            yield _sse("connected", {"message": "已连接"})
            while True:
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=30)
                    yield data
                except asyncio.TimeoutError:
                    # 30s 心跳
                    yield _sse("heartbeat", {})
        finally:
            self._queues.remove(queue)

    async def publish(self, event_type: str, data: dict):
        """广播事件到所有已连接客户端。"""
        payload = _sse(event_type, data)
        dead = []
        for q in self._queues:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            self._queues.remove(q)
        if self._queues:
            log.debug(f"[EventBus] {event_type} → {len(self._queues)} 客户端")


def _sse(event_type: str, data: dict) -> str:
    # 双通道：既发命名事件（event: xxx，供 addEventListener 消费），
    # 也发一条默认消息（onmessage 兜底）——前端两种方式都能收到，
    # 避免命名事件类型新增时前端 EventSource 收不到。
    payload = json.dumps(data, ensure_ascii=False)
    return (
        f"event: {event_type}\ndata: {payload}\n\n"
        f"data: {json.dumps({'__type': event_type, **data}, ensure_ascii=False)}\n\n"
    )


event_bus = EventBus()
