"""资源阈值管理 API — 运行时调整并发限制、API 频率、Token 预算."""

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/admin/resources", tags=["资源管理"])


class ResourceThresholdsIn(BaseModel):
    max_concurrent_requests: int = 10
    constrained_at: int = 6
    critical_at: int = 9
    max_api_calls_per_minute: int = 30
    token_budget_per_hour: int = 100000
    resource_aware_enabled: bool = True


class ResourceThresholdsOut(BaseModel):
    max_concurrent_requests: int
    constrained_at: int
    critical_at: int
    max_api_calls_per_minute: int
    token_budget_per_hour: int
    resource_aware_enabled: bool
    current_tier: str = ""
    concurrent_requests: int = 0


@router.get("", summary="获取资源阈值")
def get_thresholds():
    from app.agents.settings.resource import RESOURCE_THRESHOLDS
    from app.core.resource_monitor import resource_monitor
    from app.core.config import settings
    return ResourceThresholdsOut(
        max_concurrent_requests=settings.MAX_CONCURRENT_REQUESTS,
        constrained_at=RESOURCE_THRESHOLDS["constrained_at"],
        critical_at=RESOURCE_THRESHOLDS["critical_at"],
        max_api_calls_per_minute=RESOURCE_THRESHOLDS["max_api_calls_per_minute"],
        token_budget_per_hour=RESOURCE_THRESHOLDS["token_budget_per_hour"],
        resource_aware_enabled=resource_monitor.enabled,
        current_tier=resource_monitor.current_tier.value,
        concurrent_requests=resource_monitor.concurrent_requests,
    )


@router.put("", summary="更新资源阈值")
def update_thresholds(t: ResourceThresholdsIn):
    from app.agents.settings import resource as res_module
    from app.core.resource_monitor import resource_monitor

    res_module.RESOURCE_THRESHOLDS["constrained_at"] = t.constrained_at
    res_module.RESOURCE_THRESHOLDS["critical_at"] = t.critical_at
    res_module.RESOURCE_THRESHOLDS["max_api_calls_per_minute"] = t.max_api_calls_per_minute
    res_module.RESOURCE_THRESHOLDS["token_budget_per_hour"] = t.token_budget_per_hour
    res_module.RESOURCE_THRESHOLDS["max_concurrent_requests"] = t.max_concurrent_requests
    resource_monitor.enabled = t.resource_aware_enabled

    from app.core.logger import log
    log.info(f"[ResourceAdmin] 阈值已更新: constrained_at={t.constrained_at}, critical_at={t.critical_at}")
    return {"ok": True}
