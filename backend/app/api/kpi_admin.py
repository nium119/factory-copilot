"""KPI 指标阈值管理 API — 增删改查制造 KPI 目标与告警阈值."""

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.repositories.kpi_repo import KpiThresholdRepository

router = APIRouter(prefix="/admin/kpis", tags=["KPI管理"])

_DIRECTIONS = ["higher_better", "lower_better"]
_DOMAINS = ["equipment", "quality", "scheduling", "inventory", "andon", "production"]


# ---------- seed / reload (ORM + asyncio.run 桥接) ----------

def seed_from_yaml():
    """首次运行时从 config/kpi.yaml 种子数据填充 DB"""
    from app.core.config_loader import load_yaml
    kpis = load_yaml("kpi")
    if not kpis:
        return

    async def _do():
        async for session in get_db():
            repo = KpiThresholdRepository(session)
            existing_keys = {k.kpi_key for k in await repo.list_all()}
            inserted = 0
            for key, val in kpis.items():
                if key not in existing_keys:
                    await repo.create(
                        kpi_key=key,
                        name=val.get("name", ""),
                        target=val.get("target", 0),
                        unit=val.get("unit", ""),
                        direction=val.get("direction", "higher_better"),
                        warning_threshold=val.get("warning_threshold", 0),
                        critical_threshold=val.get("critical_threshold", 0),
                        domain=val.get("domain", ""),
                    )
                    inserted += 1
            if inserted:
                from app.core.logger import log
                log.info(f"[KPI] 种子数据已写入: {inserted} 个指标")

    try:
        asyncio.run(_do())
    except RuntimeError:
        pass


def reload_kpi_module():
    """重新加载 KPI 模块的 MANUFACTURING_KPIS（从 DB）"""
    async def _do():
        async for session in get_db():
            repo = KpiThresholdRepository(session)
            kpis = {}
            for row in await repo.list_all():
                if not row.enabled:
                    continue
                kpis[row.kpi_key] = {
                    "name": row.name, "target": row.target, "unit": row.unit,
                    "direction": row.direction, "warning_threshold": row.warning_threshold,
                    "critical_threshold": row.critical_threshold, "domain": row.domain,
                }
            return kpis

    try:
        kpis = asyncio.run(_do())
    except RuntimeError:
        return

    from app.agents.settings import kpi as kpi_module
    kpi_module.MANUFACTURING_KPIS = kpis


# ---------- Pydantic schemas ----------

class KPIIn(BaseModel):
    kpi_key: str
    name: str = ""
    target: float = 0
    unit: str = ""
    direction: str = "higher_better"
    warning_threshold: float = 0
    critical_threshold: float = 0
    domain: str = ""
    enabled: bool = True


class KPIOut(BaseModel):
    kpi_key: str
    name: str
    target: float
    unit: str
    direction: str
    warning_threshold: float
    critical_threshold: float
    domain: str
    enabled: bool
    created_at: str = ""
    updated_at: str = ""


def _model_to_out(m) -> KPIOut:
    return KPIOut(
        kpi_key=m.kpi_key,
        name=m.name,
        target=m.target,
        unit=m.unit,
        direction=m.direction,
        warning_threshold=m.warning_threshold,
        critical_threshold=m.critical_threshold,
        domain=m.domain,
        enabled=m.enabled,
        created_at=m.created_at.isoformat() if m.created_at else "",
        updated_at=m.updated_at.isoformat() if m.updated_at else "",
    )


# ---------- CRUD endpoints (ORM) ----------

@router.get("", summary="列出所有 KPI 指标")
async def list_kpis(db: AsyncSession = Depends(get_db)):
    repo = KpiThresholdRepository(db)
    kpis = await repo.list_all()
    return [_model_to_out(k) for k in kpis]


@router.get("/domains", summary="获取 KPI 域列表")
def list_domains():
    return [{"value": d, "label": {
        "equipment": "设备", "quality": "质量", "scheduling": "排产",
        "inventory": "库存", "andon": "安灯", "production": "生产",
    }.get(d, d)} for d in _DOMAINS]


@router.post("", summary="新增 KPI 指标")
async def create_kpi(kpi: KPIIn, db: AsyncSession = Depends(get_db)):
    if kpi.direction not in _DIRECTIONS:
        raise HTTPException(400, f"direction 必须是 {_DIRECTIONS} 之一")
    repo = KpiThresholdRepository(db)
    existing = await repo.get_by_key(kpi.kpi_key)
    if existing:
        raise HTTPException(409, f"KPI 已存在: {kpi.kpi_key}")
    await repo.create(
        kpi_key=kpi.kpi_key,
        name=kpi.name,
        target=kpi.target,
        unit=kpi.unit,
        direction=kpi.direction,
        warning_threshold=kpi.warning_threshold,
        critical_threshold=kpi.critical_threshold,
        domain=kpi.domain,
        enabled=kpi.enabled,
    )
    reload_kpi_module()
    return {"ok": True, "kpi_key": kpi.kpi_key}


@router.put("/{kpi_key}", summary="更新 KPI 指标")
async def update_kpi(kpi_key: str, kpi: KPIIn, db: AsyncSession = Depends(get_db)):
    if kpi.direction not in _DIRECTIONS:
        raise HTTPException(400, f"direction 必须是 {_DIRECTIONS} 之一")
    repo = KpiThresholdRepository(db)
    existing = await repo.get_by_key(kpi_key)
    if not existing:
        raise HTTPException(404, f"KPI 不存在: {kpi_key}")
    await repo.update(
        kpi_key,
        name=kpi.name,
        target=kpi.target,
        unit=kpi.unit,
        direction=kpi.direction,
        warning_threshold=kpi.warning_threshold,
        critical_threshold=kpi.critical_threshold,
        domain=kpi.domain,
        enabled=kpi.enabled,
    )
    reload_kpi_module()
    return {"ok": True, "kpi_key": kpi_key}


@router.delete("/{kpi_key}", summary="删除 KPI 指标")
async def delete_kpi(kpi_key: str, db: AsyncSession = Depends(get_db)):
    repo = KpiThresholdRepository(db)
    existing = await repo.get_by_key(kpi_key)
    if not existing:
        raise HTTPException(404, f"KPI 不存在: {kpi_key}")
    await repo.delete(kpi_key)
    reload_kpi_module()
    return {"ok": True, "kpi_key": kpi_key}


@router.post("/reload", summary="从 DB 重新加载 KPI")
def reload_kpis():
    reload_kpi_module()
    from app.agents.settings.kpi import MANUFACTURING_KPIS
    return {"ok": True, "count": len(MANUFACTURING_KPIS)}
