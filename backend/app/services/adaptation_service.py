"""用户适应服务 — 从反馈中学习偏好、调整路由权重"""
import json
from typing import Optional

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user_preference import UserPreference
from app.repositories.feedback_repository import FeedbackRepository

# 偏好权重更新参数
LEARNING_RATE = 0.1            # 每次反馈的学习率
POSITIVE_THRESHOLD = 4         # >=4 分视为正向反馈
DECAY_FACTOR = 0.95            # 长时间未交互的衰减因子
MIN_WEIGHT = 0.1               # 最低权重
MAX_WEIGHT = 0.95              # 最高权重
DEFAULT_WEIGHT = 0.5           # 默认权重


async def record_feedback(
    db: AsyncSession,
    user_id: str,
    message_id: str,
    score: int,
    agent_name: Optional[str] = None,
    comment: Optional[str] = None,
    action: Optional[str] = None,
) -> FeedbackRepository:
    """记录反馈并触发偏好更新"""
    repo = FeedbackRepository(db)

    # 1. 保存反馈记录
    await repo.create(
        user_id=user_id,
        message_id=message_id,
        score=score,
        agent_name=agent_name,
        comment=comment,
        action=action,
    )

    # 2. 更新用户偏好权重
    if agent_name:
        await _update_preference_weight(db, user_id, agent_name, score)

    logger.info(
        f"[Adaptation] 反馈已记录: user={user_id}, agent={agent_name}, score={score}"
    )
    return repo


async def _update_preference_weight(
    db: AsyncSession, user_id: str, agent_name: str, score: int
):
    """根据反馈更新用户对该 Agent 的偏好权重"""
    # 查找已有偏好记录
    result = await db.execute(
        select(UserPreference).where(
            UserPreference.user_id == user_id,
            UserPreference.agent_name == agent_name,
        )
    )
    pref = result.scalar_one_or_none()

    if not pref:
        pref = UserPreference(
            user_id=user_id,
            agent_name=agent_name,
            preference_weight=DEFAULT_WEIGHT,
        )
        db.add(pref)

    # 更新统计（兼容 DB 中 NULL 值）
    pref.interaction_count = (pref.interaction_count or 0) + 1
    is_positive = score >= POSITIVE_THRESHOLD
    if is_positive:
        pref.positive_count = (pref.positive_count or 0) + 1
    else:
        pref.negative_count = (pref.negative_count or 0) + 1

    # 增量更新权重（指数移动平均）
    target = 0.8 if is_positive else 0.2
    pref.preference_weight = round(
        pref.preference_weight * (1 - LEARNING_RATE) + target * LEARNING_RATE, 4
    )
    pref.preference_weight = max(MIN_WEIGHT, min(MAX_WEIGHT, pref.preference_weight))
    pref.last_interaction_agent = agent_name

    await db.commit()
    logger.debug(
        f"[Adaptation] Agent {agent_name} 权重更新: "
        f"{round(pref.preference_weight, 4)} (score={score})"
    )


async def get_user_preferences(db: AsyncSession, user_id: str) -> dict:
    """获取用户对所有 Agent 的偏好权重"""
    result = await db.execute(
        select(UserPreference).where(UserPreference.user_id == user_id)
    )
    prefs = result.scalars().all()
    return {
        p.agent_name: {
            "weight": p.preference_weight,
            "interactions": p.interaction_count,
            "positive_rate": round(p.positive_count / max(p.interaction_count, 1), 2),
        }
        for p in prefs
    }


async def get_adapted_confidence(
    db: AsyncSession,
    user_id: str,
    agent_name: str,
    base_confidence: float,
) -> float:
    """根据用户历史偏好调整路由置信度

    Args:
        base_confidence: 关键词匹配的基础置信度 (0.3 ~ 0.85)
    Returns:
        调整后的置信度
    """
    result = await db.execute(
        select(UserPreference).where(
            UserPreference.user_id == user_id,
            UserPreference.agent_name == agent_name,
        )
    )
    pref = result.scalar_one_or_none()

    if not pref or pref.interaction_count < 2:
        return base_confidence  # 样本不足，不调整

    # 权重偏离默认值的方向和幅度决定调整
    weight_delta = pref.preference_weight - DEFAULT_WEIGHT
    # 最多调整 ±0.15
    confidence_adjustment = weight_delta * 0.3

    adjusted = round(base_confidence + confidence_adjustment, 4)
    adjusted = max(0.2, min(0.95, adjusted))

    if abs(adjusted - base_confidence) > 0.05:
        logger.debug(
            f"[Adaptation] 置信度调整: {agent_name} {base_confidence}→{adjusted} "
            f"(权重={pref.preference_weight}, 交互={pref.interaction_count})"
        )

    return adjusted


async def extract_preference_tags(comment: str) -> list:
    """从用户评论中提取偏好标签（基于关键词）

    用于分析用户倾向于什么类型的回答：
    - "详细" → 偏好详细分析
    - "简洁" → 偏好简短回答
    - "表格" → 偏好结构化数据
    """
    tags = []
    tag_keywords = {
        "偏好详细": ["详细", "具体", "全面", "多说", "展开"],
        "偏好简洁": ["简洁", "简短", "简单点", "概括", "直接"],
        "偏好数据": ["表格", "数据", "数字", "统计", "图表"],
        "偏好建议": ["建议", "推荐", "方案", "怎么办", "优化"],
    }
    for tag, keywords in tag_keywords.items():
        if any(kw in comment for kw in keywords):
            tags.append(tag)
    return tags


async def apply_preference_tags(
    db: AsyncSession, user_id: str, agent_name: str, comment: str
):
    """从反馈评论中提取并应用偏好标签"""
    tags = await extract_preference_tags(comment)
    if not tags:
        return

    result = await db.execute(
        select(UserPreference).where(
            UserPreference.user_id == user_id,
            UserPreference.agent_name == agent_name,
        )
    )
    pref = result.scalar_one_or_none()
    if not pref:
        return

    existing = json.loads(pref.preference_tags) if pref.preference_tags else []
    merged = list(set(existing + tags))
    pref.preference_tags = json.dumps(merged, ensure_ascii=False)
    await db.commit()
    logger.info(f"[Adaptation] 用户偏好标签已更新: {user_id}/{agent_name} → {merged}")
