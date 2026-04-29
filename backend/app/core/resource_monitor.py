"""资源感知监控器 — 跟踪并发数、API 调用频率、token 用量，动态限流"""
import time
import asyncio
from enum import Enum
from typing import Optional, Dict, Any
from contextlib import asynccontextmanager
from app.core.logger import log
from app.agents.settings import (
    RESOURCE_THRESHOLDS,
    RESOURCE_TIER_CONCURRENCY,
    MODEL_COST_TIERS,
)
from app.core.config import settings


class ResourceTier(str, Enum):
    OPTIMAL = "optimal"
    NORMAL = "normal"
    CONSTRAINED = "constrained"
    CRITICAL = "critical"


class ResourceMonitor:
    """轻量级资源监控器，纯应用层指标，无外部依赖"""

    def __init__(self):
        self._concurrent_requests: int = 0
        self._api_call_timestamps: list[float] = []
        self._token_usage_window: list[tuple[float, int]] = []
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._enabled: bool = settings.RESOURCE_AWARE_ENABLED
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, val: bool):
        self._enabled = val

    # ── concurrent_requests ──

    @property
    def concurrent_requests(self) -> int:
        return self._concurrent_requests

    # ── API calls per minute ──

    @property
    def api_calls_per_minute(self) -> int:
        self._prune_timestamps()
        return len(self._api_call_timestamps)

    def record_api_call(self):
        if not self._enabled:
            return
        self._api_call_timestamps.append(time.time())

    # ── Token counting ──

    @property
    def token_usage_this_hour(self) -> int:
        self._prune_token_window()
        return sum(cnt for _, cnt in self._token_usage_window)

    def record_tokens(self, count: int):
        if not self._enabled:
            return
        self._token_usage_window.append((time.time(), count))

    # ── Tier calculation ──

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

    # ── Model selection ──

    def get_recommended_model(self, preferred_model: Optional[str] = None) -> str:
        tier = self.current_tier
        if tier in (ResourceTier.CONSTRAINED, ResourceTier.CRITICAL):
            return MODEL_COST_TIERS["budget"]
        return preferred_model or MODEL_COST_TIERS["standard"]

    # ── Concurrency cap ──

    def get_max_concurrency(self) -> int:
        tier = self.current_tier
        cap = RESOURCE_TIER_CONCURRENCY[tier.value]
        if cap == 0:
            return settings.MAX_CONCURRENT_REQUESTS
        return min(cap, settings.MAX_CONCURRENT_REQUESTS)

    # ── Admission control ──

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
                log.warning("[ResourceMonitor] acquire timed out after 30s")
                raise RuntimeError("System under heavy load, please retry later")
        self._concurrent_requests += 1
        try:
            yield
        finally:
            self._concurrent_requests -= 1
            if self._enabled and self._semaphore:
                self._semaphore.release()

    # ── Snapshot for API ──

    def snapshot(self) -> Dict[str, Any]:
        tier = self.current_tier
        thresholds = RESOURCE_THRESHOLDS
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
        }

    # ── Internal ──

    def _prune_timestamps(self):
        cutoff = time.time() - 60.0
        self._api_call_timestamps = [t for t in self._api_call_timestamps if t > cutoff]

    def _prune_token_window(self):
        cutoff = time.time() - 3600.0
        self._token_usage_window = [(ts, cnt) for ts, cnt in self._token_usage_window if ts > cutoff]


resource_monitor = ResourceMonitor()
