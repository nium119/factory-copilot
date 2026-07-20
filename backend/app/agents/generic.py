"""通用 Agent — 从编译器产出动态组装, 替代硬编码的 Agent subclass。

每个 GenericAgent 实例 = 一个 AgentDefinition 的运行时表示。
身份、工具集、system_prompt 全部来自配置，不写死一行领域代码。
"""

from typing import Optional

from loguru import logger

from app.agents.base import BaseAgent
from app.agents.compiler.models import AgentDefinition


def create_generic_agent(definition: AgentDefinition) -> BaseAgent:
    """从 AgentDefinition 创建 GenericAgent 实例。"""

    class GenericAgent(BaseAgent):
        name = definition.name
        display_name = definition.display_name
        icon = definition.icon
        color = definition.color
        description = definition.description
        project_description = definition.project_description
        system_prompt = definition.system_prompt
        namespace = definition.namespace

        async def call_tools(self, message: str) -> Optional[str]:
            return await self._call_tools_via_ontology(message)

    agent = GenericAgent()
    # 存储完整定义供运行时查询
    agent._compiled_definition = definition
    # 存储 Skill 清单
    agent._skill_names = definition.skill_names
    agent._chain_names = definition.chain_names

    logger.info(
        f"[GenericAgent] {definition.icon} {definition.display_name} "
        f"({definition.name}): {len(definition.skill_names)} skills, "
        f"{len(definition.chain_names)} chains"
    )
    return agent


def create_agents_from_runtime(runtime) -> dict[str, BaseAgent]:
    """从 CompiledRuntime 批量创建所有 GenericAgent 实例。

    返回 {agent_name: agent_instance} 字典。
    """
    from app.agents.compiler.models import CompiledRuntime

    agents = {}
    for ad in runtime.agents:
        agent = create_generic_agent(ad)
        agents[ad.name] = agent
    return agents
