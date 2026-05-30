"""Ontology Service — loads ontology metadata from Neo4j, provides context injection.

Single source of truth: Neo4j graph database (pushed from OntoStudio).
No JSON/YAML fallback — if Neo4j is unavailable, Agent cannot function anyway.
"""

import json
from datetime import datetime, timezone
from typing import Optional

from app.agents.settings.concept_domains import CONCEPT_AGENT_MAP
from app.core.config import settings
from app.core.logger import log


def _parse_json_list(raw) -> list:
    """Parse a JSON string or list into a Python list."""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, list) else []
        except (json.JSONDecodeError, TypeError):
            return []
    return []


class OntologyService:
    """Loads and caches ontology metadata from Neo4j for agent context enrichment."""

    def __init__(self):
        self._data: Optional[dict] = None
        self._source: str = "none"
        self._loaded_at: Optional[datetime] = None

    # ── public API ──

    @property
    def loaded(self) -> bool:
        return self._data is not None

    @property
    def source(self) -> str:
        return self._source

    @property
    def meta(self) -> dict:
        return (self._data or {}).get("meta", {})

    def get_prompt(self) -> str:
        """Return the full ontology system prompt (all concepts)."""
        if not self._data:
            return ""
        return self._data.get("prompt", "")

    def get_prompt_for_agent(self, agent_name: str) -> str:
        """Return ontology prompt filtered by concept-to-agent mapping.

        Uses CONCEPT_AGENT_MAP (concept_domains.py) to determine which
        concepts belong to this agent.
        Falls back to full prompt if no concepts match.
        """
        if not self._data:
            return ""

        concepts = self._data.get("concepts", [])
        matched = []
        for c in concepts:
            if agent_name in CONCEPT_AGENT_MAP.get(c["name"], set()):
                matched.append(c["name"])

        if matched:
            return self.get_prompt_for(matched)

        return self.get_prompt()

    def get_prompt_for(self, concept_names: list[str]) -> str:
        """Return a filtered prompt containing only the specified concepts and their relations.

        Also includes target concepts that are referenced by the selected concepts' relations,
        so the agent sees both sides of each relationship.
        """
        if not self._data:
            return ""

        all_concepts = self._data.get("concepts", [])
        concept_by_name = {c["name"]: c for c in all_concepts}

        selected: dict[str, dict] = {}
        for name in concept_names:
            c = concept_by_name.get(name)
            if c:
                selected[name] = c

        for c in list(selected.values()):
            for r in c.get("relations", []):
                target_name = r.get("target", "")
                if target_name not in selected and target_name in concept_by_name:
                    selected[target_name] = concept_by_name[target_name]

        if not selected:
            return ""

        meta = self.meta
        display_name = meta.get("description", meta.get("projectName", ""))
        if " — " in display_name:
            display_name = display_name.split(" — ")[0]

        lines = [f"你是一个{display_name}领域的查询助手。", "", "## 领域概念", ""]

        for c in selected.values():
            lines.append(f"### {c.get('label', '')} ({c.get('name', '')})")
            if c.get("description"):
                lines.append(f"  {c['description']}")
            for p in c.get("properties", []):
                pk = " [主键]" if p.get("isPrimary") else ""
                pt = p.get("type", "string")
                lines.append(f"  · {p.get('label', p.get('name', ''))}({pt}){pk}")
            for a in c.get("actions", []):
                params_desc = ", ".join(
                    f"{p.get('name', '')}:{p.get('type', 'string')}" + ("*" if p.get("required") else "")
                    for p in a.get("inputParams", [])
                )
                prefix = f"({params_desc}) → " if params_desc else ""
                ret = a.get("outputType", "") or "void"
                lines.append(f"  ◇ {a.get('label', a.get('name', ''))}{prefix}{ret}")

        lines.append("")
        lines.append("## 关系路径")
        lines.append("")
        for c in selected.values():
            for r in c.get("relations", []):
                target_name = r.get("target", "")
                target_c = concept_by_name.get(target_name, {})
                target_label = target_c.get("label", target_name) if target_c else target_name
                lines.append(f"- {c.get('label', '')} --[{r.get('label', '')}]--> {target_label}")

        return "\n".join(lines)

    def get_tools(self) -> list[dict]:
        """Return OpenAI-format tool definitions from ontology actions."""
        if not self._data:
            return []
        return self._data.get("tools", [])

    def get_tools_for_agent(self, agent_name: str) -> list[dict]:
        """Return tools filtered by concept-to-agent mapping (CONCEPT_AGENT_MAP).

        Action tools named ConceptName_actionName are included only when
        the concept is mapped to this agent.
        General tools (搜索节点, 统计概览) and trace tools are always included.
        """
        all_tools = self._data.get("tools", []) if self._data else []
        if not all_tools:
            return []

        agent_concepts: set[str] = set()
        for c in self.get_concepts():
            if agent_name in CONCEPT_AGENT_MAP.get(c["name"], set()):
                agent_concepts.add(c["name"])

        if not agent_concepts:
            return all_tools

        matched: list[dict] = []
        for tool in all_tools:
            func_name = tool.get("function", {}).get("name", "")
            if not any(func_name.startswith(cn + "_") for cn in self._all_concept_names()):
                matched.append(tool)
                continue
            for cn in agent_concepts:
                if func_name.startswith(cn + "_"):
                    matched.append(tool)
                    break

        return matched

    def _all_concept_names(self) -> list[str]:
        concepts = self._data.get("concepts", []) if self._data else []
        return [c.get("name", "") for c in concepts]

    def get_concepts(self) -> list[dict]:
        if not self._data:
            return []
        return self._data.get("concepts", [])

    def get_action_signatures(self) -> list[dict]:
        if not self._data:
            return []
        return self._data.get("actionSignatures", [])

    def get_concept(self, name: str) -> Optional[dict]:
        for c in self.get_concepts():
            if c.get("name") == name:
                return c
        return None

    def get_rules_for_agent(self, agent_name: str) -> list[dict]:
        """Return rules filtered by concept-to-agent mapping (CONCEPT_AGENT_MAP)."""
        concepts = self._data.get("concepts", []) if self._data else []
        matched = []
        for c in concepts:
            if agent_name not in CONCEPT_AGENT_MAP.get(c["name"], set()):
                continue
            for r in c.get("rules", []):
                if not r:
                    continue
                matched.append({
                    "concept": c.get("label", c.get("name", "")),
                    "conceptName": c.get("name", ""),
                    "name": r.get("name", ""),
                    "label": r.get("label", ""),
                    "description": r.get("description", ""),
                    "ruleType": r.get("ruleType", "constraint"),
                    "expression": r.get("expression", ""),
                })
        return matched

    def get_mappings(self) -> list[dict]:
        if not self._data:
            return []
        return self._data.get("mappings", [])

    def status(self) -> dict:
        """Return current ontology status for the management API."""
        if not self._data:
            return {"loaded": False, "source": "none"}
        meta = self.meta
        return {
            "loaded": True,
            "source": self._source,
            "projectName": meta.get("projectName", ""),
            "description": meta.get("description", ""),
            "exportedAt": meta.get("exportedAt", ""),
            "loadedAt": self._loaded_at.isoformat() if self._loaded_at else "",
            "conceptCount": meta.get("conceptCount", 0),
            "actionCount": meta.get("actionCount", 0),
            "systemCount": meta.get("systemCount", 0),
        }

    # ── loading ──

    async def load(self) -> bool:
        """Load ontology from Neo4j. Returns True if loaded."""
        self._data = None
        self._source = "none"

        if not settings.NEO4J_ENABLED:
            log.warning("ontology: Neo4j disabled, cannot load")
            return False

        try:
            if await self._load_from_neo4j():
                return True
        except Exception as e:
            log.warning(f"Neo4j ontology load failed: {e}")

        log.info("ontology: Neo4j unavailable, agent will run without ontology context")
        return False

    async def reload(self) -> bool:
        """Reload ontology from Neo4j."""
        return await self.load()

    # ── internals ──

    async def _load_from_neo4j(self) -> bool:
        """Load ontology metadata from Neo4j. Queries Concept/Action/Property/Relation
        nodes and assembles them into the bundle format expected by all consumers.

        Requires Neo4j to have Ontology-Graph schema pushed (push_schema).
        """
        from app.services.neo4j_service import neo4j_service

        if not neo4j_service.connected:
            return False

        # 1) Concepts
        records = await neo4j_service.execute_read(
            "MATCH (c:Concept) RETURN c ORDER BY c.name"
        )
        if not records:
            log.warning("[Ontology] Neo4j has no Concept nodes — push_schema first")
            return False

        concept_map: dict[str, dict] = {}
        for r in records:
            c = r["c"]
            concept_map[c["name"]] = {
                "name": c["name"],
                "label": c.get("label", c["name"]),
                "description": c.get("description", ""),
                "parents": c.get("parents", []),
                "authorized_roles": _parse_json_list(c.get("authorized_roles", "[]")),
                "properties": [],
                "relations": [],
                "actions": [],
                "rules": [],
                "dataFilters": [],
            }

        # 2) Properties: MATCH (c:Concept)-[:HAS_PROPERTY]->(p:Property)
        prop_records = await neo4j_service.execute_read(
            "MATCH (c:Concept)-[:HAS_PROPERTY]->(p:Property) RETURN c.name AS cn, p"
        )
        for r in prop_records:
            cn = r.get("cn", "")
            if cn in concept_map:
                p = r["p"]
                ev = p.get("enumValues")
                if isinstance(ev, str):
                    try:
                        ev = json.loads(ev)
                    except (json.JSONDecodeError, TypeError):
                        ev = [ev]
                concept_map[cn]["properties"].append({
                    "name": p["name"],
                    "label": p.get("label", p["name"]),
                    "type": p.get("type", "string"),
                    "isPrimary": p.get("isPrimary", False),
                    "required": p.get("required", False),
                    "enumValues": ev if isinstance(ev, list) else None,
                })

        # 3) Relations: MATCH (c:Concept)-[:HAS_RELATION]->(r:Relation)
        rel_records = await neo4j_service.execute_read(
            "MATCH (c:Concept)-[:HAS_RELATION]->(r:Relation) RETURN c.name AS cn, r"
        )
        for r in rel_records:
            cn = r.get("cn", "")
            if cn in concept_map:
                rel = r["r"]
                concept_map[cn]["relations"].append({
                    "target": rel["target"],
                    "type": rel.get("type", "ManyToOne"),
                    "label": rel.get("label", ""),
                    "reverseLabel": rel.get("reverseLabel", ""),
                })

        # 4) Actions: MATCH (c:Concept)-[:HAS_ACTION]->(a:Action)
        action_records = await neo4j_service.execute_read(
            "MATCH (c:Concept)-[:HAS_ACTION]->(a:Action) RETURN c.name AS cn, a"
        )
        action_signatures = []
        tools = []
        for r in action_records:
            cn = r.get("cn", "")
            if cn not in concept_map:
                continue
            a = r["a"]
            params_raw = a.get("params", "[]")
            if isinstance(params_raw, str):
                try:
                    params = json.loads(params_raw)
                except (json.JSONDecodeError, TypeError):
                    params = []
            else:
                params = params_raw or []

            fn_name = a.get("functionName", f"{cn}_{a.get('name', '')}")
            concept_map[cn]["actions"].append({
                "name": a.get("name", ""),
                "label": a.get("label", ""),
                "description": a.get("description", ""),
                "inputParams": params,
                "outputType": a.get("outputType", ""),
                "requiresConfirmation": a.get("requiresConfirmation", False),
                "authorized_roles": _parse_json_list(a.get("authorized_roles", "[]")),
            })

            # Build actionSignatures entry
            action_signatures.append({
                "functionName": fn_name,
                "conceptName": cn,
                "conceptLabel": concept_map[cn]["label"],
                "actionName": a.get("name", ""),
                "actionLabel": a.get("label", ""),
                "description": a.get("description", ""),
                "outputType": a.get("outputType", ""),
                "requiresConfirmation": a.get("requiresConfirmation", False),
                "authorized_roles": _parse_json_list(a.get("authorized_roles", "[]")),
                "params": [
                    {
                        "name": p.get("name", ""),
                        "label": p.get("label", ""),
                        "type": p.get("paramType", p.get("type", "string")),
                        "required": p.get("required", False),
                        "conceptPropertyRef": p.get("conceptPropertyRef", ""),
                    }
                    for p in params
                ],
            })

            # Build tools entry
            props = {}
            required_list = []
            for p in params:
                json_type = {
                    "int": "integer", "float": "number", "bool": "boolean",
                }.get(p.get("paramType", p.get("type", "string")), "string")
                props[p["name"]] = {
                    "type": json_type,
                    "description": p.get("label", p.get("name", "")),
                }
                if p.get("required"):
                    required_list.append(p["name"])
            tool = {
                "type": "function",
                "function": {
                    "name": fn_name,
                    "description": a.get("description", ""),
                    "parameters": {"type": "object", "properties": props},
                },
            }
            if required_list:
                tool["function"]["parameters"]["required"] = required_list
            tools.append(tool)

        # 5) Rules: MATCH (c:Concept)-[:HAS_RULE]->(r:Rule)
        try:
            rule_records = await neo4j_service.execute_read(
                "MATCH (c:Concept)-[:HAS_RULE]->(r:Rule) RETURN c.name AS cn, r"
            )
            for r in rule_records:
                cn = r.get("cn", "")
                if cn in concept_map:
                    rule = r["r"]
                    concept_map[cn]["rules"].append({
                        "name": rule.get("name", ""),
                        "label": rule.get("label", ""),
                        "description": rule.get("description", ""),
                        "ruleType": rule.get("ruleType", "constraint"),
                        "expression": rule.get("expression", ""),
                        "authorized_roles": _parse_json_list(rule.get("authorized_roles", "[]")),
                        "nextRules": _parse_json_list(rule.get("nextRules", "[]")),
                    })
        except Exception as e:
            log.warning(f"[OntologyService] failed to load rules from Neo4j: {e}")

        # 6) DataFilters: MATCH (c:Concept)-[:HAS_DATAFILTER]->(f:DataFilter)
        try:
            df_records = await neo4j_service.execute_read(
                "MATCH (c:Concept)-[:HAS_DATAFILTER]->(f:DataFilter) RETURN c.name AS cn, f"
            )
            for r in df_records:
                cn = r.get("cn", "")
                if cn in concept_map:
                    f = r["f"]
                    concept_map[cn]["dataFilters"].append({
                        "property": f.get("property", ""),
                        "matchProperty": f.get("matchProperty", ""),
                        "roles": _parse_json_list(f.get("roles", "[]")),
                    })
        except Exception as e:
            log.warning(f"[OntologyService] failed to load dataFilters from Neo4j: {e}")

        # 7) Build prompt from concepts
        prompt = self._build_prompt_from_concepts(list(concept_map.values()))

        # 8) Mappings from Neo4j
        mappings = await self._load_mappings_from_neo4j()

        self._data = {
            "meta": {
                "projectName": "manufacturing",
                "description": "制造业本体模型",
                "exportedAt": datetime.now(timezone.utc).isoformat(),
                "version": "1.0",
                "conceptCount": len(concept_map),
                "actionCount": len(action_signatures),
                "systemCount": 4,
            },
            "prompt": prompt,
            "tools": tools,
            "concepts": list(concept_map.values()),
            "mappings": mappings,
            "actionSignatures": action_signatures,
        }
        self._source = f"neo4j://{settings.NEO4J_URI}"
        self._loaded_at = datetime.now(timezone.utc)
        log.info(
            f"ontology loaded from Neo4j: {len(concept_map)} concepts, "
            f"{len(action_signatures)} actions"
        )
        return True

    async def _load_mappings_from_neo4j(self) -> list[dict]:
        from app.services.neo4j_service import neo4j_service
        records = await neo4j_service.execute_read("MATCH (m:Mapping) RETURN m")
        if records:
            return [dict(r["m"]) for r in records]
        return []

    @staticmethod
    def _build_prompt_from_concepts(concepts: list[dict]) -> str:
        """Build a system prompt string from concept definitions."""
        lines = ["你是一个制造业领域的查询助手。", "", "## 领域概念", ""]
        for c in concepts:
            lines.append(f"### {c.get('label', '')} ({c.get('name', '')})")
            if c.get("description"):
                lines.append(f"  {c['description']}")
            for p in c.get("properties", []):
                pk = " [主键]" if p.get("isPrimary") else ""
                lines.append(f"  · {p.get('label', p.get('name', ''))}({p.get('type', 'string')}){pk}")
            for r in c.get("relations", []):
                lines.append(f"  → [{r.get('label', '')}] {r.get('target', '')}")
        return "\n".join(lines)


ontology_service = OntologyService()
