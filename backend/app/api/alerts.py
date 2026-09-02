"""告警管理 API — 查询、确认、解决告警"""
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.logger import log
from app.repositories.alert_repository import AlertRepository

router = APIRouter(prefix="/alerts", tags=["告警管理"])

# 复用 app.db 统一引擎（WAL + pool_pre_ping，避免多连接池各自失效）
from app.db import _engine
_async_session = async_sessionmaker(_engine, expire_on_commit=False)


async def get_db() -> AsyncSession:
    async with _async_session() as session:
        yield session


def _alert_to_dict(alert) -> dict:
    return {
        "id": alert.id,
        "rule_name": alert.rule_name,
        "rule_label": alert.rule_label,
        "concept_name": alert.concept_name,
        "entity_id": alert.entity_id,
        "severity": alert.severity,
        "status": alert.status,
        "agents": json.loads(alert.agents or "[]"),
        "trigger_condition": alert.trigger_condition,
        "description": alert.description,
        "created_at": alert.created_at,
        "acknowledged_at": alert.acknowledged_at,
        "resolved_at": alert.resolved_at,
    }


@router.get("", summary="获取活跃告警列表")
async def list_alerts(
    limit: int = 50,
    agent_name: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """查询当前活跃（detected/escalated）的告警，可按 Agent 过滤。"""
    repo = AlertRepository(db)
    alerts = await repo.list_active(
        limit=limit, agent_name=agent_name,
    )
    return {
        "success": True,
        "count": len(alerts),
        "alerts": [_alert_to_dict(a) for a in alerts],
    }


@router.post("/{alert_id}/acknowledge", summary="确认告警")
async def acknowledge_alert(alert_id: str, db: AsyncSession = Depends(get_db)):
    """将告警标记为已确认（acknowledged）。"""
    repo = AlertRepository(db)
    alert = await repo.acknowledge(alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="告警不存在")
    log.info(f"[告警] 已确认: {alert_id}")
    return {"success": True, "alert": _alert_to_dict(alert)}


@router.post("/{alert_id}/resolve", summary="解决告警")
async def resolve_alert(alert_id: str, db: AsyncSession = Depends(get_db)):
    """将告警标记为已解决（resolved）。"""
    repo = AlertRepository(db)
    alert = await repo.resolve(alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="告警不存在")
    log.info(f"[告警] 已解决: {alert_id}")
    return {"success": True, "alert": _alert_to_dict(alert)}


@router.get("/count", summary="获取活跃告警数量")
async def alert_count(
    agent_name: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """返回当前活跃告警总数，用于前端角标显示。"""
    repo = AlertRepository(db)
    alerts = await repo.list_active(
        limit=200, agent_name=agent_name,
    )
    high = sum(1 for a in alerts if a.severity == "high")
    return {
        "success": True,
        "total": len(alerts),
        "high": high,
    }
