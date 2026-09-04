import time

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import settings
from app.core.logger import log


class AuthMiddleware(BaseHTTPMiddleware):
    """API 鉴权中间件 — 所有 /api 请求 Bearer JWT 验签（统一认证），公开白名单例外。

    安全修复：此前仅校验静态 API_AUTH_TOKEN（默认空=不拦截），大量端点无认证。
    现在统一要求 Bearer JWT 验签（签名 + 过期），未认证返回 401。
    """

    # 公开端点（无需登录）：健康检查（登录经 /SysWebApi 代理，MES 与 FC 共享密钥验签）
    _PUBLIC_PATHS = frozenset({"/api/health"})

    async def dispatch(self, request: Request, call_next):
        if not request.url.path.startswith("/api"):
            return await call_next(request)
        if request.method == "OPTIONS":  # CORS 预检放行
            return await call_next(request)
        if request.url.path in self._PUBLIC_PATHS:
            return await call_next(request)

        # SSE 事件流：EventSource 无法自定义 Authorization header，支持 query token 验签
        # （/api/messages/events/stream?token=xxx，前端 sse.js 附带）
        if request.url.path == "/api/messages/events/stream":
            qtoken = request.query_params.get("token", "")
            if qtoken:
                from app.services.auth_service import auth_service
                if auth_service.resolve_user(qtoken):
                    return await call_next(request)

        # 统一 Bearer JWT 验签（签名 + 过期），resolve_user 内部含 session 缓存
        auth = request.headers.get("Authorization", "")
        token = auth[7:].strip() if auth.startswith("Bearer ") else ""
        if token:
            from app.services.auth_service import auth_service
            if auth_service.resolve_user(token):
                return await call_next(request)

        # 独立部署免登录模式：无 token 的请求放行（默认身份由
        # api/deps.get_current_user_id 在端点上下文注入 claims；此处只管不拦）。
        # 带 token 但验签失败的不放行——登录体系存在时坏 token 应显式失败。
        if settings.AUTH_DISABLED and not token:
            return await call_next(request)

        return JSONResponse(
            status_code=401,
            content={"error": "未授权访问", "detail": "缺少或无效的 Bearer token"},
        )


class LoggingMiddleware(BaseHTTPMiddleware):
    """请求日志中间件"""
    async def dispatch(self, request: Request, call_next):
        start_time = time.time()
        log.info(f"请求开始: {request.method} {request.url}")
        response = await call_next(request)
        process_time = time.time() - start_time
        log.info(
            f"请求完成: {request.method} {request.url} "
            f"状态码: {response.status_code} "
            f"耗时: {process_time:.3f}s"
        )
        response.headers["X-Process-Time"] = str(process_time)
        return response
