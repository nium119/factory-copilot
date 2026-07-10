"""本体编译器核心 — 读 Neo4j 本体元数据 → 生成 Skill + Agent + 链。"""

import asyncio
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
        agents = await self._assemble_agents(skills, chains)

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
        """从 Neo4j 加载概念、属性、关系、动作、规则。按 active namespace 过滤。"""
        from app.services.ontology_service import ontology_service

        if not ontology_service._data:
            await ontology_service.reload()

        all_concepts = ontology_service.get_concepts() or []
        # 保存完整概念列表供领域推导使用 (不受 namespace 过滤影响)
        self._all_concepts = list(all_concepts)

        # namespace 过滤: 从 Neo4j 查询该 namespace 下有业务数据的标签
        ns = self._get_active_ns()
        if ns:
            active_labels = await self._get_namespace_labels(ns)
            if active_labels:
                all_concepts = [c for c in all_concepts if c["name"] in active_labels]

        self._concepts = all_concepts
        self._concept_map = {c["name"]: c for c in all_concepts}

        # 构建父子关系索引 (用完整概念树)
        self._parent_children = {}
        for c in self._all_concepts:
            for p in c.get("parents", []):
                self._parent_children.setdefault(p, []).append(c["name"])

    @staticmethod
    async def _get_namespace_labels(ns: str) -> set:
        """查询 Neo4j 中该 namespace 下所有业务数据的节点标签。"""
        try:
            from app.services.neo4j_service import neo4j_service
            if not neo4j_service.connected:
                await neo4j_service.connect()
            if neo4j_service.connected:
                records = await neo4j_service.execute_read(
                    "MATCH (n) WHERE n._namespace = $ns "
                    "AND NOT n:Concept AND NOT n:Property AND NOT n:Action "
                    "AND NOT n:Rule AND NOT n:Relation AND NOT n:DataFilter "
                    "AND NOT n:Mapping AND NOT n:Project AND NOT n:SchemaVersion "
                    "RETURN DISTINCT labels(n) AS labels",
                    {"ns": ns}
                )
                labels = set()
                for r in (records or []):
                    labels.update(r["labels"])
                return labels
        except Exception:
            pass
        return set()

    @staticmethod
    def _get_active_ns() -> str:
        try:
            import os
            path = os.path.join(os.path.dirname(__file__), "..", "..", "..", "config", "active_namespace.txt")
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    return f.read().strip()
        except Exception:
            pass
        return ""

    # ── 原子 Skill 生成 ───────────────────────────────────────

    def _generate_atomic_skills(self) -> list[AtomicSkill]:
        """每个概念生成一个 query Skill + 关联手动定义的 actions。"""
        # 先建全局触发词频次表，用于去重过滤
        trigger_counts = self._build_trigger_counts()
        skills = []
        for concept in self._concepts:
            name = concept.get("name", "")
            if not name:
                continue
            label = concept.get("label", name)
            desc = concept.get("description", "")

            # 只处理有 label 的业务概念, 跳过纯枚举/字典概念
            if not label or label == name:
                continue

            # 跳过纯语义概念: 父节点 (有子概念无主键) 或 字典概念 (父链含 Dictionary)
            has_mapping = any(
                m for p in concept.get("properties", [])
                for m in p.get("mappings", [])
            )
            has_children = name in self._parent_children
            has_primary = any(p.get("isPrimary") for p in concept.get("properties", []))
            is_dictionary = self._is_dictionary_concept(name)
            if not has_mapping and (has_children or is_dictionary or not has_primary):
                continue

            # 生成 query Skill
            skill = AtomicSkill(
                name=f"{name}_query",
                display_name=f"{label}查询",
                concept=name,
                concept_label=label,
                description=desc or f"查询{label}数据",
                triggers=self._extract_triggers(concept, trigger_counts),
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

    def _extract_triggers(self, concept: dict, global_counts: dict[str, int] = None) -> list[str]:
        """触发词仅保留概念中文名，属性名不再自动加入。
        属性名跨概念重复率高、区分度差，按需在 SkillsTab 手动添加。"""
        label = concept.get("label", "")
        return [label] if label else []

    def _build_trigger_counts(self) -> dict[str, int]:
        """统计所有候选触发词在各概念中的出现次数。"""
        counts: dict[str, int] = {}
        for concept in self._concepts:
            seen = set()
            label = concept.get("label", "")
            if label:
                seen.add(label)
            for prop in concept.get("properties", []):
                pl = prop.get("label", "")
                if pl and len(pl) >= 2:
                    seen.add(pl)
            for t in seen:
                counts[t] = counts.get(t, 0) + 1
        return counts

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
        """根据概念特征 + 系统配置决定数据源。API 配置优先于关系判断。"""
        name = concept["name"]

        # 1. 显式 API 配置 → API 优先
        api_system = self._find_api_system(name)
        if api_system:
            return DataSource(
                type=DataSourceType.API,
                system=api_system,
                freshness="realtime",
                reason="API 接口配置",
            )

        # 2. 有跨概念关系或计算规则 → Neo4j
        has_relations = bool(concept.get("relations", []))
        has_rules = bool(concept.get("rules", []))
        if has_relations or has_rules:
            return DataSource(
                type=DataSourceType.NEO4J,
                freshness="cached",
                reason="有跨概念关系或计算规则",
            )

        # 3. 默认 → Neo4j
        return DataSource(type=DataSourceType.NEO4J, freshness="cached")

    def _find_api_system(self, concept_name: str) -> str:
        """从 DB 读取当前 namespace 的系统配置，查找概念对应的 API 系统名。
        仅当配置 _applied=true 时生效。"""
        import asyncio
        async def _find():
            from app.db import get_db
            async for session in get_db():
                from app.repositories.namespace_config_repo import NamespaceConfigRepository
                repo = NamespaceConfigRepository(session)
                ns = self._get_active_ns() or "manufacturing"
                config = await repo.get(ns, "systems")
                # 未应用 → 跳过，所有概念走 Neo4j
                if not config.get("_applied", True):
                    return ""
                for sys_name, sys_cfg in config.get("systems", {}).items():
                    for ep in (sys_cfg.get("endpoints") or []):
                        if ep.get("concept") == concept_name and ep.get("enabled", True):
                            return sys_name
                    if concept_name in (sys_cfg.get("concepts") or []):
                        return sys_name
            return ""
        try:
            return asyncio.run(_find())
        except RuntimeError:
            return ""

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

    def _is_dictionary_concept(self, name: str) -> bool:
        """检查概念是否属于 Dictionary 子树 (字典/枚举概念)。"""
        parent = name
        visited = set()
        while parent and parent not in visited:
            visited.add(parent)
            if parent == "Dictionary":
                return True
            c = self._concept_map.get(parent, {})
            parents = c.get("parents", [])
            if not parents:
                break
            parent = parents[0]  # 沿父链上行
        return False

    # ── 复合 Skill 发现 ──────────────────────────────────────

    def _discover_composite_skills(
        self, skills: list[AtomicSkill]
    ) -> list[CompositeSkill]:
        """复合 Skill 发现已关闭 — 链条由用户在业务域卡片中手动创建。
        关系图 BFS 无法判断业务分析目标，机械生成的路径没有实际价值。"""
        return []

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

        chain_name = f"{path_labels[0]}诊断链"
        # 触发词必须包含路径中至少2个概念标签，避免劫持单概念查询
        triggers = []
        if len(path_labels) >= 2:
            triggers.append(f"{path_labels[0]}.*{path_labels[1]}")
        if len(path_labels) >= 3:
            triggers.append(f"{path_labels[0]}.*{path_labels[1]}.*{path_labels[2]}")
        if not triggers:
            triggers.append(f"分析.*{path_labels[0]}" if path_labels else "分析")
        return CompositeSkill(
            name=f"chain_{path[0].lower()}",
            display_name=chain_name,
            description=f"自动发现的{'→'.join(path_labels)}分析路径",
            path=path,
            steps=steps,
            triggers=triggers,
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

    async def _assemble_agents(
        self, skills: list[AtomicSkill], chains: list[CompositeSkill]
    ) -> list[AgentDefinition]:
        """从领域配置 + 概念树组装 Agent 定义。"""
        domains = await self._load_domain_config()

        agents = []
        for agent_name, config in domains.items():
            if agent_name == "mode" or not isinstance(config, dict):
                continue
            domain_concepts = set(config.get("concepts", []))

            # 该 Agent 持有的 Skill
            agent_skills = [
                s for s in skills
                if s.concept in domain_concepts
            ]
            # 链归起点概念所在的域 (跨域链也能被看到)
            agent_chains = [
                c for c in chains
                if c.path and c.path[0] in domain_concepts
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

    async def _load_domain_config(self) -> dict:
        """从 DB 加载当前 namespace 的业务域配置。"""
        from app.db import get_db
        async for session in get_db():
            from app.repositories.namespace_config_repo import NamespaceConfigRepository
            repo = NamespaceConfigRepository(session)
            ns = self._get_active_ns() or "manufacturing"
            config = await repo.get(ns, "domains")
            # 只有 mode 不算有效配置，需触发推导
            if config and any(k != "mode" for k in config):
                return config

        # 读取推导模式
        derivation_mode = self._get_derivation_mode()
        if derivation_mode == "llm":
            logger.warning("[Compiler] LLM 推导模式")
            result = await self._llm_derive_domains()
            if result:
                return result
            raise RuntimeError("LLM推导失败，请检查LLM服务配置")
        elif derivation_mode == "rule":
            logger.warning("[Compiler] 规则推导模式")
            result = self._derive_domains_from_ontology()
            logger.warning(f"[Compiler] 规则推导完成: {len(result)} 个域")
            return result

        # 无配置: 返回空, 需手动推导
        logger.warning("[Compiler] 无域配置, Agent数为0")
        return {}

    def _get_derivation_mode(self) -> str:
        """从 DB 读取推导模式。"""
        import asyncio
        async def _get():
            from app.db import get_db
            async for session in get_db():
                from app.repositories.namespace_config_repo import NamespaceConfigRepository
                repo = NamespaceConfigRepository(session)
                ns = self._get_active_ns() or "manufacturing"
                config = await repo.get(ns, "domains")
                return config.get("mode", "")
            return ""
        try:
            return asyncio.run(_get())
        except RuntimeError:
            return ""

    async def _llm_derive_domains(self) -> dict:
        """用 LLM 从概念列表推导领域分组，结果持久化到 DB。"""
        import json
        concepts_info = []
        for c in self._concepts:
            label = c.get("label", "")
            if not label or label == c["name"]:
                continue
            concepts_info.append({
                "name": c["name"],
                "label": label,
                "description": c.get("description", ""),
                "parents": c.get("parents", []),
            })
        if len(concepts_info) < 3:
            return {}

        prompt = f"""以下是本体中的所有业务概念，请将它们分组为 3-6 个"业务域"。
每个概念只能属于一个域。域的名称应该是中文，简洁易懂。
排除纯字典/枚举概念。

## 概念列表
{json.dumps(concepts_info, ensure_ascii=False, indent=2)}

## 输出格式 (严格JSON)
{{
  "domain_key": {{
    "display_name": "域中文名",
    "description": "域描述",
    "icon": "emoji",
    "concepts": ["ConceptA", "ConceptB"]
  }}
}}

只输出JSON，不要解释。"""

        try:
            from app.services.llm_service import llm_service
            response = ""
            async for chunk_type, chunk_content in llm_service.chat_stream(
                message=prompt, session_id="compiler_domains",
                system_prompt="你是领域专家，擅长对业务概念进行语义分类。只输出JSON。",
                model_name=None, enable_thinking=False, tools=None,
            ):
                if chunk_type == 'content':
                    response += chunk_content
            response = response.strip()
            if response.startswith("```"):
                response = response.split("\n", 1)[1].rsplit("\n", 1)[0]
            result = json.loads(response)
            if isinstance(result, dict) and len(result) >= 2:
                logger.info(f"[Compiler] LLM推导成功: {len(result)} 个域")
                # 持久化到 DB (去掉 mode 字段)
                result.pop("mode", None)
                try:
                    from app.db import get_db
                    async for session in get_db():
                        from app.repositories.namespace_config_repo import NamespaceConfigRepository
                        repo = NamespaceConfigRepository(session)
                        ns = self._get_active_ns() or "manufacturing"
                        await repo.save(ns, "domains", result)
                except Exception:
                    pass
                return result
        except Exception as e:
            logger.error(f"[Compiler] LLM推导失败: {e}")
            raise RuntimeError(f"LLM推导失败: {e}") from e

    def _derive_domains_from_ontology(self) -> dict:
        """从完整概念树找顶层父概念 (被引用为父但自己没有父或父不在概念列表中)。"""
        domains = {}
        all_concepts = getattr(self, '_all_concepts', self._concepts)
        all_names = {c["name"] for c in all_concepts}

        # 找顶层父概念: 出现在其他概念的 parents 中, 但自己不在任何概念的 parents 中
        referenced_as_parent = set()
        child_parents = {}
        for c in all_concepts:
            for p in c.get("parents", []):
                referenced_as_parent.add(p)
                child_parents.setdefault(p, []).append(c["name"])

        # 根 = 被引用为父、且自己没有父 (或父不在已知概念中)
        root_names = [
            p for p in referenced_as_parent
            if p in all_names  # 概念必须存在
        ]
        # 排除自己也作为子概念出现的
        has_own_parent = set()
        for c in all_concepts:
            if c.get("parents"):
                has_own_parent.add(c["name"])
        root_names = [p for p in root_names if p not in has_own_parent or not any(
            pp in all_names for pp in next((c for c in all_concepts if c["name"]==p), {}).get("parents",[])
        )]

        # 重新简化: 根 = 出现在 parents 引用中且概念存在
        root_concepts = [c for c in all_concepts if c["name"] in root_names]

        if not root_concepts:
            # 回退: 没有显式根, 用无父概念的顶层级
            all_parents = set()
            for c in all_concepts:
                all_parents.update(c.get("parents", []))
            root_concepts = [c for c in all_concepts if c["name"] not in all_parents]

        filtered_names = {c["name"] for c in self._concepts}
        logger.warning(f"[Compiler] 根: {[r['name'] for r in root_concepts]} ({len(root_concepts)}个)")

        for root in root_concepts:
            children = child_parents.get(root["name"], [])
            active_children = [c for c in children if c in filtered_names]
            concepts = ([root["name"]] if root["name"] in filtered_names else []) + active_children
            if not concepts:
                continue
            name = f"agent_{root['name'].lower()}"
            domains[name] = {
                "display_name": root.get("label", root["name"]),
                "description": root.get("description", ""),
                "icon": "🤖",
                "color": "#6c5ce7",
                "concepts": concepts,
            }

        # 持久化到 DB
        try:
            import asyncio
            async def _save():
                from app.db import get_db
                async for session in get_db():
                    from app.repositories.namespace_config_repo import NamespaceConfigRepository
                    repo = NamespaceConfigRepository(session)
                    ns = self._get_active_ns() or "manufacturing"
                    await repo.save(ns, "domains", domains)
                    logger.info(f"[Compiler] 规则推导已保存: {len(domains)} 个域")
            asyncio.run(_save())
        except Exception as e:
            logger.warning(f"[Compiler] 规则推导保存失败: {e}")

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
