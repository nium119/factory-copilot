import time

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import settings
from app.core.logger import log


class AuthMiddleware(BaseHTTPMiddleware):
    """可选的 API Token 鉴权中间件 — 仅当 API_AUTH_TOKEN 配置后生效"""

    async def dispatch(self, request: Request, call_next):
        token = settings.API_AUTH_TOKEN
        if token and request.url.path.startswith("/api"):
            auth = request.headers.get("Authorization", "")
            if not auth or auth != f"Bearer {token}":
                return JSONResponse(
                    status_code=401,
                    content={"error": "未授权访问", "detail": "缺少或无效的 Authorization token"},
                )
        return await call_next(request)


class LoggingMiddleware(BaseHTTPMiddleware):
    """请求日志中间件"""
    async def dispatch(self, request: Request, call_next):
        # 记录请求开始时间
        start_time = time.time()

        # 记录请求信息
        log.info(f"请求开始: {request.method} {request.url}")

        # 调用下一个中间件或路由处理
        response = await call_next(request)

        # 计算请求处理时间
        process_time = time.time() - start_time

        # 记录响应信息
        log.info(
            f"请求完成: {request.method} {request.url} "
            f"状态码: {response.status_code} "
            f"耗时: {process_time:.3f}s"
        )

        # 添加处理时间到响应头
        response.headers["X-Process-Time"] = str(process_time)

        return response

