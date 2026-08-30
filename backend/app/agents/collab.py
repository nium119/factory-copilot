# -*- coding: utf-8 -*-
"""协作意图识别：判断消息是否「多业务域协作」类复合指令。

阶段 E「任务级自治 · 多 Agent 协作」：把协作触发从散落的启发式收编成
确定性识别组件，产出 LoopPlan(kind="collab") 的输入信号。

识别规则（确定性，配置驱动，来自 config/collaboration.yaml）：
- 显式协作词（综合分析/全面/协作/汇总/所有/全部…）→ 协作
- 隐式协作词（生产线/车间/今天/概览/当前状况…）→ 协作（整体情况类，需多域汇总）
"""
from app.agents.settings.collaboration import (
    COLLABORATION_KEYWORDS,
    IMPLICIT_COLLAB_KEYWORDS,
)


def is_collab_intent(message: str) -> bool:
    """判断消息是否多业务域协作意图（确定性，配置驱动）。"""
    m = (message or "").strip()
    if not m:
        return False
    return any(k in m for k in COLLABORATION_KEYWORDS) or \
        any(k in m for k in IMPLICIT_COLLAB_KEYWORDS)


def collab_reason(message: str) -> str:
    """协作决定的审计理由（命中哪个词，供留痕）。"""
    m = (message or "").strip()
    for k in COLLABORATION_KEYWORDS:
        if k in m:
            return f"显式协作词「{k}」→ 多域协作"
    for k in IMPLICIT_COLLAB_KEYWORDS:
        if k in m:
            return f"隐式协作词「{k}」→ 整体情况需多域汇总"
    return ""
