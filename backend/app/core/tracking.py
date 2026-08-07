"""行为埋点 — 静默采集路由质量信号，不增加用户操作负担。

采集维度：
  1. 每次路由的分发方式、耗时、命中概念
  2. DynamicPlanner 多步查询详情
  3. 同会话追问检测（60s 内再次提问）

所有数据写入 agent_api_call_logs 表，后续可离线分析：
  - 高频场景排行 → 优先配链
  - 改口/追问率 → 路由质量指标
  - DynamicPlanner 兜底比例 → 预定义链覆盖率
"""
import json
import time
from typing import Optional

from loguru import logger


# ── 会话追踪缓存（进程内存，不持久化）──
# {conversation_id: {"last_message": str, "last_time": float, "count": int}}
_session_tracker: dict[str, dict] = {}


def _prune_sessions():
    """清理超过 30 分钟的旧会话记录。"""
    now = time.time()
    stale = [cid for cid, v in _session_tracker.items() if now - v.get("last_time", 0) > 1800]
    for cid in stale:
        del _session_tracker[cid]


def track_route(
    conversation_id: str,
    message: str,
    action_name: str = "",
    method: str = "",       # trigger / rag_llm / llm / dynamic
    confidence: float = 0.0,
    elapsed_ms: int = 0,
    error: str = "",
    context: Optional[dict] = None,
):
    """记录一次路由分发。

    异步写入 agent_api_call_logs，不阻塞主流程。
    """
    _prune_sessions()

    # 追问检测
    prev = _session_tracker.get(conversation_id, {})
    is_followup = (time.time() - prev.get("last_time", 0)) < 60
    followup_count = prev.get("count", 0) + 1

    _session_tracker[conversation_id] = {
        "last_message": message[:200],
        "last_time": time.time(),
        "count": followup_count,
    }

    ctx = {
        "confidence": round(confidence, 4),
        "is_followup": is_followup,
        "followup_count": followup_count,
        **(context or {}),
    }

    if error:
        ctx["error"] = error

    try:
        from app.db import run_async

        async def _write():
            from app.db import get_db
            async for session in get_db():
                from app.repositories.api_log_repo import ApiLogRepository
                # 当前本体图谱项目（namespace），供行为数据按项目 Tab 区分
                _ns = ""
                try:
                    from app.services.ontology_service import ontology_service
                    _ns = getattr(ontology_service, "namespace", "") or ""
                except Exception:
                    pass
                if not _ns:
                    try:
                        from app.core.config import settings as _st
                        _ns = _st.NEO4J_NAMESPACE or ""
                    except Exception:
                        pass
                repo = ApiLogRepository(session)
                await repo.insert(
                    namespace=_ns,
                    conversation_id=conversation_id,
                    message=message[:200],
                    concept=action_name,
                    method=method,
                    status=200 if not error else 500,
                    elapsed_ms=elapsed_ms,
                    error=error[:500],
                    context=json.dumps(ctx, ensure_ascii=False),
                )
                break

        run_async(_write())
    except Exception as e:
        logger.debug(f"[Tracking] 写入失败: {e}")


def track_dynamic_steps(
    conversation_id: str,
    message: str,
    steps_taken: int,
    concepts: list[str],
    elapsed_ms: int = 0,
):
    """记录 DynamicPlanner 执行详情。"""
    track_route(
        conversation_id=conversation_id,
        message=message,
        action_name="dynamic_plan",
        method="dynamic",
        confidence=0.5,  # 兜底路由，无 confidence
        elapsed_ms=elapsed_ms,
        context={
            "steps_taken": steps_taken,
            "concepts_queried": concepts,
        },
    )
