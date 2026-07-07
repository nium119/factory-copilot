"""编译器基础测试 — 验证编译流程的核心逻辑。"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from app.agents.compiler.models import (
    AtomicSkill, CompositeSkill, AgentDefinition, CompiledRuntime,
    DataSource, DataSourceType, SkillParam, SkillField,
)


class TestAtomicSkill:
    """原子 Skill 模型测试"""

    def test_skill_creation(self):
        skill = AtomicSkill(
            name="WorkOrder_query",
            display_name="工单查询",
            concept="WorkOrder",
            concept_label="工单",
        )
        assert skill.name == "WorkOrder_query"
        assert skill.display_name == "工单查询"
        assert skill.concept == "WorkOrder"

    def test_to_tool_def(self):
        skill = AtomicSkill(
            name="Test_query",
            display_name="测试查询",
            concept="Test",
            description="测试描述",
            input_params=[
                SkillParam(name="code", label="编码", type="string", required=True),
                SkillParam(name="status", label="状态", type="string"),
            ],
        )
        tool_def = skill.to_tool_def()
        assert tool_def["type"] == "function"
        assert tool_def["function"]["name"] == "Test_query"
        assert "code" in tool_def["function"]["parameters"]["properties"]
        assert "code" in tool_def["function"]["parameters"]["required"]

    def test_skill_default_triggers(self):
        skill = AtomicSkill(name="x", display_name="y", concept="z")
        assert skill.triggers == []
        assert skill.actions == []


class TestCompositeSkill:
    """复合 Skill 模型测试"""

    def test_chain_creation(self):
        chain = CompositeSkill(
            name="test_chain",
            display_name="测试链",
            path=["A", "B", "C"],
            source="discovered",
        )
        assert chain.name == "test_chain"
        assert len(chain.path) == 3
        assert chain.source == "discovered"


class TestAgentDefinition:
    """Agent 定义模型测试"""

    def test_agent_definition(self):
        agent = AgentDefinition(
            name="test_agent",
            display_name="测试Agent",
            icon="🤖",
            system_prompt="你是测试助手",
            skill_names=["A_query", "B_query"],
            chain_names=["chain_1"],
        )
        assert agent.display_name == "测试Agent"
        assert len(agent.skill_names) == 2
        assert len(agent.chain_names) == 1


class TestCompiledRuntime:
    """编译结果模型测试"""

    def test_empty_runtime(self):
        runtime = CompiledRuntime()
        assert runtime.skills == []
        assert runtime.chains == []
        assert runtime.agents == []
        assert runtime.concept_count == 0

    def test_runtime_with_data(self):
        skills = [
            AtomicSkill(name="A_query", display_name="A查询", concept="A"),
            AtomicSkill(name="B_query", display_name="B查询", concept="B"),
        ]
        agents = [
            AgentDefinition(name="agent1", display_name="Agent1", skill_names=["A_query"]),
        ]
        runtime = CompiledRuntime(
            skills=skills,
            agents=agents,
            concept_count=2,
            compiled_at="2025-01-01T00:00:00",
        )
        assert len(runtime.skills) == 2
        assert len(runtime.agents) == 1
        assert runtime.concept_count == 2


class TestDataSource:
    """数据源模型测试"""

    def test_neo4j_source(self):
        ds = DataSource(type=DataSourceType.NEO4J, freshness="cached")
        assert ds.type == DataSourceType.NEO4J

    def test_api_source(self):
        ds = DataSource(type=DataSourceType.API, system="mes", freshness="realtime")
        assert ds.type == DataSourceType.API
        assert ds.system == "mes"
