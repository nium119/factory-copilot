import time

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import settings
from app.core.logger import log


class AuthMiddleware(BaseHTTPMiddleware):
    """API 鉴权中间件 — Bearer token 校验。"""

    async def dispatch(self, request: Request, call_next):
        if not request.url.path.startswith("/api"):
            return await call_next(request)

        token = settings.API_AUTH_TOKEN
        if token:
            auth_header = request.headers.get("Authorization", "")
            if not auth_header or auth_header != f"Bearer {token}":
                return JSONResponse(
                    status_code=401,
                    content={"error": "未授权访问", "detail": "缺少或无效的 API token"},
                )

        return await call_next(request)


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
