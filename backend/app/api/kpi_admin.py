"""KPI 指标阈值管理 API — 增删改查制造 KPI 目标与告警阈值."""

import json
import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/admin/kpis", tags=["KPI管理"])

_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "agent.db")
_DIRECTIONS = ["higher_better", "lower_better"]
_DOMAINS = ["equipment", "quality", "scheduling", "inventory", "andon", "production"]


def _get_db():
    import sqlite3
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_table():
    conn = _get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS kpi_thresholds (
            kpi_key TEXT PRIMARY KEY,
            name TEXT NOT NULL DEFAULT '',
            target REAL NOT NULL DEFAULT 0,
            unit TEXT NOT NULL DEFAULT '',
            direction TEXT NOT NULL DEFAULT 'higher_better',
            warning_threshold REAL NOT NULL DEFAULT 0,
            critical_threshold REAL NOT NULL DEFAULT 0,
            domain TEXT NOT NULL DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.commit()
    conn.close()


def seed_from_yaml():
    """首次运行时从 config/kpi.yaml 种子数据填充 DB"""
    from app.core.config_loader import load_yaml
    kpis = load_yaml("kpi")
    if not kpis:
        return
    _ensure_table()
    conn = _get_db()
    inserted = 0
    for key, val in kpis.items():
        existing = conn.execute("SELECT 1 FROM kpi_thresholds WHERE kpi_key=?", (key,)).fetchone()
        if not existing:
            conn.execute(
                """INSERT INTO kpi_thresholds (kpi_key, name, target, unit, direction, warning_threshold, critical_threshold, domain)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (key, val.get("name", ""), val.get("target", 0), val.get("unit", ""),
                 val.get("direction", "higher_better"), val.get("warning_threshold", 0),
                 val.get("critical_threshold", 0), val.get("domain", "")),
            )
            inserted += 1
    conn.commit()
    conn.close()
    if inserted:
        from app.core.logger import log
        log.info(f"[KPI] 种子数据已写入: {inserted} 个指标")


def reload_kpi_module():
    """重新加载 KPI 模块的 MANUFACTURING_KPIS（从 DB）"""
    conn = _get_db()
    rows = conn.execute("SELECT * FROM kpi_thresholds WHERE enabled=1").fetchall()
    conn.close()
    kpis = {}
    for row in rows:
        kpis[row["kpi_key"]] = {
            "name": row["name"], "target": row["target"], "unit": row["unit"],
            "direction": row["direction"], "warning_threshold": row["warning_threshold"],
            "critical_threshold": row["critical_threshold"], "domain": row["domain"],
        }
    from app.agents.settings import kpi as kpi_module
    kpi_module.MANUFACTURING_KPIS = kpis


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


@router.get("", summary="列出所有 KPI 指标")
def list_kpis():
    _ensure_table()
    conn = _get_db()
    try:
        rows = conn.execute("SELECT * FROM kpi_thresholds ORDER BY domain, kpi_key").fetchall()
        return [KPIOut(
            kpi_key=r["kpi_key"], name=r["name"], target=r["target"], unit=r["unit"],
            direction=r["direction"], warning_threshold=r["warning_threshold"],
            critical_threshold=r["critical_threshold"], domain=r["domain"],
            enabled=bool(r["enabled"]), created_at=r["created_at"], updated_at=r["updated_at"],
        ) for r in rows]
    finally:
        conn.close()


@router.get("/domains", summary="获取 KPI 域列表")
def list_domains():
    return [{"value": d, "label": {
        "equipment": "设备", "quality": "质量", "scheduling": "排产",
        "inventory": "库存", "andon": "安灯", "production": "生产",
    }.get(d, d)} for d in _DOMAINS]


@router.post("", summary="新增 KPI 指标")
def create_kpi(kpi: KPIIn):
    if kpi.direction not in _DIRECTIONS:
        raise HTTPException(400, f"direction 必须是 {_DIRECTIONS} 之一")
    _ensure_table()
    conn = _get_db()
    try:
        existing = conn.execute("SELECT 1 FROM kpi_thresholds WHERE kpi_key=?", (kpi.kpi_key,)).fetchone()
        if existing:
            raise HTTPException(409, f"KPI 已存在: {kpi.kpi_key}")
        conn.execute(
            """INSERT INTO kpi_thresholds (kpi_key, name, target, unit, direction, warning_threshold, critical_threshold, domain, enabled)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (kpi.kpi_key, kpi.name, kpi.target, kpi.unit, kpi.direction,
             kpi.warning_threshold, kpi.critical_threshold, kpi.domain, int(kpi.enabled)),
        )
        conn.commit()
        reload_kpi_module()
        return {"ok": True, "kpi_key": kpi.kpi_key}
    finally:
        conn.close()


@router.put("/{kpi_key}", summary="更新 KPI 指标")
def update_kpi(kpi_key: str, kpi: KPIIn):
    if kpi.direction not in _DIRECTIONS:
        raise HTTPException(400, f"direction 必须是 {_DIRECTIONS} 之一")
    _ensure_table()
    conn = _get_db()
    try:
        existing = conn.execute("SELECT 1 FROM kpi_thresholds WHERE kpi_key=?", (kpi_key,)).fetchone()
        if not existing:
            raise HTTPException(404, f"KPI 不存在: {kpi_key}")
        conn.execute(
            """UPDATE kpi_thresholds SET name=?, target=?, unit=?, direction=?, warning_threshold=?,
               critical_threshold=?, domain=?, enabled=?, updated_at=datetime('now','localtime')
               WHERE kpi_key=?""",
            (kpi.name, kpi.target, kpi.unit, kpi.direction, kpi.warning_threshold,
             kpi.critical_threshold, kpi.domain, int(kpi.enabled), kpi_key),
        )
        conn.commit()
        reload_kpi_module()
        return {"ok": True, "kpi_key": kpi_key}
    finally:
        conn.close()


@router.delete("/{kpi_key}", summary="删除 KPI 指标")
def delete_kpi(kpi_key: str):
    _ensure_table()
    conn = _get_db()
    try:
        existing = conn.execute("SELECT 1 FROM kpi_thresholds WHERE kpi_key=?", (kpi_key,)).fetchone()
        if not existing:
            raise HTTPException(404, f"KPI 不存在: {kpi_key}")
        conn.execute("DELETE FROM kpi_thresholds WHERE kpi_key=?", (kpi_key,))
        conn.commit()
        reload_kpi_module()
        return {"ok": True, "kpi_key": kpi_key}
    finally:
        conn.close()


@router.post("/reload", summary="从 DB 重新加载 KPI")
def reload_kpis():
    reload_kpi_module()
    from app.agents.settings.kpi import MANUFACTURING_KPIS
    return {"ok": True, "count": len(MANUFACTURING_KPIS)}
