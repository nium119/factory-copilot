from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from app.core.logger import log
import time
import json

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

