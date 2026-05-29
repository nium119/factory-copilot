"""Ontology Service — loads ontology bundle from OntoStudio, provides context injection.

Load order: local data/ontology/ dir → remote OntoStudio API → empty fallback.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx

from app.agents.settings.concept_domains import CONCEPT_AGENT_MAP
from app.core.config import settings
from app.core.logger import log


class OntologyService:
    """Loads and caches the ontology agent-bundle for agent context enrichment."""

    def __init__(self):
        self._data: Optional[dict] = None
        self._source: str = "none"
        self._mtime: float = 0
        self._loaded_at: Optional[datetime] = None
        self._local_path: str = ""
        self._remote_url: str = ""

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

    def _auto_reload_if_changed(self):
        """Check if local file mtime changed and reload silently."""
        if not self._local_path or not os.path.isfile(self._local_path):
            return
        try:
            current_mtime = os.path.getmtime(self._local_path)
            if current_mtime != self._mtime:
                log.info(f"ontology file changed, auto-reloading: {self._local_path}")
                import asyncio
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    return
                loop.create_task(self._load_local(self._local_path))
        except Exception:
            pass

    def get_prompt(self) -> str:
        """Return the full ontology system prompt (all concepts)."""
        self._auto_reload_if_changed()
        if not self._data:
            return ""
        return self._data.get("prompt", "")

    def get_prompt_for_agent(self, agent_name: str) -> str:
        """Return ontology prompt filtered by concept-to-agent mapping.

        Uses CONCEPT_AGENT_MAP (concept_domains.py) to determine which
        concepts belong to this agent.
        Falls back to full prompt if no concepts match.
        """
        self._auto_reload_if_changed()
        if not self._data:
            return ""

        concepts = self._data.get("concepts", [])
        matched = []
        for c in concepts:
            if agent_name in CONCEPT_AGENT_MAP.get(c["name"], set()):
                matched.append(c["name"])

        if matched:
            return self.get_prompt_for(matched)

        # Fallback: no domain match → return full prompt (for general agent, etc.)
        return self.get_prompt()

    def get_prompt_for(self, concept_names: list[str]) -> str:
        """Return a filtered prompt containing only the specified concepts and their relations.

        Also includes target concepts that are referenced by the selected concepts' relations,
        so the agent sees both sides of each relationship.
        """
        self._auto_reload_if_changed()
        if not self._data:
            return ""

        all_concepts = self._data.get("concepts", [])
        concept_by_name = {c["name"]: c for c in all_concepts}

        # Collect selected concepts + targets of their relations
        selected: dict[str, dict] = {}
        for name in concept_names:
            c = concept_by_name.get(name)
            if c:
                selected[name] = c

        # Add target concepts referenced by relations (so the agent sees both sides)
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
        self._auto_reload_if_changed()
        if not self._data:
            return []
        return self._data.get("tools", [])

    def get_tools_for_agent(self, agent_name: str) -> list[dict]:
        """Return tools filtered by concept-to-agent mapping (CONCEPT_AGENT_MAP).

        Action tools named ConceptName_actionName are included only when
        the concept is mapped to this agent.
        General tools (搜索节点, 统计概览) and trace tools are always included.
        """
        self._auto_reload_if_changed()
        all_tools = self._data.get("tools", []) if self._data else []
        if not all_tools:
            return []

        # Build set of concept names matching this agent's domain
        agent_concepts: set[str] = set()
        for c in self.get_concepts():
            if agent_name in CONCEPT_AGENT_MAP.get(c["name"], set()):
                agent_concepts.add(c["name"])

        if not agent_concepts:
            return all_tools

        matched: list[dict] = []
        for tool in all_tools:
            func_name = tool.get("function", {}).get("name", "")
            # General tools — always include
            if not any(func_name.startswith(cn + "_") for cn in self._all_concept_names()):
                matched.append(tool)
                continue
            # Action tools — include only if concept matches agent domain
            for cn in agent_concepts:
                if func_name.startswith(cn + "_"):
                    matched.append(tool)
                    break

        return matched

    def _all_concept_names(self) -> list[str]:
        concepts = self._data.get("concepts", []) if self._data else []
        return [c.get("name", "") for c in concepts]

    def get_concepts(self) -> list[dict]:
        self._auto_reload_if_changed()
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
        self._auto_reload_if_changed()
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
            "localPath": self._local_path,
            "remoteUrl": self._remote_url,
        }

    # ── loading ──

    async def load(self, *, local_path: str = "", remote_url: str = "") -> bool:
        """Load ontology from local file or remote URL. Returns True if loaded."""
        self._local_path = local_path
        self._remote_url = remote_url

        # 0) Neo4j (primary if enabled)
        if settings.NEO4J_ENABLED and settings.NEO4J_ONTOLOGY_AS_PRIMARY:
            try:
                if await self._load_from_neo4j():
                    return True
            except Exception as e:
                log.warning(f"Neo4j ontology load failed, falling back: {e}")

        # 1) local file
        if local_path:
            if await self._load_local(local_path):
                return True

        # 2) auto-detect local files in data/ontology/
        if not local_path:
            auto_path = self._find_local_bundle()
            if auto_path and await self._load_local(auto_path):
                return True

        # 3) remote OntoStudio API
        if remote_url:
            try:
                if await self._load_remote(remote_url):
                    return True
            except Exception as e:
                log.warning(f"remote ontology load failed: {e}")

        # 4) env var fallback
        remote_env = os.getenv("ONTOLOGY_API_URL", "")
        if remote_env and remote_env != remote_url:
            try:
                if await self._load_remote(remote_env):
                    return True
            except Exception as e:
                log.warning(f"remote ontology load (env) failed: {e}")

        log.info("ontology: no source available, agent will run without ontology context")
        return False

    async def reload(self) -> bool:
        """Reload from the same source used previously."""
        self._data = None
        self._source = "none"
        return await self.load(local_path=self._local_path, remote_url=self._remote_url)

    # ── internals ──

    @staticmethod
    def _find_local_bundle() -> str:
        """Find the first .json or .onto.yaml in data/ontology/."""
        data_dir = os.path.join(os.path.dirname(__file__), "..", "..", "data", "ontology")
        data_dir = os.path.normpath(data_dir)
        if not os.path.isdir(data_dir):
            return ""
        for name in sorted(os.listdir(data_dir)):
            if name.endswith((".json", ".onto.yaml", ".onto.yml")):
                return os.path.join(data_dir, name)
        return ""

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
                "properties": [],
                "relations": [],
                "actions": [],
                "rules": [],
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
                    })
        except Exception as e:
            log.warning(f"[OntologyService] failed to load rules from Neo4j: {e}")

        # 6) Build prompt from concepts
        prompt = self._build_prompt_from_concepts(list(concept_map.values()))

        # 6) Mappings — try Neo4j first, keep existing if empty
        mappings = await self._load_mappings_from_neo4j()
        if not mappings and self._data:
            mappings = self._data.get("mappings", [])

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

    async def _load_local(self, path: str) -> bool:
        """Load ontology bundle from a local JSON or YAML file."""
        if not os.path.isfile(path):
            log.warning(f"ontology local file not found: {path}")
            return False

        mtime = os.path.getmtime(path)
        if self._data is not None and path == self._local_path and mtime == self._mtime:
            return True  # already loaded, no change

        try:
            raw = Path(path).read_text(encoding="utf-8")
        except Exception as e:
            log.warning(f"ontology read failed ({path}): {e}")
            return False

        if path.endswith((".yaml", ".yml")):
            data = self._parse_yaml_bundle(raw, path)
        else:
            data = json.loads(raw)

        if not data:
            return False

        self._data = data
        self._source = f"file://{path}"
        self._mtime = mtime
        self._loaded_at = datetime.now(timezone.utc)
        log.info(
            f"ontology loaded from {path}: {data['meta'].get('conceptCount', 0)} concepts, "
            f"{data['meta'].get('actionCount', 0)} actions"
        )
        return True

    async def _load_remote(self, url: str) -> bool:
        """Load ontology bundle from a remote OntoStudio API."""
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()

        if not data or "meta" not in data:
            log.warning(f"ontology remote response missing 'meta': {url}")
            return False

        self._data = data
        self._source = f"remote://{url}"
        self._loaded_at = datetime.now(timezone.utc)
        log.info(
            f"ontology loaded from {url}: {data['meta'].get('conceptCount', 0)} concepts, "
            f"{data['meta'].get('actionCount', 0)} actions"
        )
        return True

    def _parse_yaml_bundle(self, raw: str, path: str) -> Optional[dict]:
        """Parse an .onto.yaml into the bundle format expected by OntologyService."""
        try:
            import yaml
        except ImportError:
            log.warning("PyYAML not installed, cannot parse .onto.yaml")
            return None

        try:
            project = yaml.safe_load(raw)
        except Exception as e:
            log.warning(f"YAML parse failed ({path}): {e}")
            return None

        if not isinstance(project, dict) or "concepts" not in project:
            log.warning(f"invalid onto.yaml structure in {path}")
            return None

        # Build a minimal bundle from the raw YAML so OntologyService works
        concepts_raw = project.get("concepts", [])
        concepts_data = []
        for c in concepts_raw:
            concepts_data.append({
                "name": c.get("name", ""),
                "label": c.get("label", ""),
                "description": c.get("description", ""),
                "parents": c.get("parents", []),
                "properties": c.get("properties", []),
                "relations": c.get("relations", []),
                "actions": c.get("actions", []),
                "rules": c.get("rules", []),
            })

        # Build a text prompt from the YAML directly
        prompt_lines = [f"你是一个{project.get('description', project.get('name', ''))}领域的查询助手。", "", "## 领域概念结构", ""]
        for c in concepts_data:
            prompt_lines.append(f"  {c['label']} ({c['name']})")
            if c.get("description"):
                prompt_lines.append(f"    {c['description']}")
            for p in c.get("properties", []):
                pk = " [主键]" if p.get("isPrimary") else ""
                prompt_lines.append(f"    · {p.get('label', p.get('name', ''))}({p.get('type', 'string')}){pk}")
            for r in c.get("relations", []):
                prompt_lines.append(f"    → [{r.get('label', '')}] {r.get('target', '')}")

        return {
            "meta": {
                "projectName": project.get("name", ""),
                "description": project.get("description", ""),
                "exportedAt": "",
                "version": "1.0",
                "conceptCount": len(concepts_data),
                "actionCount": sum(len(c.get("actions", [])) for c in concepts_data),
                "systemCount": len(project.get("systems", [])),
            },
            "prompt": "\n".join(prompt_lines),
            "tools": [],
            "concepts": concepts_data,
            "mappings": [],
            "actionSignatures": [],
        }


ontology_service = OntologyService()
