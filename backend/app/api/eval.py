"""Agent 响应评估 API — LLM 自评 + 用户反馈 + 偏好学习"""
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.prompts import EVAL_SYSTEM_PROMPT
from app.repositories.message_repository import MessageRepository
from app.services.llm_service import llm_service

router = APIRouter(tags=["评估"])


class EvalRequest(BaseModel):
    """自评请求"""
    message: str
    response: str
    criteria: Optional[str] = None  # 自定义评估维度


class FeedbackRequest(BaseModel):
    """用户反馈请求"""
    message_id: str
    score: int  # 1-5 评分
    comment: Optional[str] = None
    agent_name: Optional[str] = None  # 用于偏好学习
    action: Optional[str] = None      # like/dislike/detail


# 数据库会话工厂 — 复用 app.db 统一引擎（WAL + pool_pre_ping）
from app.db import _engine
_async_session = async_sessionmaker(_engine, expire_on_commit=False)


async def get_db() -> AsyncSession:
    async with _async_session() as session:
        yield session


@router.post("/self", summary="LLM 自评")
async def self_eval(request: EvalRequest):
    """
    使用 LLM 对指定响应进行自评

    返回 accuracy/completeness/relevance/readability/overall 各维度 1-5 分
    """
    eval_input = f"用户问题: {request.message}\n\nAI 响应: {request.response}"
    if request.criteria:
        eval_input += f"\n\n额外评估维度: {request.criteria}"

    try:
        result = await llm_service.chat_sync(
            message=eval_input,
            session_id="eval",
            system_prompt=EVAL_SYSTEM_PROMPT,
        )

        # 尝试解析 JSON
        try:
            # 提取 JSON 块
            start = result.find('{')
            end = result.rfind('}') + 1
            if start != -1 and end > start:
                eval_data = json.loads(result[start:end])
            else:
                eval_data = {"raw": result}
        except json.JSONDecodeError:
            eval_data = {"raw": result}

        return {
            "success": True,
            "evaluation": eval_data,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"自评失败: {str(e)}")


@router.post("/feedback", summary="提交用户反馈（含偏好学习）")
async def submit_feedback(
    request: FeedbackRequest,
    db: AsyncSession = Depends(get_db),
):
    """提交反馈评分，并更新用户偏好权重"""
    if request.score < 1 or request.score > 5:
        raise HTTPException(status_code=400, detail="评分必须在 1-5 之间")

    msg_repo = MessageRepository(db)
    message = await msg_repo.get_by_id(request.message_id)
    if not message:
        raise HTTPException(status_code=404, detail="消息不存在")

    # 1. 更新消息 metadata 中的 feedback（兼容旧路径）
    existing_meta = message.metadata_dict if hasattr(message, 'metadata_dict') else {}
    existing_meta["feedback"] = {
        "score": request.score,
        "comment": request.comment,
    }
    await msg_repo.update_metadata(request.message_id, existing_meta)

    # 2. 写入独立反馈表 + 更新偏好权重
    from app.services.adaptation_service import apply_preference_tags, record_feedback

    # 推断 agent_name
    agent_name = request.agent_name or existing_meta.get("agent_name") or "analysis_monitor"

    await record_feedback(
        db=db,
        user_id="default_user",
        message_id=request.message_id,
        score=request.score,
        agent_name=agent_name,
        comment=request.comment,
        action=request.action,
    )

    # 3. 从评论中提取偏好标签
    if request.comment:
        await apply_preference_tags(db, "default_user", agent_name, request.comment)

    return {
        "success": True,
        "message_id": request.message_id,
        "score": request.score,
        "agent_name": agent_name,
    }


@router.get("/preferences", summary="获取用户偏好")
async def get_user_preferences(
    user_id: str = "default_user",
    db: AsyncSession = Depends(get_db),
):
    """获取当前用户对所有 Agent 的偏好权重"""
    from app.services.adaptation_service import get_user_preferences as get_prefs
    prefs = await get_prefs(db, user_id)
    return {"success": True, "preferences": prefs}


@router.get("/feedback/stats", summary="获取反馈统计")
async def get_feedback_stats(
    user_id: str = "default_user",
    db: AsyncSession = Depends(get_db),
):
    """获取各 Agent 的评分统计"""
    from app.repositories.feedback_repository import FeedbackRepository
    repo = FeedbackRepository(db)
    stats = await repo.get_agent_score_stats(user_id)
    return {"success": True, "stats": stats}
