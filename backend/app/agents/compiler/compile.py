"""本体编译器核心 — 读 Neo4j 本体元数据 → 生成 Skill + Agent + 链。"""

import re
from datetime import datetime
from typing import Optional

from loguru import logger

from app.agents.compiler.models import (
    AtomicSkill, CompositeSkill, AgentDefinition, CompiledRuntime,
    DataSource, DataSourceType, SkillParam, SkillField,
)


class OntologyCompiler:
    """从 Neo4j 本体元数据编译出完整的 Agent 运行时。"""

    def __init__(self):
        self._concepts: list[dict] = []
        self._concept_map: dict[str, dict] = {}
        self._parent_children: dict[str, list[str]] = {}  # 父概念 → [子概念]

    # ── 公共入口 ──────────────────────────────────────────────

    async def compile(self) -> CompiledRuntime:
        """执行完整编译：加载本体 → 生成 Skill → 发现链 → 组装 Agent。"""
        logger.info("[Compiler] 开始编译...")

        # 1. 从 Neo4j 加载本体元数据
        await self._load_ontology()

        # 2. 生成原子 Skill
        skills = self._generate_atomic_skills()

        # 3. 发现复合 Skill (链)
        chains = self._discover_composite_skills(skills)

        # 4. 组装 Agent 定义
        agents = self._assemble_agents(skills, chains)

        # 5. 生成 LLM 动态编排上下文
        skill_catalog = self._build_skill_catalog(skills)
        relation_graph = self._build_relation_graph()

        runtime = CompiledRuntime(
            skills=skills,
            chains=chains,
            agents=agents,
            skill_catalog_text=skill_catalog,
            relation_graph_text=relation_graph,
            compiled_at=datetime.now().isoformat(),
            concept_count=len(self._concepts),
        )
        logger.info(
            f"[Compiler] 编译完成: {len(skills)} 原子Skill, "
            f"{len(chains)} 复合Skill, {len(agents)} Agent"
        )
        return runtime

    # ── 本体加载 ──────────────────────────────────────────────

    async def _load_ontology(self):
        """从 Neo4j 加载概念、属性、关系、动作、规则。"""
        from app.services.ontology_service import ontology_service

        # 确保本体数据已加载
        if not ontology_service._data:
            await ontology_service.reload()

        self._concepts = ontology_service.get_concepts() or []
        self._concept_map = {c["name"]: c for c in self._concepts}

        # 构建父子关系索引
        self._parent_children = {}
        for c in self._concepts:
            for p in c.get("parents", []):
                self._parent_children.setdefault(p, []).append(c["name"])

    # ── 原子 Skill 生成 ───────────────────────────────────────

    def _generate_atomic_skills(self) -> list[AtomicSkill]:
        """每个概念生成一个 query Skill + 关联手动定义的 actions。"""
        skills = []
        for concept in self._concepts:
            name = concept["name"]
            label = concept.get("label", name)
            desc = concept.get("description", "")

            # 只处理有 label 的业务概念, 跳过纯枚举/字典概念
            if not label or label == name:
                continue

            # 跳过抽象父概念 (有子概念但自身不是业务实体)
            has_children = name in self._parent_children
            has_primary = any(p.get("isPrimary") for p in concept.get("properties", []))
            if has_children and not has_primary:
                continue

            # 生成 query Skill
            skill = AtomicSkill(
                name=f"{name}_query",
                display_name=f"{label}查询",
                concept=name,
                concept_label=label,
                description=desc or f"查询{label}数据",
                triggers=self._extract_triggers(concept),
                input_params=self._extract_input_params(concept),
                output_fields=self._extract_output_fields(concept),
                data_source=self._determine_data_source(concept),
                actions=[
                    a["name"] for a in concept.get("actions", [])
                    if a.get("name") != "query"  # query 已经是 Skill 本身
                ],
            )
            skills.append(skill)

        return skills

    def _extract_triggers(self, concept: dict) -> list[str]:
        """从概念 label + 属性 label 提取触发关键词。"""
        triggers = []
        label = concept.get("label", "")
        if label:
            triggers.append(label)

        for prop in concept.get("properties", []):
            pl = prop.get("label", "")
            if pl and len(pl) >= 2 and pl != label:
                triggers.append(pl)

        return list(set(triggers))  # 去重

    def _extract_input_params(self, concept: dict) -> list[SkillParam]:
        """提取主键 + 索引属性作为查询参数。"""
        params = []
        for prop in concept.get("properties", []):
            if prop.get("isPrimary"):
                params.append(SkillParam(
                    name=prop["name"],
                    label=prop.get("label", prop["name"]),
                    type=self._map_type(prop.get("type", "string")),
                    required=False,  # 查询参数可选, 用于过滤
                    description=prop.get("description", ""),
                ))
        return params

    def _extract_output_fields(self, concept: dict) -> list[SkillField]:
        """全部属性作为输出字段。"""
        return [
            SkillField(
                name=p["name"],
                label=p.get("label", p["name"]),
                type=p.get("type", "string"),
            )
            for p in concept.get("properties", [])
        ]

    def _determine_data_source(self, concept: dict) -> DataSource:
        """根据概念特征决定数据源。"""
        has_relations = bool(concept.get("relations", []))
        has_rules = bool(concept.get("rules", []))
        mappings = [
            p.get("mappings", [])
            for p in concept.get("properties", [])
        ]
        has_external_mapping = any(m for ml in mappings for m in ml if m.get("system"))

        # 有跨概念关系或计算规则 → Neo4j (需要图遍历)
        if has_relations or has_rules:
            return DataSource(
                type=DataSourceType.NEO4J,
                freshness="cached",
                reason="有跨概念关系或计算规则, 需要图遍历",
            )

        # 无关系但有外部系统映射 → API 直查
        if has_external_mapping:
            system = ""
            for ml in mappings:
                for m in ml:
                    if m.get("system"):
                        system = m["system"]
                        break
                if system:
                    break
            return DataSource(
                type=DataSourceType.API,
                system=system,
                freshness="realtime",
                reason=f"映射到外部系统 {system}, 可直查 API",
            )

        # 默认 → Neo4j
        return DataSource(type=DataSourceType.NEO4J, freshness="cached")

    @staticmethod
    def _map_type(ont_type: str) -> str:
        """本体类型 → JSON Schema 类型。"""
        return {
            "string": "string", "text": "string",
            "int": "integer", "float": "number", "number": "number",
            "bool": "boolean", "boolean": "boolean",
            "datetime": "string", "date": "string",
            "enum": "string", "ref": "string",
        }.get(ont_type, "string")

    # ── 复合 Skill 发现 ──────────────────────────────────────

    def _discover_composite_skills(
        self, skills: list[AtomicSkill]
    ) -> list[CompositeSkill]:
        """从关系图 BFS 遍历, 发现多跳分析路径。"""
        chains = []

        # 选择遍历起点: 有最多出边关系的概念
        start_candidates = sorted(
            self._concepts,
            key=lambda c: len(c.get("relations", [])),
            reverse=True,
        )[:5]  # 最多考虑 5 个起点

        for concept in start_candidates:
            paths = self._bfs_paths(concept["name"], max_depth=3, min_nodes=3)
            for path in paths:
                if self._is_valid_chain_path(path):
                    chain = self._build_chain_from_path(path, skills)
                    if chain:
                        chains.append(chain)

        # 去重: 同一组概念只保留一个链
        seen = set()
        unique = []
        for c in chains:
            key = tuple(sorted(c.path))
            if key not in seen:
                seen.add(key)
                unique.append(c)

        logger.info(f"[Compiler] 发现 {len(unique)} 条候选链 (去重后)")
        return unique

    def _bfs_paths(
        self, start: str, max_depth: int, min_nodes: int
    ) -> list[list[str]]:
        """BFS 遍历关系图, 收集从 start 出发的所有路径。"""
        paths = []
        queue = [([start], {start})]

        while queue:
            path, visited = queue.pop(0)
            current = path[-1]

            if len(path) > max_depth:
                continue

            if len(path) >= min_nodes:
                paths.append(list(path))

            concept = self._concept_map.get(current, {})
            for rel in concept.get("relations", []):
                target = rel.get("target", "")
                if target and target in self._concept_map and target not in visited:
                    queue.append((path + [target], visited | {target}))

        return paths

    def _is_valid_chain_path(self, path: list[str]) -> bool:
        """检查路径是否适合作为分析链。"""
        # 过滤纯枚举/字典概念
        for cn in path:
            c = self._concept_map.get(cn, {})
            if not c.get("label") or c.get("label") == cn:
                return False
        # 至少有一个概念有 query Skill
        return True

    def _build_chain_from_path(
        self, path: list[str], skills: list[AtomicSkill]
    ) -> Optional[CompositeSkill]:
        """从概念路径构建链定义。"""
        skill_map = {s.concept: s for s in skills}
        path_labels = []
        steps = []

        for i, cn in enumerate(path):
            c = self._concept_map.get(cn, {})
            label = c.get("label", cn)
            path_labels.append(label)

            if cn in skill_map:
                steps.append({
                    "step_id": f"step_{i+1}",
                    "description": f"{label}分析" if i > 0 else f"查询{label}",
                    "concept": cn,
                    "focus_concepts": cn,
                    "prompt_template": self._generate_step_prompt(
                        cn, label, i, path, path_labels
                    ),
                    "output_key": f"{cn}_result",
                })

        if len(steps) < 2:
            return None

        # 加汇总步骤
        steps.append({
            "step_id": "summary",
            "description": "综合汇总",
            "concept": "",
            "focus_concepts": "",
            "prompt_template": self._generate_summary_prompt(path_labels),
            "output_key": "summary_result",
        })

        chain_name = f"{path_labels[0]}分析链"
        return CompositeSkill(
            name=f"chain_{path[0].lower()}",
            display_name=chain_name,
            description=f"自动发现的{'→'.join(path_labels)}分析路径",
            path=path,
            steps=steps,
            triggers=[path_labels[0]],
            source="discovered",
        )

    @staticmethod
    def _generate_step_prompt(
        cn: str, label: str, idx: int, path: list[str], path_labels: list[str]
    ) -> str:
        """为链步骤生成提示词模板。"""
        if idx == 0:
            return f"查询{label}数据，分析关键发现。\n\n## 实时数据\n{{data_context}}\n\n## 用户问题\n{{message}}"
        return f"基于前序分析结果评估{label}影响。\n\n## 实时数据\n{{data_context}}"

    @staticmethod
    def _generate_summary_prompt(labels: list[str]) -> str:
        """生成汇总步骤提示词。"""
        parts = "\n".join(f"## {l}\n{{{l}_result}}" for l in labels)
        return f"{parts}\n\n## 用户问题\n{{message}}\n\n汇总输出 P0/P1/P2 行动项。"

    # ── Agent 组装 ────────────────────────────────────────────

    def _assemble_agents(
        self, skills: list[AtomicSkill], chains: list[CompositeSkill]
    ) -> list[AgentDefinition]:
        """从领域配置 + 概念树组装 Agent 定义。"""
        domains = self._load_domain_config()

        agents = []
        for agent_name, config in domains.items():
            domain_concepts = set(config.get("concepts", []))

            # 该 Agent 持有的 Skill
            agent_skills = [
                s for s in skills
                if s.concept in domain_concepts
            ]
            # 该 Agent 持有的链 (链的所有概念都在此领域内)
            agent_chains = [
                c for c in chains
                if all(p in domain_concepts for p in c.path)
            ]

            # 从领域配置读取身份
            display_name = config.get("display_name", agent_name)
            icon = config.get("icon", "🤖")
            color = config.get("color", "#6c5ce7")

            # 拼装系统提示词
            system_prompt = self._assemble_system_prompt(
                agent_name, display_name, agent_skills, agent_chains
            )

            agents.append(AgentDefinition(
                name=agent_name,
                display_name=display_name,
                icon=icon,
                color=color,
                description=config.get("description", ""),
                system_prompt=system_prompt,
                skill_names=[s.name for s in agent_skills],
                chain_names=[c.name for c in agent_chains],
            ))

        return agents

    def _load_domain_config(self) -> dict:
        """加载领域分组配置 (compiler_domains.yaml)。"""
        import os
        import yaml

        config_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "..",
            "config", "compiler_domains.yaml",
        )
        if os.path.exists(config_path):
            with open(config_path, encoding="utf-8") as f:
                return yaml.safe_load(f) or {}

        # 回退: 从本体 parents 树自动推导
        logger.warning("[Compiler] compiler_domains.yaml 不存在, 从本体自动推导")
        return self._derive_domains_from_ontology()

    def _derive_domains_from_ontology(self) -> dict:
        """从本体的顶层父概念自动推导领域分组。"""
        domains = {}
        # 找顶层父概念 (不在任何概念的 parents 中)
        all_parents = set()
        for c in self._concepts:
            all_parents.update(c.get("parents", []))

        root_concepts = [
            c for c in self._concepts
            if c["name"] not in all_parents
            and c.get("label")
            and c.get("label") != c["name"]
        ]

        for root in root_concepts:
            name = f"agent_{root['name'].lower()}"
            # 收集该根节点下的所有子概念
            children = self._collect_children(root["name"])
            concepts = [root["name"]] + children
            domains[name] = {
                "display_name": root.get("label", root["name"]),
                "description": root.get("description", ""),
                "icon": "🤖",
                "color": "#6c5ce7",
                "concepts": concepts,
            }

        return domains

    def _collect_children(self, parent: str) -> list[str]:
        """递归收集所有子概念名。"""
        result = []
        for child in self._parent_children.get(parent, []):
            result.append(child)
            result.extend(self._collect_children(child))
        return result

    def _assemble_system_prompt(
        self, name: str, display_name: str,
        skills: list[AtomicSkill], chains: list[CompositeSkill],
    ) -> str:
        """从 Skill 列表拼装 Agent 系统提示词。"""
        parts = [f"你是 {display_name}（{name}），制造业智能助手。\n"]

        if skills:
            parts.append("## 你可以查询的概念")
            for s in skills:
                parts.append(f"- {s.concept_label}（{s.concept}）：{s.description}")

        if chains:
            parts.append("\n## 预定义分析链 (可直接使用)")
            for c in chains:
                parts.append(f"- {c.display_name}：{' → '.join(c.path)}")

        parts.append("\n## 规则")
        parts.append("- 一次只查询一个概念")
        parts.append("- 根据数据结果决定下一步, 最多 4 步")
        parts.append("- 无数据时如实告知, 不编造")
        parts.append("- 最终输出含 P0/P1/P2 行动项")

        return "\n".join(parts)

    # ── LLM 上下文生成 ────────────────────────────────────────

    def _build_skill_catalog(self, skills: list[AtomicSkill]) -> str:
        """生成 Skill 目录文本, 注入给 LLM 用于动态编排。"""
        lines = ["## 可查询的概念\n"]
        for s in skills:
            relations = self._get_concept_relations(s.concept)
            rel_text = ""
            if relations:
                rel_text = " → 关联: " + ", ".join(relations)
            lines.append(f"- {s.display_name}（{s.concept}）{rel_text}")
        return "\n".join(lines)

    def _get_concept_relations(self, concept_name: str) -> list[str]:
        """获取概念的关系列表 (用于目录展示)。"""
        concept = self._concept_map.get(concept_name, {})
        return [
            r.get("target", "")
            for r in concept.get("relations", [])
            if r.get("target")
        ]

    def _build_relation_graph(self) -> str:
        """生成关系图文本, 注入给 LLM。"""
        lines = ["## 概念关系图\n"]
        for concept in self._concepts:
            label = concept.get("label", "")
            name = concept["name"]
            if not label or label == name:
                continue
            for rel in concept.get("relations", []):
                target = rel.get("target", "")
                target_label = self._concept_map.get(target, {}).get("label", target)
                if target_label and target_label != target:
                    lines.append(f"- {label}({name}) --[{rel.get('label','')}]--> {target_label}({target})")
        return "\n".join(lines)


# 全局单例
compiler = OntologyCompiler()
