"""反馈 Repository — 用户反馈的 CRUD + 统计分析"""
from typing import List, Optional
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.feedback import Feedback


class FeedbackRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(
        self,
        user_id: str,
        message_id: str,
        score: int,
        agent_name: Optional[str] = None,
        comment: Optional[str] = None,
        action: Optional[str] = None,
    ) -> Feedback:
        fb = Feedback(
            user_id=user_id,
            message_id=message_id,
            score=score,
            agent_name=agent_name,
            comment=comment,
            action=action,
        )
        self.db.add(fb)
        await self.db.commit()
        await self.db.refresh(fb)
        return fb

    async def get_by_user(
        self, user_id: str, limit: int = 50
    ) -> List[Feedback]:
        result = await self.db.execute(
            select(Feedback)
            .where(Feedback.user_id == user_id)
            .order_by(Feedback.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_by_message(self, message_id: str) -> Optional[Feedback]:
        result = await self.db.execute(
            select(Feedback).where(Feedback.message_id == message_id)
        )
        return result.scalar_one_or_none()

    async def get_agent_score_stats(self, user_id: str) -> List[dict]:
        """按 Agent 统计评分：avg_score, count, positive_rate"""
        result = await self.db.execute(
            select(
                Feedback.agent_name,
                func.avg(Feedback.score).label("avg_score"),
                func.count(Feedback.id).label("count"),
                func.sum(
                    func.case((Feedback.score >= 4, 1), else_=0)
                ).label("positive_count"),
            )
            .where(and_(Feedback.user_id == user_id, Feedback.agent_name.isnot(None)))
            .group_by(Feedback.agent_name)
        )
        rows = result.all()
        return [
            {
                "agent_name": r.agent_name,
                "avg_score": round(float(r.avg_score), 2),
                "count": r.count,
                "positive_rate": round(r.positive_count / r.count, 2) if r.count else 0,
            }
            for r in rows
        ]
