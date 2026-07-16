"""Agent 意图路由器 — LLM 语义路由"""
import json
from typing import Any, Dict, Optional

from app.agents.agent_config import AGENT_DEFINITIONS
from app.agents.settings import (
    COMPLEXITY_KEYWORDS,
    COMPLEXITY_LENGTH_THRESHOLDS,
    COMPLEXITY_MULTI_DOMAIN_BONUS,
    COMPLEXITY_RANGE,
    MODEL_SELECTION_MAP,
    MODEL_SELECTION_THRESHOLDS,
)
from app.core.logger import log
from app.core.resource_monitor import ResourceTier, resource_monitor


def _build_routing_prompt(message: str) -> str:
    """构建 LLM 路由提示词"""
    agent_list = []
    for name, cfg in AGENT_DEFINITIONS.items():
        if not cfg.get("enabled", True):
            continue
        agent_list.append(
            f"- **{name}**（{cfg['display_name']}）：{cfg['description']}"
        )

    agents_text = "\n".join(agent_list)

    # Read domain description from ontology (or use neutral default)
    domain_desc = "通用领域"
    try:
        from app.services.ontology_service import ontology_service
        meta = ontology_service.meta
        domain_desc = meta.get("description") or meta.get("projectName") or "通用领域"
    except Exception:
        pass

    return f"""你是一个{domain_desc}领域的智能路由助手。根据用户消息的语义，判断应该由哪个 Agent 处理。

## 可用 Agent

{agents_text}

## 路由规则

1. 根据用户消息的语义内容和领域术语，选择最匹配的 Agent
2. 区分同领域内的「执行操作」与「分析查询」：
   - 记录/创建/修改数据（如质检结果记录、安灯呼叫）→ production_execution
   - 查询/统计/分析数据（如质检合格率、缺陷趋势分析）→ quality_equipment
3. 如果消息涉及多个领域，判断最主要的需求，选最匹配的单个 Agent
4. 如果无法判断或消息是通用问答，选择 "analysis_monitor"
5. confidence 取值：0.9-1.0=高度确定，0.7-0.89=比较确定，0.5-0.69=不确定，0.3-0.49=猜测

## 用户消息

{message}

## 输出格式

严格输出 JSON，不要包含其他文字：
{{"agent_name": "<agent key>", "confidence": <0.0-1.0>, "use_agent": <true|false>, "matched_agents": []}}"""


def _agent_hints() -> list[str]:
    """从已注册 Agent 动态获取名称和描述供路由 prompt 使用。"""
    hints = []
    try:
        from app.agents import get_loaded_agents
        agents = get_loaded_agents()
        for name, ag in agents.items():
            info = ag.get_info()
            hints.append(f"{name}({info.get('display_name', name)}): {info.get('description', '')}")
    except Exception:
        pass
    if not hints:
        hints = ["analysis_monitor(分析监控): 通用分析和兜底"]
    return hints


async def route_intent(message: str, agent_name: Optional[str] = None) -> Dict[str, Any]:
    """LLM 语义路由 — 判断用户消息应路由到哪个 Agent。

    如果用户已手动指定 Agent，直接返回。
    LLM 调用失败时默认回退到 analysis_monitor。
    """
    if agent_name and agent_name != "auto":
        return {
            "agent_name": agent_name,
            "confidence": 1.0,
            "method": "manual",
            "use_agent": False,
            "matched_agents": [],
        }

    try:
        from app.services.llm_service import llm_service

        prompt = _build_routing_prompt(message)

        import asyncio
        routing_model = "qwen-turbo"  # 轻量模型，无 thinking，快速路由
        raw = await asyncio.wait_for(
            llm_service.chat_sync(
                message=prompt,
                system_prompt=f"你是一个精确的 JSON 路由决策器。只输出 JSON。"
                              f"可选 Agent: {', '.join(_agent_hints())}。"
                              f"无法确定时用 analysis_monitor。",
                model_name=routing_model,
            ),
            timeout=10.0,
        )

        raw = raw.strip()
        if raw.startswith("```"):
            lines = raw.split("\n")
            raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
            raw = raw.strip()

        result = json.loads(raw)
        result.setdefault("method", "llm")
        result.setdefault("matched_agents", [])
        result.setdefault("use_agent", False)
        result.setdefault("confidence", 0.5)

        log.info(
            f"LLM 路由 → {result['agent_name']} "
            f"(confidence={result['confidence']}, use_agent={result['use_agent']})"
        )
        return result

    except asyncio.TimeoutError:
        log.warning("LLM 路由超时，回退到 analysis_monitor")
    except json.JSONDecodeError as e:
        log.warning(f"LLM 路由 JSON 解析失败: {e}")
    except Exception as e:
        log.error(f"LLM 路由失败: {e}")

    return {
        "agent_name": "analysis_monitor",
        "confidence": 0.3,
        "method": "default",
        "use_agent": False,
        "matched_agents": [],
    }


def assess_query_complexity(message: str) -> int:
    """评估查询复杂度（1-10 分）"""
    score = 0

    for kw, weight in COMPLEXITY_KEYWORDS.items():
        if kw in message:
            score += weight

    if len(message) > COMPLEXITY_LENGTH_THRESHOLDS["long"]:
        score += 2
    elif len(message) > COMPLEXITY_LENGTH_THRESHOLDS["short"]:
        score += 1

    return min(max(score, COMPLEXITY_RANGE[0]), COMPLEXITY_RANGE[1])


def select_model_for_complexity(message: str, user_model: Optional[str] = None) -> Optional[str]:
    """根据查询复杂度自动选择模型"""
    if user_model:
        return user_model

    complexity = assess_query_complexity(message)
    thresholds = MODEL_SELECTION_THRESHOLDS

    if complexity <= thresholds["simple_max"]:
        model = MODEL_SELECTION_MAP["simple"]
    elif complexity <= thresholds["medium_max"]:
        model = None
    else:
        model = MODEL_SELECTION_MAP["complex"]

    if resource_monitor.enabled and resource_monitor.current_tier in (ResourceTier.CONSTRAINED, ResourceTier.CRITICAL):
        return resource_monitor.get_recommended_model(model)
    return model
