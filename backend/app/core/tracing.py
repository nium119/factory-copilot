"""LLM 调用追踪 — trace + span 埋点，异步落库不阻塞主流程

一次对话一条 trace（agent_traces 表），记录端到端耗时、LLM 调用次数、token 用量，
spans 列存全链路 span 数组（JSON），供线上排障回放。

埋点方式：
- start_trace() / finish_trace() 包裹整个编排（root，用 try/finally 保证收尾）
- `async with span(name, kind)` 包裹同步可圈定的子步骤（记忆检索/路由/工具等）
- record_llm_call() 记录 LLM 调用次数 + token（供流式 generator 内部调用，无需圈定）

上下文用 contextvars 承载（随 asyncio 协程上下文传播，并发会话隔离）。
"""
import json
import time
import uuid
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any, Dict, List, Optional

from app.core.logger import log

# 当前活跃 trace（contextvars，随协程上下文传播，天然并发隔离）
_current_trace: ContextVar[Optional["TraceContext"]] = ContextVar("current_trace", default=None)


class TraceContext:
    """单次对话的追踪上下文"""

    def __init__(self, trace_id: str, namespace: str, user_id: str, conversation_id: str, message: str):
        self.trace_id = trace_id
        self.namespace = namespace
        self.user_id = user_id
        self.conversation_id = conversation_id
        self.message = message[:200]
        self.spans: List[Dict[str, Any]] = []
        self.llm_calls = 0
        self.total_tokens = 0
        self.status = "ok"
        self.error = ""
        self._start = time.perf_counter()
        self._stack: List[Dict[str, Any]] = []  # 当前 span 栈（严格嵌套）

    def start_span(self, name: str, kind: str) -> Dict[str, Any]:
        span = {
            "name": name,
            "kind": kind,
            "start_ms": int((time.perf_counter() - self._start) * 1000),
            "dur_ms": 0,
            "status": "ok",
            "meta": {},
        }
        self._stack.append(span)
        return span

    def end_span(self, span: Dict[str, Any], status: str = "ok", meta: Optional[Dict[str, Any]] = None):
        span["dur_ms"] = int((time.perf_counter() - self._start) * 1000) - span["start_ms"]
        span["status"] = status
        if meta:
            span["meta"].update(meta)
        self.spans.append(span)
        if self._stack and self._stack[-1] is span:
            self._stack.pop()

    def record_llm(self, tokens: int = 0, estimated: bool = False, model: str = ""):
        self.llm_calls += 1
        self.total_tokens += tokens
        # 标记到当前最内层 span（若有）
        if self._stack:
            s = self._stack[-1]
            s["meta"]["tokens"] = s["meta"].get("tokens", 0) + tokens
            if model:
                s["meta"]["model"] = model
            if estimated:
                s["meta"]["token_source"] = "estimated"

    def total_ms(self) -> int:
        return int((time.perf_counter() - self._start) * 1000)


def _resolve_namespace() -> str:
    """解析当前本体图谱项目 namespace（复用 ontology_service 缓存，回退配置）"""
    try:
        from app.services.ontology_service import ontology_service
        ns = getattr(ontology_service, "namespace", "") or ""
        if ns:
            return ns
    except Exception:
        pass
    try:
        from app.core.config import settings
        return settings.NEO4J_NAMESPACE or ""
    except Exception:
        return ""


def start_trace(
    user_id: str,
    conversation_id: str,
    message: str,
    namespace: Optional[str] = None,
) -> TraceContext:
    """开始一次对话追踪，返回 trace 上下文并设为当前"""
    trace = TraceContext(
        trace_id=uuid.uuid4().hex,
        namespace=namespace or _resolve_namespace(),
        user_id=user_id,
        conversation_id=conversation_id,
        message=message,
    )
    _current_trace.set(trace)
    return trace


def get_current_trace() -> Optional[TraceContext]:
    return _current_trace.get()


@asynccontextmanager
async def span(name: str, kind: str = "generic", **meta):
    """子步骤 span 上下文管理器（asyncio-safe）。

    用法：
        async with span("route", "llm"):
            ...
    """
    trace = _current_trace.get()
    if trace is None:
        yield None
        return
    s = trace.start_span(name, kind)
    if meta:
        s["meta"].update(meta)
    try:
        yield s
        trace.end_span(s)
    except Exception:
        trace.end_span(s, status="error")
        raise


def record_llm_call(tokens: int = 0, estimated: bool = False, model: str = ""):
    """记录一次 LLM 调用（供流式 generator 内部调用，无需圈定整个 generator）"""
    trace = _current_trace.get()
    if trace is not None:
        trace.record_llm(tokens, estimated, model)


def finish_trace(status: str = "ok", error: str = ""):
    """结束当前 trace 并异步落库（不阻塞主流程）"""
    trace = _current_trace.get()
    if trace is None:
        return
    trace.status = status
    trace.error = error[:500]
    _current_trace.set(None)
    _flush(trace)


def _flush(trace: TraceContext):
    """异步写入 agent_traces（复用 run_async，失败仅 debug 记录不阻塞）"""
    try:
        from app.db import run_async

        async def _write():
            from app.db import get_db
            from app.models.trace import AgentTrace
            async for session in get_db():
                session.add(AgentTrace(
                    trace_id=trace.trace_id,
                    namespace=trace.namespace,
                    user_id=trace.user_id,
                    conversation_id=trace.conversation_id,
                    message=trace.message,
                    status=trace.status,
                    total_ms=trace.total_ms(),
                    llm_calls=trace.llm_calls,
                    total_tokens=trace.total_tokens,
                    spans=json.dumps(trace.spans, ensure_ascii=False),
                    error=trace.error,
                ))
                await session.commit()
                break

        run_async(_write())
    except Exception as e:
        log.debug(f"[Tracing] 落库失败: {e}")
