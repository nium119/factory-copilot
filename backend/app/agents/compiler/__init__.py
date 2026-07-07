"""本体编译器 — 从 Neo4j 本体元数据生成 Skill、Agent、链定义。

用法:
    from app.agents.compiler import OntologyCompiler
    compiler = OntologyCompiler()
    runtime = await compiler.compile()

    # runtime.skills → 所有原子 Skill
    # runtime.agents → Agent 定义列表
    # runtime.chains → 链定义列表
"""

from app.agents.compiler.models import (
    AtomicSkill, CompositeSkill, AgentDefinition, CompiledRuntime,
    DataSource, DataSourceType, SkillParam, SkillField,
)
from app.agents.compiler.compile import OntologyCompiler
from app.agents.compiler.dynamic import DynamicPlanner

__all__ = [
    "OntologyCompiler", "DynamicPlanner",
    "AtomicSkill", "CompositeSkill", "AgentDefinition", "CompiledRuntime",
    "DataSource", "DataSourceType", "SkillParam", "SkillField",
]
