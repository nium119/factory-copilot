"""LLM 追踪查询 API — trace 列表 + 详情（span 瀑布数据）+ 执行质量聚合"""
import json
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.trace import AgentTrace

router = APIRouter(tags=["追踪"])


def _iso(dt) -> str:
    return dt.isoformat() if dt else ""


@router.get("/traces", summary="追踪列表")
async def list_traces(
    conversation_id: str = "",
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
):
    """列出最近追踪（可按会话过滤）"""
    query = select(AgentTrace).order_by(desc(AgentTrace.id)).limit(min(limit, 100))
    if conversation_id:
        query = query.where(AgentTrace.conversation_id == conversation_id)
    result = await db.execute(query)
    rows = result.scalars().all()
    return [{
        "trace_id": r.trace_id,
        "namespace": r.namespace,
        "user_id": r.user_id,
        "conversation_id": r.conversation_id,
        "message": r.message,
        "status": r.status,
        "total_ms": r.total_ms,
        "llm_calls": r.llm_calls,
        "total_tokens": r.total_tokens,
        "error": r.error,
        "created_at": _iso(r.created_at),
    } for r in rows]


@router.get("/traces/{trace_id}", summary="追踪详情（span 瀑布）")
async def get_trace(trace_id: str, db: AsyncSession = Depends(get_db)):
    """返回单条 trace 的完整 span 数组（供瀑布图/排查回放）"""
    result = await db.execute(select(AgentTrace).where(AgentTrace.trace_id == trace_id))
    row = result.scalars().first()
    if not row:
        raise HTTPException(404, "trace 不存在")
    spans = []
    try:
        spans = json.loads(row.spans) if row.spans else []
    except json.JSONDecodeError:
        spans = []
    return {
        "trace_id": row.trace_id,
        "namespace": row.namespace,
        "user_id": row.user_id,
        "conversation_id": row.conversation_id,
        "message": row.message,
        "status": row.status,
        "total_ms": row.total_ms,
        "llm_calls": row.llm_calls,
        "total_tokens": row.total_tokens,
        "error": row.error,
        "spans": spans,
        "created_at": _iso(row.created_at),
    }


@router.get("/summary", summary="执行质量聚合")
async def trace_summary(days: int = 7, db: AsyncSession = Depends(get_db)):
    """聚合最近 N 天 trace 的执行质量指标。

    基于 agent_traces 结构化表（替代原先对 agent_messages.extra_data 的 LIKE 反查）：
    - 总体执行数 / 失败数 / 成功率（按 status 分组）
    - 按 namespace 分组（对应多业务域）
    - 错误环节分布（解析失败 trace 的 span 数组，按 span.name 聚合）
    - 最近失败明细（可下钻到单条 trace 详情）
    """
    cutoff = datetime.now() - timedelta(days=days)

    # 1. 总体：status 分组计数
    status_rows = await db.execute(
        select(AgentTrace.status, func.count())
        .where(AgentTrace.created_at >= cutoff)
        .group_by(AgentTrace.status)
    )
    status_counts = {s: c for s, c in status_rows.all()}
    total = sum(status_counts.values())
    failed = status_counts.get("error", 0)

    # 2. 按 namespace 分组（多业务域）
    ns_rows = await db.execute(
        select(AgentTrace.namespace, AgentTrace.status, func.count())
        .where(AgentTrace.created_at >= cutoff)
        .group_by(AgentTrace.namespace, AgentTrace.status)
    )
    by_namespace: dict = {}
    for ns, status, cnt in ns_rows.all():
        d = by_namespace.setdefault(ns or "默认", {"count": 0, "failed": 0})
        d["count"] += cnt
        if status == "error":
            d["failed"] += cnt

    # 3. 错误环节分布 + 最近失败明细（仅解析失败 trace 的 spans，量级小）
    failed_rows = await db.execute(
        select(AgentTrace)
        .where(AgentTrace.created_at >= cutoff, AgentTrace.status == "error")
        .order_by(desc(AgentTrace.id))
        .limit(100)
    )
    errors: dict = {}
    recent_failures = []
    for r in failed_rows.scalars().all():
        recent_failures.append({
            "trace_id": r.trace_id,
            "namespace": r.namespace,
            "user_id": r.user_id,
            "message": r.message,
            "error": r.error,
            "created_at": _iso(r.created_at),
        })
        try:
            spans = json.loads(r.spans) if r.spans else []
        except json.JSONDecodeError:
            spans = []
        for s in spans:
            if s.get("status") == "error":
                name = s.get("name") or "未知环节"
                errors[name] = errors.get(name, 0) + 1

    return {
        "days": days,
        "total": total,
        "failed": failed,
        "success_rate": round((total - failed) / total * 100, 1) if total else 0.0,
        "by_namespace": by_namespace,
        "errors": dict(sorted(errors.items(), key=lambda x: -x[1])),
        "recent_failures": recent_failures,
    }
