"""EventDispatcherWorker — 后台轮询 event_queue，匹配规则，分发通知"""
import asyncio
import json
from datetime import datetime

from sqlalchemy import select, update

from app.core.logger import log
from app.db import get_db


class EventDispatcherWorker:
    """后台异步 worker，轮询 event_queue 消费未处理事件。"""

    def __init__(self, interval_seconds: int = 5):
        self._interval = interval_seconds
        self._task: asyncio.Task | None = None
        self._running = False
        self._counters = {"processed": 0, "failed": 0, "notifications": 0}

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def counters(self) -> dict:
        return dict(self._counters)

    async def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        log.info(f"[EventDispatcher] 已启动，轮询间隔 {self._interval}s")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        log.info(
            f"[EventDispatcher] 已停止 (处理: {self._counters['processed']}, "
            f"失败: {self._counters['failed']}, 通知: {self._counters['notifications']})"
        )

    async def _loop(self):
        await asyncio.sleep(10)  # 初始延迟，等待服务就绪

        while self._running:
            try:
                await self._process_batch()
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.warning(f"[EventDispatcher] 处理异常: {e}")

            try:
                await asyncio.sleep(self._interval)
            except asyncio.CancelledError:
                break

    async def _process_batch(self):
        from app.models.event import EventQueue
        from app.core.notification_engine import notification_engine

        async for session in get_db():
            # 取一批 pending 事件
            stmt = (
                select(EventQueue)
                .where(EventQueue.status == "pending")
                .order_by(EventQueue.created_at.asc())
                .limit(20)
            )
            result = await session.execute(stmt)
            events = result.scalars().all()

            if not events:
                return

            for event in events:
                try:
                    # 标记处理中
                    event.status = "processing"
                    await session.commit()

                    # 解析 payload
                    payload = json.loads(event.payload) if isinstance(event.payload, str) else event.payload

                    # 匹配规则 → 创建通知
                    count = await notification_engine.process_event(
                        event_type=event.type,
                        payload=payload,
                    )

                    # 标记完成
                    event.status = "processed"
                    event.processed_at = datetime.now()
                    self._counters["processed"] += 1
                    self._counters["notifications"] += count
                    await session.commit()

                except Exception as e:
                    log.warning(f"[EventDispatcher] 事件 {event.id} ({event.type}) 失败: {e}")
                    event.retry_count += 1
                    event.error_message = str(e)[:500]
                    if event.retry_count >= event.max_retries:
                        event.status = "failed"
                        self._counters["failed"] += 1
                    else:
                        event.status = "pending"  # 放回队列等重试
                    await session.commit()

            break  # async for 只迭代一次


event_dispatcher = EventDispatcherWorker(interval_seconds=5)
