"""LLM-judge — 用 LLM 评估响应质量（回归评估）

当 eval case 的 expect.judge 配置了评估标准时，调用 LLM 对响应打分
（各维度 1-5 分 + overall + 理由），低于及格线（min_score）判 fail。

与确定性断言（record_count/field_values）互补：确定性断言保证「结构对」，
LLM-judge 保证「语义质量达标」，两者共同构成回归门禁。
"""
import json
from typing import Optional

from app.core.logger import log

from .models import JudgeConfig, JudgeResult

JUDGE_SYSTEM_PROMPT = """你是一个 AI 响应质量评估器。请从以下维度评估给定的响应，每个维度 1-5 分（1 最差 5 最好）：
{criteria}

请严格以 JSON 格式返回（不要包含任何其他文字）：
{{"scores": {{"维度名": 分数, ...}}, "overall": 1-5, "reason": "评估理由"}}"""


def _build_prompt(config: JudgeConfig, question: str, response: str) -> str:
    """构造 judge 提示词（自定义 prompt 优先，支持 {question}/{response}/{reference} 占位）"""
    if config.prompt:
        return config.prompt.format(question=question, response=response, reference=config.reference)
    parts = [f"用户问题：{question}", f"待评估响应：{response}"]
    if config.reference:
        parts.append(f"参考答案：{config.reference}")
    return "\n\n".join(parts)


def _parse_result(raw: str) -> Optional[JudgeResult]:
    """解析 LLM 返回的 JSON（容错：剥离 markdown 代码块、提取首个 JSON 对象）"""
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    # 剥离 ```json ... ``` 包裹
    if text.startswith("```"):
        text = text.strip("`")
        brace = text.find("{")
        if brace >= 0:
            text = text[brace:]
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    scores = data.get("scores", {}) or {}
    try:
        overall = float(data.get("overall", 0))
    except (TypeError, ValueError):
        overall = 0.0
    return JudgeResult(
        overall=overall,
        scores={str(k): float(v) for k, v in scores.items()},
        reason=str(data.get("reason", "")),
        raw=raw,
    )


async def judge_response(
    question: str,
    response: str,
    config: JudgeConfig,
    model: Optional[str] = None,
) -> JudgeResult:
    """LLM-judge：调用 llm_service 评估响应质量"""
    from app.services.llm_service import llm_service
    prompt = _build_prompt(config, question, response)
    criteria = "\n".join(f"{i + 1}. {c}" for i, c in enumerate(config.criteria))
    system_prompt = JUDGE_SYSTEM_PROMPT.format(criteria=criteria)
    try:
        raw = await llm_service.chat_sync(prompt, system_prompt=system_prompt, model_name=model)
    except Exception as e:
        log.warning(f"[Judge] LLM 评估失败: {e}")
        return JudgeResult(overall=0.0, reason=f"judge 调用失败: {e}")
    result = _parse_result(raw)
    if result is None:
        return JudgeResult(overall=0.0, reason=f"judge 返回无法解析: {raw[:200]}", raw=raw)
    return result
