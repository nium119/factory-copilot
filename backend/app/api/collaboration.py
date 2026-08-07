"""多 Agent 协作 API — 主 agent 拆解 + 并行派发 + 聚合"""
from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services.collaboration_service import collaboration_service

router = APIRouter(prefix="/collaboration", tags=["多Agent协作"])


class CollabIn(BaseModel):
    message: str
    assignments: dict = Field(default_factory=dict)  # 可选；不传则 LLM 拆解


@router.post("", summary="多 agent 协作：拆解 + 并行执行 + 聚合")
async def collaborate(body: CollabIn):
    """接收主任务，LLM 拆解为各领域 agent 子任务，并行执行后聚合结果。"""
    return await collaboration_service.collaborate(body.message, body.assignments or None)
