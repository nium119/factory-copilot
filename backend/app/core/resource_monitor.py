"""资源感知监控器 — 跟踪并发数、API 调用频率、token 用量，动态限流"""
import asyncio
import time
from contextlib import asynccontextmanager
from enum import Enum
from typing import Any, Dict, Optional

from app.agents.settings import (
    MODEL_COST_TIERS,
    RESOURCE_THRESHOLDS,
    RESOURCE_TIER_CONCURRENCY,
)
from app.core.config import settings
from app.core.logger import log


class ResourceTier(str, Enum):
    OPTIMAL = "optimal"
    NORMAL = "normal"
    CONSTRAINED = "constrained"
    CRITICAL = "critical"


class ResourceMonitor:
    """轻量级资源监控器，纯应用层指标，无外部依赖"""

    # 历史采样：5s 一次，内存环形缓冲保留最近 60 点（5 分钟），供前端趋势图
    HISTORY_INTERVAL = 5.0
    HISTORY_LIMIT = 60

    def __init__(self):
        self._concurrent_requests: int = 0
        self._api_call_timestamps: list[float] = []
        self._token_usage_window: list[tuple[float, int]] = []
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._enabled: bool = settings.RESOURCE_AWARE_ENABLED
        self._lock = asyncio.Lock()
        self._history: list[dict] = []
        self._last_sample_ts: float = 0.0

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, val: bool):
        self._enabled = val

    # ── 并发数 ──

    @property
    def concurrent_requests(self) -> int:
        return self._concurrent_requests

    # ── 每分钟 API 调用数 ──

    @property
    def api_calls_per_minute(self) -> int:
        self._prune_timestamps()
        return len(self._api_call_timestamps)

    def record_api_call(self):
        if not self._enabled:
            return
        self._api_call_timestamps.append(time.time())

    # ── Token 计数 ──

    @property
    def token_usage_this_hour(self) -> int:
        self._prune_token_window()
        return sum(cnt for _, cnt in self._token_usage_window)

    def record_tokens(self, count: int):
        if not self._enabled:
            return
        self._token_usage_window.append((time.time(), count))

    # ── 负载等级计算 ──

    @property
    def current_tier(self) -> ResourceTier:
        if not self._enabled:
            return ResourceTier.OPTIMAL
        thresholds = RESOURCE_THRESHOLDS
        c = self._concurrent_requests
        api = self.api_calls_per_minute
        tokens = self.token_usage_this_hour
        if (
            c >= thresholds["critical_at"]
            or api >= thresholds["max_api_calls_per_minute"]
            or tokens >= thresholds["token_budget_per_hour"]
        ):
            return ResourceTier.CRITICAL
        if c >= thresholds["constrained_at"]:
            return ResourceTier.CONSTRAINED
        if c > 0:
            return ResourceTier.NORMAL
        return ResourceTier.OPTIMAL

    # ── 模型选择 ──

    def get_recommended_model(self, preferred_model: Optional[str] = None) -> str:
        tier = self.current_tier
        if tier in (ResourceTier.CONSTRAINED, ResourceTier.CRITICAL):
            return MODEL_COST_TIERS["budget"]
        return preferred_model or MODEL_COST_TIERS["standard"]

    # ── 并发上限 ──

    def get_max_concurrency(self) -> int:
        tier = self.current_tier
        cap = RESOURCE_TIER_CONCURRENCY[tier.value]
        if cap == 0:
            return settings.MAX_CONCURRENT_REQUESTS
        return min(cap, settings.MAX_CONCURRENT_REQUESTS)

    # ── 准入控制 ──

    @asynccontextmanager
    async def acquire(self):
        if self._enabled:
            max_conc = self.get_max_concurrency()
            if self._semaphore is None or self._semaphore._value != max_conc:
                async with self._lock:
                    self._semaphore = asyncio.Semaphore(max_conc)
            try:
                await asyncio.wait_for(self._semaphore.acquire(), timeout=30.0)
            except asyncio.TimeoutError:
                log.warning("[ResourceMonitor] 获取信号量超时（30s）")
                raise RuntimeError("系统负载过高，请稍后重试")
        self._concurrent_requests += 1
        try:
            yield
        finally:
            self._concurrent_requests -= 1
            if self._enabled and self._semaphore:
                self._semaphore.release()

    # ── 历史采样（前端趋势图数据源）──

    def _sample_history(self):
        """惰性采样：距上次采样超过间隔才记录一个点，供趋势图展示"""
        now = time.time()
        if now - self._last_sample_ts < self.HISTORY_INTERVAL:
            return
        self._last_sample_ts = now
        self._history.append({
            "ts": now,
            "concurrent": self._concurrent_requests,
            "api_cpm": self.api_calls_per_minute,
            "token_hour": self.token_usage_this_hour,
        })
        if len(self._history) > self.HISTORY_LIMIT:
            self._history = self._history[-self.HISTORY_LIMIT:]

    def history(self, limit: int = 60) -> list[dict]:
        """返回最近 N 个采样点（含当前触发一次采样），供前端画趋势图"""
        self._sample_history()
        return self._history[-limit:]

    # ── API 快照 ──

    def snapshot(self) -> Dict[str, Any]:
        tier = self.current_tier
        thresholds = RESOURCE_THRESHOLDS
        self._sample_history()
        return {
            "tier": tier.value,
            "concurrent_requests": self._concurrent_requests,
            "api_calls_per_minute": self.api_calls_per_minute,
            "token_usage_this_hour": self.token_usage_this_hour,
            "max_concurrency": self.get_max_concurrency(),
            "model_tier": (
                MODEL_COST_TIERS["budget"]
                if tier in (ResourceTier.CONSTRAINED, ResourceTier.CRITICAL)
                else MODEL_COST_TIERS["standard"]
            ),
            "thresholds": {
                "constrained_at": thresholds["constrained_at"],
                "critical_at": thresholds["critical_at"],
                "max_api_calls_per_minute": thresholds["max_api_calls_per_minute"],
                "token_budget_per_hour": thresholds["token_budget_per_hour"],
            },
            "enabled": self._enabled,
            "history": self._history[-self.HISTORY_LIMIT:],
        }

    # ── 内部方法 ──

    def _prune_timestamps(self):
        cutoff = time.time() - 60.0
        self._api_call_timestamps = [t for t in self._api_call_timestamps if t > cutoff]

    def _prune_token_window(self):
        cutoff = time.time() - 3600.0
        self._token_usage_window = [(ts, cnt) for ts, cnt in self._token_usage_window if ts > cutoff]


resource_monitor = ResourceMonitor()
