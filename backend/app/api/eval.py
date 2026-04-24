"""Agent 响应评估 API — LLM 自评 + 用户反馈"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
import json

from app.services.llm_service import llm_service
from app.repositories.message_repository import MessageRepository
from app.repositories.conversation_repository import ConversationRepository
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Depends

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


# DB dependency from messages.py
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.core.config import settings

_engine = create_async_engine(settings.DATABASE_URL, echo=False)
_async_session = async_sessionmaker(_engine, expire_on_commit=False)


async def get_db() -> AsyncSession:
    async with _async_session() as session:
        yield session


EVAL_SYSTEM_PROMPT = """你是一个 AI 响应质量评估器。请从以下维度评估给定的响应：
1. **准确性**：回答是否准确、无幻觉
2. **完整性**：是否覆盖了用户问题的所有方面
3. **相关性**：是否与问题直接相关
4. **可读性**：结构是否清晰、语言是否通顺

请以 JSON 格式返回评估结果：
{"accuracy": 1-5, "completeness": 1-5, "relevance": 1-5, "readability": 1-5, "overall": 1-5, "reason": "评估理由"}"""


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


@router.post("/feedback", summary="提交用户反馈")
async def submit_feedback(
    request: FeedbackRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    为指定消息提交用户反馈（评分 + 评论）

    反馈存储在消息的 metadata 中
    """
    if request.score < 1 or request.score > 5:
        raise HTTPException(status_code=400, detail="评分必须在 1-5 之间")

    msg_repo = MessageRepository(db)
    message = await msg_repo.get_by_id(request.message_id)
    if not message:
        raise HTTPException(status_code=404, detail="消息不存在")

    # 更新消息 metadata 中的 feedback
    existing_meta = message.metadata_dict if hasattr(message, 'metadata_dict') else {}
    existing_meta["feedback"] = {
        "score": request.score,
        "comment": request.comment,
    }
    await msg_repo.update_metadata(request.message_id, existing_meta)

    return {"success": True, "message_id": request.message_id, "score": request.score}
