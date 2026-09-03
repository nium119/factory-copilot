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

    return f"""你是一个{domain_desc}领域的智能路由助手。根据用户消息的语义，判断应该由哪个 Agent 处理，并给出意图类型。

## 可用 Agent

{agents_text}

## 路由规则

1. **intent（意图类型）**：`query`=查询/获取某数据；`analysis`=分析/方案/总结/评估；`chat`=闲聊/通用问答。
2. 查询/编码疑问（如「380000呢」「查380000的工艺路线」「有没有38开头的物料」）→ intent=query，agent 选能查该数据的业务 Agent（生产执行、产品工艺工程、质量、仓储等）；
   **不要**因为消息简短/模糊就把 intent 判成 analysis 或丢给 analysis_monitor（会凭空猜）。
3. 明确「分析/方案/影响/对比/原因/怎么改/变更/评估」类请求 → intent=analysis，agent=analysis_monitor（走分析链）。
4. 记录/创建/修改数据（建工单、录质检）→ intent=query（执行类），agent=对应业务 Agent。
5. 纯闲聊/无法判断 → intent=chat，agent=analysis_monitor。
6. confidence：0.9-1.0=确定，0.7-0.89=比较确定，0.5-0.69=不确定，0.3-0.49=猜测

## 用户消息

{message}

## 输出格式

严格输出 JSON，不要包含其他文字：
{{"agent_name": "<agent key>", "intent": "query|analysis|chat", "confidence": <0.0-1.0>, "use_agent": <true|false>, "matched_agents": []}}"""


def _agent_hints() -> list[str]:
    """从内置 Agent 类获取，覆盖编译 Agent 的基础描述。"""
    hints = []
    try:
        from app.agents import get_agent
        for name in ['analysis_monitor', 'manufacturing_execution', 'quality_management',
                      'factory_resource_management', 'inventory_logistics',
                      'master_data_management', 'engineering_definition']:
            try:
                ag = get_agent(name)
                info = ag.get_info()
                hints.append(f"{name}({info.get('display_name', name)}): {info.get('description', '')}")
            except KeyError:
                pass
    except Exception:
        pass
    if not hints:
        hints = ["analysis_monitor(分析监控): 通用分析和兜底"]
    return hints


async def route_intent(message: str, agent_name: Optional[str] = None, model_name: Optional[str] = None) -> Dict[str, Any]:
    """确定性快速路由（对齐 DSH：单次 LLM 决策，无前置路由 LLM 调用）。

    FC 决策层已含全量 query+write 工具（跨业务域），路由到哪个 Agent 不影响工具选择，
    因此不再用 LLM 语义路由（原 qwen3.6-plus 深度推理 + 非流式约 8s）。统一路由到
    production_execution（全量工具 react loop），由 FC 决策循环自己判断查询/操作/反问/结束。
    """
    if agent_name and agent_name != "auto":
        return {
            "agent_name": agent_name,
            "confidence": 1.0,
            "method": "manual",
            "use_agent": False,
            "matched_agents": [],
        }

    return {
        "agent_name": "production_execution",
        "intent": "query",
        "confidence": 1.0,
        "method": "fast_route",
        "use_agent": True,
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
