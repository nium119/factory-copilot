"""MonitorScheduler — 后台定时扫描告警，周期性调用 ExplorerService 分析生产数据。

通过 FastAPI lifespan 启动/停止。
"""
import asyncio
from datetime import datetime

from app.core.logger import log


class MonitorScheduler:
    """后台异步定时器，周期性运行 anomaly detection + trigger scanning."""

    def __init__(self, interval_seconds: int = 300):
        self._interval = interval_seconds
        self._task: asyncio.Task | None = None
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        log.info(f"[MonitorScheduler] 已启动，扫描间隔 {self._interval}s")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        log.info("[MonitorScheduler] 已停止")

    async def _loop(self):
        # 首次延迟 30s，让服务完全就绪
        await asyncio.sleep(30)

        while self._running:
            try:
                await self._scan()
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.warning(f"[MonitorScheduler] 扫描异常: {e}")

            try:
                await asyncio.sleep(self._interval)
            except asyncio.CancelledError:
                break

    async def _scan(self):
        from app.services.explorer_service import explorer_service

        start = datetime.now()
        result = await explorer_service.analyze(hours=24)
        elapsed = (datetime.now() - start).total_seconds()

        count = result["anomaly_count"]
        if count > 0:
            high = sum(1 for a in result["anomalies"] if a["severity"] == "high")
            log.info(
                f"[MonitorScheduler] 扫描完成 ({elapsed:.1f}s): "
                f"发现 {count} 项异常（高 {high}）"
            )
        else:
            log.debug(f"[MonitorScheduler] 扫描完成 ({elapsed:.1f}s): 无异常")


monitor_scheduler = MonitorScheduler(interval_seconds=300)
