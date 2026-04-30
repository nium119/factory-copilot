"""
Prompt Chaining 引擎 — 将复杂查询分解为多步串行调用，前一步输出作为下一步输入
"""
import json as _json
import re
from dataclasses import dataclass
from typing import AsyncGenerator, Callable, Dict, List, Optional

from loguru import logger


@dataclass
class ChainStep:
    """链式调用中的单个步骤"""
    step_id: str
    description: str                          # 前端展示用
    agent_name: str                           # 执行此步骤的 Agent
    prompt_template: str                      # 含 {{key}} 占位符的提示模板
    output_key: str                           # 此步骤输出存储在上下文中的 key

    def resolve_prompt(self, context: Dict[str, str]) -> str:
        """用上下文变量替换 {{key}} 占位符"""
        result = self.prompt_template
        for key, value in context.items():
            result = result.replace(f"{{{{{key}}}}}", value)
        return result


@dataclass
class ChainDefinition:
    """预定义的提示链"""
    chain_id: str
    name: str
    description: str
    trigger_patterns: List[str]               # 触发关键词/正则
    steps: List[ChainStep]
    final_agent: str = "general"              # 最后汇总的 Agent
    final_prompt_template: str = ""           # 汇总提示模板

    def matches(self, message: str) -> bool:
        """检查消息是否匹配此链的触发模式"""
        message_lower = message.lower()
        return any(
            re.search(pattern, message_lower) if pattern.startswith("regex:") else pattern in message_lower
            for pattern in self.trigger_patterns
        )


# ── 预定义链 ─────────────────────────────────────────────

CHAIN_DEFINITIONS: List[ChainDefinition] = [
    ChainDefinition(
        chain_id="work_order_readiness",
        name="工单投产准备检查",
        description="依次检查物料齐套、设备状态、质检标准、SOP，最后汇总",
        trigger_patterns=["生产准备", "投产准备", "齐套检查", "开工检查", "准备检查", "工单.*准备"],
        steps=[
            ChainStep(
                step_id="material_check",
                description="物料齐套检查",
                agent_name="production_prep",
                prompt_template="请检查工单的物料齐套情况：{{message}}。只需报告物料齐套结果，不要做其他分析。",
                output_key="material_status",
            ),
            ChainStep(
                step_id="equipment_check",
                description="设备状态确认",
                agent_name="equipment",
                prompt_template="请确认生产设备状态是否正常：{{message}}。只需报告设备状态，不要做其他分析。",
                output_key="equipment_status",
            ),
            ChainStep(
                step_id="quality_standard",
                description="质检标准查询",
                agent_name="quality",
                prompt_template="请确认对应产品的质量检验标准：{{message}}。只需报告质检标准要点，不要做其他分析。",
                output_key="quality_standard_info",
            ),
            ChainStep(
                step_id="sop_check",
                description="SOP 确认",
                agent_name="production_prep",
                prompt_template="请确认工序对应的 SOP 作业指导书是否就绪：{{message}}。只需报告 SOP 状态，不要做其他分析。",
                output_key="sop_status",
            ),
        ],
        final_agent="production_prep",
        final_prompt_template="""请汇总以下工单准备检查结果，给出是否可投产的综合判断：

## 用户需求
{{message}}

## 物料齐套
{{material_status}}

## 设备状态
{{equipment_status}}

## 质检标准
{{quality_standard_info}}

## SOP 就绪
{{sop_status}}

请用结构化清单输出：每项用 ✅/⚠️/🔴 标注状态，最后给出"可投产"/"条件投产"/"不可投产"的结论。""",
    ),
    ChainDefinition(
        chain_id="fault_diagnosis",
        name="设备故障诊断",
        description="依次诊断设备故障、检查备件库存、评估排产影响，最后汇总",
        trigger_patterns=["故障诊断", "设备.*故障", "设备.*坏", "设备.*异常", "停机.*原因", "诊断.*故障"],
        steps=[
            ChainStep(
                step_id="diagnose",
                description="设备故障诊断",
                agent_name="equipment",
                prompt_template="请诊断以下设备故障：{{message}}。请给出详细诊断结果。",
                output_key="diagnosis_result",
            ),
            ChainStep(
                step_id="spare_parts",
                description="备件库存检查",
                agent_name="inventory",
                prompt_template="根据以下设备诊断结果，检查所需备件的库存情况：{{diagnosis_result}}。请列出备件库存状态。",
                output_key="spare_parts_status",
            ),
            ChainStep(
                step_id="schedule_impact",
                description="排产影响评估",
                agent_name="scheduling",
                prompt_template="根据以下设备故障和备件情况，评估对排产计划的影响：\n故障诊断：{{diagnosis_result}}\n备件库存：{{spare_parts_status}}\n原始问题：{{message}}",
                output_key="schedule_impact_info",
            ),
        ],
        final_agent="equipment",
        final_prompt_template="""请汇总以下设备故障诊断全貌，给出综合处理建议：

## 故障诊断
{{diagnosis_result}}

## 备件库存
{{spare_parts_status}}

## 排产影响
{{schedule_impact_info}}

请按优先级给出处理步骤（紧急措施 → 根因修复 → 预防措施），含预期时间线。""",
    ),
    ChainDefinition(
        chain_id="quality_analysis",
        name="质量缺陷分析",
        description="依次查询质量数据、分析根因、生成改善建议，最后汇总",
        trigger_patterns=["质量.*分析", "缺陷.*分析", "不良.*分析", "质检.*分析", "质量.*改善", "质量.*改进"],
        steps=[
            ChainStep(
                step_id="quality_data",
                description="质量数据查询",
                agent_name="quality",
                prompt_template="请查询质量检测数据和合格率：{{message}}。给出详细数据。",
                output_key="quality_data_info",
            ),
            ChainStep(
                step_id="root_cause",
                description="缺陷根因分析",
                agent_name="quality",
                prompt_template="基于以下质量数据，进行根因分析（按 5-Why 框架）：{{quality_data_info}}。请追溯到深层根因。",
                output_key="root_cause_info",
            ),
            ChainStep(
                step_id="improvement",
                description="改善措施建议",
                agent_name="quality",
                prompt_template="基于以下根因分析，给出具体的改善措施建议：{{root_cause_info}}。请按紧急/短期/长期分级。",
                output_key="improvement_info",
            ),
        ],
        final_agent="quality",
        final_prompt_template="""请汇总以下质量分析全貌：

## 质量数据
{{quality_data_info}}

## 根因分析
{{root_cause_info}}

## 改善建议
{{improvement_info}}

原始问题：{{message}}

请用结构化清单输出完整的质量分析报告，含数据、根因、措施的完整链路。""",
    ),
    ChainDefinition(
        chain_id="production_report",
        name="生产综合报告",
        description="依次查询排产、质量、设备、库存，最后汇总为综合生产报告",
        trigger_patterns=["生产.*报告", "综合.*报告", "生产.*总结", "车间.*报告", "产线.*报告"],
        steps=[
            ChainStep(
                step_id="schedule_status",
                description="排产状态",
                agent_name="scheduling",
                prompt_template="请查询当前排产状态和产能数据：{{message}}。",
                output_key="schedule_status_info",
            ),
            ChainStep(
                step_id="quality_status",
                description="质量概况",
                agent_name="quality",
                prompt_template="请查询当前质量概况：{{message}}。",
                output_key="quality_status_info",
            ),
            ChainStep(
                step_id="equipment_status",
                description="设备概况",
                agent_name="equipment",
                prompt_template="请查询当前设备运行概况：{{message}}。",
                output_key="equipment_status_info",
            ),
            ChainStep(
                step_id="inventory_status",
                description="库存概况",
                agent_name="inventory",
                prompt_template="请查询当前库存和物料概况：{{message}}。",
                output_key="inventory_status_info",
            ),
        ],
        final_agent="general",
        final_prompt_template="""请汇总以下生产综合数据，生成一份简明的生产日报：

## 排产状态
{{schedule_status_info}}

## 质量概况
{{quality_status_info}}

## 设备概况
{{equipment_status_info}}

## 库存概况
{{inventory_status_info}}

请使用表格和关键指标展示，标注异常项。""",
    ),
]


class ChainEngine:
    """提示链执行引擎"""

    def __init__(self, chains: List[ChainDefinition] = None):
        self.chains = chains or CHAIN_DEFINITIONS
        self._agent_resolver: Optional[Callable] = None

    def detect(self, message: str) -> Optional[ChainDefinition]:
        """检测消息是否触发某个链"""
        for chain in self.chains:
            if chain.matches(message):
                logger.info(f"[ChainEngine] 检测到链: {chain.chain_id} ({chain.name})")
                return chain
        return None

    def set_agent_resolver(self, resolver: Callable):
        """设置 Agent 解析器，用于按名称获取 Agent 实例"""
        self._agent_resolver = resolver

    async def execute(
        self,
        chain: ChainDefinition,
        message: str,
        model_name: Optional[str] = None,
        enable_thinking: Optional[bool] = None,
        session_id: str = "",
        history_messages: list = None,
    ) -> AsyncGenerator[tuple, None]:
        """
        执行提示链，逐步产出 SSE 事件

        Yields:
            (type, content) 元组
        """
        if not self._agent_resolver:
            logger.error("[ChainEngine] Agent resolver 未设置")
            yield ('error', 'Chain engine not properly initialized')
            return

        # 发送链开始事件
        steps_summary = [
            {"step_id": s.step_id, "description": s.description, "agent_name": s.agent_name}
            for s in chain.steps
        ]
        yield ('chain_start', _json.dumps({
            "chain_id": chain.chain_id,
            "chain_name": chain.name,
            "steps": steps_summary,
        }))
        logger.info(f"[ChainEngine] chain_start: {chain.chain_id}, {len(chain.steps)} 步")

        context: Dict[str, str] = {"message": message}
        full_outputs: Dict[str, str] = {}

        for step in chain.steps:
            # 发送步骤开始事件
            yield ('chain_step', _json.dumps({
                "step_id": step.step_id,
                "status": "running",
                "description": step.description,
                "agent_name": step.agent_name,
            }))
            logger.info(f"[ChainEngine] 步骤开始: {step.step_id} → {step.agent_name}")

            try:
                prompt = step.resolve_prompt(context)
                agent = self._agent_resolver(step.agent_name)

                if agent is None:
                    error_msg = f"Agent not found: {step.agent_name}"
                    yield ('chain_step', _json.dumps({
                        "step_id": step.step_id,
                        "status": "error",
                        "error": error_msg,
                    }))
                    context[step.output_key] = f"[错误] {error_msg}"
                    continue

                step_response = ""
                async for chunk_type, chunk_content in agent.process(
                    message=prompt,
                    session_id=session_id,
                    model_name=model_name,
                    use_agent=False,
                    web_search=False,
                    enable_thinking=enable_thinking,
                    context=None,
                    history_messages=history_messages or [],
                    matched_agents=[],
                ):
                    if chunk_type == 'content':
                        step_response += chunk_content
                        yield ('content', chunk_content)

                context[step.output_key] = step_response
                full_outputs[step.step_id] = step_response

                # 发送步骤完成事件
                yield ('chain_step', _json.dumps({
                    "step_id": step.step_id,
                    "status": "done",
                    "description": step.description,
                    "output_preview": step_response[:200] + ("..." if len(step_response) > 200 else ""),
                }))
                logger.info(f"[ChainEngine] 步骤完成: {step.step_id}, 输出 {len(step_response)} 字符")

            except Exception as e:
                logger.error(f"[ChainEngine] 步骤异常: {step.step_id}: {e}")
                yield ('chain_step', _json.dumps({
                    "step_id": step.step_id,
                    "status": "error",
                    "error": str(e),
                }))
                context[step.output_key] = f"[错误] {str(e)}"

        # 汇总阶段
        if chain.final_prompt_template and chain.final_agent:
            final_prompt = chain.final_prompt_template
            for key, value in context.items():
                final_prompt = final_prompt.replace(f"{{{{{key}}}}}", value)

            yield ('chain_summary', _json.dumps({
                "chain_id": chain.chain_id,
                "agent_name": chain.final_agent,
            }))

            final_agent = self._agent_resolver(chain.final_agent)
            if final_agent:
                async for chunk_type, chunk_content in final_agent.process(
                    message=final_prompt,
                    session_id=session_id,
                    model_name=model_name,
                    use_agent=False,
                    web_search=False,
                    enable_thinking=enable_thinking,
                    context=None,
                    history_messages=history_messages or [],
                    matched_agents=[],
                ):
                    if chunk_type == 'content':
                        yield ('content', chunk_content)

        # 发送链完成事件
        yield ('chain_done', _json.dumps({
            "chain_id": chain.chain_id,
            "steps_completed": len([s for s in chain.steps if context.get(s.output_key, "").startswith("[错误]") is False]),
            "total_steps": len(chain.steps),
        }))
        logger.info(f"[ChainEngine] chain_done: {chain.chain_id}")


# 全局单例
chain_engine = ChainEngine()
