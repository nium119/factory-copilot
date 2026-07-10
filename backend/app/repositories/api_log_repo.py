"""API 调用日志 Repository"""
import datetime
from typing import Optional
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.api_log import ApiCallLog

class ApiLogRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def insert(self, **kwargs) -> ApiCallLog:
        log = ApiCallLog(
            timestamp=kwargs.get("timestamp", datetime.datetime.now().isoformat()),
            user_id=kwargs.get("user_id", ""),
            conversation_id=kwargs.get("conversation_id", ""),
            message=kwargs.get("message", ""),
            concept=kwargs.get("concept", ""),
            method=kwargs.get("method", ""),
            url=kwargs.get("url", ""),
            status=kwargs.get("status", 0),
            elapsed_ms=kwargs.get("elapsed_ms", 0),
            error=kwargs.get("error", ""),
            request_body=kwargs.get("request_body", ""),
            response_body=kwargs.get("response_body", ""),
            context=kwargs.get("context", ""),
        )
        self.db.add(log)
        await self.db.commit()
        return log

    async def query_logs(
        self, page: int = 1, page_size: int = 15,
        user_id: str = "", concept: str = "", keyword: str = "",
        date_from: str = "", date_to: str = "",
    ) -> tuple[list[ApiCallLog], int]:
        query = select(ApiCallLog)
        count_query = select(func.count(ApiCallLog.id))

        conditions = []
        if user_id:
            conditions.append(ApiCallLog.user_id == user_id)
        if concept:
            conditions.append(ApiCallLog.concept == concept)
        if keyword:
            kw = f"%{keyword}%"
            conditions.append(
                (ApiCallLog.url.like(kw)) |
                (ApiCallLog.message.like(kw)) |
                (ApiCallLog.error.like(kw))
            )
        if date_from:
            conditions.append(ApiCallLog.timestamp >= date_from)
        if date_to:
            conditions.append(ApiCallLog.timestamp <= date_to + "T23:59:59")

        if conditions:
            for c in conditions:
                query = query.where(c)
                count_query = count_query.where(c)

        total = (await self.db.execute(count_query)).scalar() or 0
        offset = (page - 1) * page_size
        query = query.order_by(desc(ApiCallLog.id)).limit(page_size).offset(offset)
        result = await self.db.execute(query)
        return list(result.scalars().all()), total
