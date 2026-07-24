"""编译器数据模型 — Skill、Agent、链的运行时表示。"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class DataSourceType(str, Enum):
    NEO4J = "neo4j"
    API = "api"
    DB = "db"


@dataclass
class DataSource:
    """Skill 的数据源配置。"""
    type: DataSourceType = DataSourceType.NEO4J
    system: str = ""           # 系统名, 如 "mes"、"srm"
    endpoint: str = ""         # API 路径, type=api 时使用
    method: str = "GET"        # HTTP 方法
    freshness: str = "cached"  # "realtime" 或 "cached"
    reason: str = ""           # 为什么选择这个数据源 (调试用)


@dataclass
class SkillParam:
    """Skill 的输入参数。"""
    name: str
    label: str = ""
    type: str = "string"
    required: bool = False
    description: str = ""
    conceptPropertyRef: str = ""


@dataclass
class SkillField:
    """Skill 的输出字段。"""
    name: str         # 属性名
    label: str = ""   # 中文标签
    type: str = ""    # 数据类型


@dataclass
class AtomicSkill:
    """单个概念的原子 Skill — 编译器从概念属性自动生成。"""
    name: str                    # "WorkOrder_query"
    display_name: str            # "工单查询"
    concept: str                 # "WorkOrder"
    concept_label: str = ""      # "工单"
    description: str = ""        # 从 concept.description 派生
    triggers: list[str] = field(default_factory=list)
    input_params: list[SkillParam] = field(default_factory=list)
    output_fields: list[SkillField] = field(default_factory=list)
    data_source: Optional[DataSource] = None
    actions: list[str] = field(default_factory=list)  # 手动定义的 action 名
    # --- LLM 工具格式 ---
    def to_tool_def(self) -> dict:
        """转为 OpenAI Function Calling 格式。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        p.name: {"type": p.type, "description": p.label or p.name}
                        for p in self.input_params
                    },
                    "required": [p.name for p in self.input_params if p.required],
                },
            },
        }


@dataclass
class CompositeSkill:
    """多概念分析链 — 编译器从关系图遍历发现或手动配置。"""
    name: str                    # "fault_diagnosis"
    display_name: str            # "设备故障诊断"
    description: str = ""        # 从路径概念描述拼接
    path: list[str] = field(default_factory=list)  # ["Equipment","AndonEvent","WorkOrder","Material"]
    steps: list[dict] = field(default_factory=list)
    triggers: list[str] = field(default_factory=list)
    source: str = "discovered"   # "discovered" | "manual"


@dataclass
class AgentDefinition:
    """编译器产出的 Agent 定义。"""
    name: str                    # "quality_equipment"
    display_name: str            # "质量设备"
    icon: str = "🤖"
    color: str = "#6c5ce7"
    description: str = ""
    project_description: str = ""  # 本体项目行业描述（来自 Neo4j Project 节点）
    system_prompt: str = ""      # 编译器从概念描述拼装
    skill_names: list[str] = field(default_factory=list)  # 持有的原子 Skill 名
    chain_names: list[str] = field(default_factory=list)  # 持有的链名
    namespace: str = ""  # 业务域 namespace，从概念继承


@dataclass
class CompiledRuntime:
    """一次编译的完整产出。"""
    skills: list[AtomicSkill] = field(default_factory=list)
    chains: list[CompositeSkill] = field(default_factory=list)
    agents: list[AgentDefinition] = field(default_factory=list)
    # LLM 上下文 — 注入给 Agent 用于动态编排
    skill_catalog_text: str = ""     # "你可以查询的概念: ..."
    relation_graph_text: str = ""    # "概念关系图: ..."
    compiled_at: str = ""
    concept_count: int = 0
