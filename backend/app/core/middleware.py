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

class CORSMiddleware(BaseHTTPMiddleware):
    """自定义CORS中间件"""
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        # 添加CORS头
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"

        return response

class RateLimitMiddleware(BaseHTTPMiddleware):
    """限流中间件"""
    def __init__(self, app, requests_per_minute: int = 60):
        super().__init__(app)
        self.requests_per_minute = requests_per_minute
        self.request_counts = {}

    async def dispatch(self, request: Request, call_next):
        # TODO: 实现基于IP或用户的限流逻辑
        response = await call_next(request)
        return response
