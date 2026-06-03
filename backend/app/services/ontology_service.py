"""Ontology Service — loads ontology metadata from Neo4j, provides context injection.

Single source of truth: Neo4j graph database (pushed from OntoStudio).
No JSON/YAML fallback — if Neo4j is unavailable, Agent cannot function anyway.
"""

import asyncio
import json
import threading
from datetime import datetime, timezone
from typing import Optional


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
    """Loads and caches ontology metadata from Neo4j for agent context enrichment.

    Auto-refreshes when cached data exceeds TTL (default 5 seconds), so
    OntoStudio pushes to Neo4j are reflected within seconds without
    requiring a manual reload.
    """

    _MAX_METRICS_SAMPLES = 20

    def __init__(self):
        self._data: Optional[dict] = None
        self._source: str = "none"
        self._loaded_at: Optional[datetime] = None
        self._last_full_reload: Optional[datetime] = None
        self._refresh_lock = threading.Lock()
        self._refresh_scheduled = False
        self._fingerprint: str = ""
        self._consecutive_failures: int = 0
        self._last_failure: Optional[str] = None
        # Metrics ring buffers
        self._reload_durations: list[float] = []     # last N full-reload durations (ms)
        self._fingerprint_durations: list[float] = [] # last N fingerprint-check durations (ms)
        self._total_reloads: int = 0
        self._total_checks: int = 0

    @property
    def _ns(self) -> str:
        return settings.NEO4J_NAMESPACE

    def _ns_filter(self, alias: str = "") -> tuple[str, dict]:
        """Return (match_clause, params_dict) for namespace filtering.
        When namespace is empty, returns ('', None) for backward compat.
        """
        ns = self._ns
        if not ns:
            return "", None
        return " {namespace: $ns}", {"ns": ns}

    @property
    def _cache_ttl(self) -> int:
        return settings.ONTOLOGY_CACHE_TTL

    @property
    def _force_reload_interval(self) -> int:
        return settings.ONTOLOGY_FORCE_RELOAD

    # ── freshness ──

    def _ensure_fresh(self):
        """Schedule a background fingerprint check if cache TTL has expired.

        Called at the start of every getter. Non-blocking — the current
        call returns cached data; the next call gets fresh data.

        Uses a lightweight fingerprint query to avoid full reload when
        Neo4j hasn't changed. Only does the expensive full load (7 queries)
        when the fingerprint differs from last load.
        """
        if not self._data or not self._loaded_at:
            return
        # Circuit breaker: stop auto-refreshing after too many consecutive failures
        if self._consecutive_failures >= settings.ONTOLOGY_RELOAD_MAX_FAILURES:
            return
        age = (datetime.now(timezone.utc) - self._loaded_at).total_seconds()
        if age < self._cache_ttl:
            return  # still fresh
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # no event loop, skip auto-refresh
        with self._refresh_lock:
            if self._refresh_scheduled:
                return
            self._refresh_scheduled = True
        loop.create_task(self._auto_refresh())

    async def _auto_refresh(self):
        """Background: fingerprint check → full reload only if data changed,
        with a force-reload fallback and circuit breaker on repeated failures."""
        t0 = datetime.now(timezone.utc)
        try:
            force = (
                self._last_full_reload is None
                or (datetime.now(timezone.utc) - self._last_full_reload).total_seconds()
                >= self._force_reload_interval
            )
            fp_t0 = datetime.now(timezone.utc)
            changed = await self._fingerprint_changed()
            fp_ms = (datetime.now(timezone.utc) - fp_t0).total_seconds() * 1000
            self._fingerprint_durations.append(fp_ms)
            self._total_checks += 1
            if len(self._fingerprint_durations) > self._MAX_METRICS_SAMPLES:
                self._fingerprint_durations = self._fingerprint_durations[-self._MAX_METRICS_SAMPLES:]

            if changed or force:
                reload_t0 = datetime.now(timezone.utc)
                ok = await self.reload()
                reload_ms = (datetime.now(timezone.utc) - reload_t0).total_seconds() * 1000
                self._reload_durations.append(reload_ms)
                self._total_reloads += 1
                if len(self._reload_durations) > self._MAX_METRICS_SAMPLES:
                    self._reload_durations = self._reload_durations[-self._MAX_METRICS_SAMPLES:]

                if ok:
                    self._consecutive_failures = 0
                    self._last_failure = None
                    self._last_full_reload = datetime.now(timezone.utc)
                    from app.services.action_executor import action_executor
                    from app.services.rule_engine import rule_engine
                    from app.services.intent_router import intent_router
                    action_executor.invalidate_cache()
                    rule_engine.invalidate_cache()
                    intent_router.rebuild(self, action_executor)
                    tag = "force-reload" if force and not changed else "refreshed"
                    log.info(
                        f"[Ontology] {tag} ({len(self.get_concepts())} concepts)"
                        f" fp={fp_ms:.0f}ms reload={reload_ms:.0f}ms"
                    )
                else:
                    self._consecutive_failures += 1
                    self._last_failure = f"reload returned False ({datetime.now(timezone.utc).isoformat()})"
                    log.warning(f"[Ontology] reload failed ({self._consecutive_failures}/{settings.ONTOLOGY_RELOAD_MAX_FAILURES})")
            else:
                self._loaded_at = datetime.now(timezone.utc)
        except Exception as e:
            self._consecutive_failures += 1
            self._last_failure = f"{type(e).__name__}: {e}"
            log.warning(f"[Ontology] auto-refresh error ({self._consecutive_failures}/{settings.ONTOLOGY_RELOAD_MAX_FAILURES}): {e}")
        finally:
            with self._refresh_lock:
                self._refresh_scheduled = False

    async def _fingerprint_changed(self) -> bool:
        """Lightweight check: count nodes by type. Returns True if data changed.

        Runs ONE fast Cypher query instead of the full 7-query reload.
        Detects additions/deletions. Modifications (e.g. requiresConfirmation
        change) are handled by OntoStudio's push notification.
        """
        from app.services.neo4j_service import neo4j_service
        if not neo4j_service.connected:
            return True  # can't check, assume changed
        try:
            ns_filter, ns_params = self._ns_filter()
            records = await neo4j_service.execute_read(f"""
                MATCH (c:Concept{ns_filter})
                OPTIONAL MATCH (c)-[:HAS_ACTION]->(a:Action{ns_filter})
                OPTIONAL MATCH (c)-[:HAS_RULE]->(r:Rule{ns_filter})
                OPTIONAL MATCH (c)-[:HAS_PROPERTY]->(p:Property{ns_filter})
                OPTIONAL MATCH (c)-[:HAS_RELATION]->(rel:Relation{ns_filter})
                RETURN count(DISTINCT c) AS concepts,
                       count(DISTINCT a) AS actions,
                       count(DISTINCT r) AS rules,
                       count(DISTINCT p) AS properties,
                       count(DISTINCT rel) AS relations
            """, params=ns_params)
            if not records:
                return True
            rec = records[0]
            new_fp = f"c{rec['concepts']}a{rec['actions']}r{rec['rules']}p{rec['properties']}rel{rec['relations']}"
            if new_fp != self._fingerprint:
                self._fingerprint = new_fp
                return True
            return False
        except Exception:
            return True  # on error, do full reload to be safe

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
        self._ensure_fresh()
        if not self._data:
            return ""
        return self._data.get("prompt", "")

    def get_prompt_for_agent(self, agent_name: str) -> str:
        """Return full ontology prompt — no per-agent filtering."""
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
                pn = p.get("name", "")
                pl = p.get("label", pn)
                lines.append(f"  · {pn}({pt}): {pl}{pk}")
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
        """Return all ontology tools — no per-agent filtering.

        Agent differentiation lives in system prompts, not hardcoded tool whitelists.
        Neo4j is the single source of truth.
        """
        return self._data.get("tools", []) if self._data else []

    def _all_concept_names(self) -> list[str]:
        concepts = self._data.get("concepts", []) if self._data else []
        return [c.get("name", "") for c in concepts]

    def get_concepts(self) -> list[dict]:
        self._ensure_fresh()
        if not self._data:
            return []
        return self._data.get("concepts", [])

    def get_action_signatures(self) -> list[dict]:
        self._ensure_fresh()
        if not self._data:
            return []
        return self._data.get("actionSignatures", [])

    def get_concept(self, name: str) -> Optional[dict]:
        for c in self.get_concepts():
            if c.get("name") == name:
                return c
        return None

    def get_rules_for_agent(self, agent_name: str) -> list[dict]:
        """Return all rules — no per-agent filtering."""
        concepts = self._data.get("concepts", []) if self._data else []
        matched = []
        for c in concepts:
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
        """Return current ontology status for the management API.

        Includes cache freshness metadata so ops can monitor staleness.
        """
        if not self._data:
            return {
                "loaded": False,
                "source": "none",
                "cacheAge": None,
                "lastFullReload": None,
                "consecutiveFailures": self._consecutive_failures,
            }
        meta = self.meta
        now = datetime.now(timezone.utc)
        cache_age = (now - self._loaded_at).total_seconds() if self._loaded_at else None
        last_reload = self._last_full_reload.isoformat() if self._last_full_reload else None
        return {
            "loaded": True,
            "source": self._source,
            "projectName": meta.get("projectName", ""),
            "description": meta.get("description", ""),
            "exportedAt": meta.get("exportedAt", ""),
            "loadedAt": self._loaded_at.isoformat() if self._loaded_at else "",
            "cacheAgeSeconds": round(cache_age, 1) if cache_age else None,
            "lastFullReload": last_reload,
            "fingerprint": self._fingerprint,
            "conceptCount": meta.get("conceptCount", 0),
            "actionCount": meta.get("actionCount", 0),
            "systemCount": meta.get("systemCount", 0),
            "schemaVersion": meta.get("schemaVersion"),
            "consecutiveFailures": self._consecutive_failures,
            "lastFailure": self._last_failure,
        }

    def health(self) -> dict:
        """Return health-check status for load balancers / monitoring.

        Returns a dict with:
          - healthy: bool — overall health
          - suggestedHttpStatus: 200 | 503
          - checks: list of individual check results
          - metrics: aggregate timing stats for capacity planning

        Unhealthy when:
          - Cache is older than ONTOLOGY_MAX_STALENESS (Neo4j unreachable)
          - Circuit breaker tripped (consecutive failures >= max)
        """
        checks: list[dict] = []
        healthy = True

        # Check 1: ontology loaded at all
        if not self._data:
            checks.append({"name": "ontology_loaded", "pass": False, "detail": "No ontology data loaded"})
            return {"healthy": False, "suggestedHttpStatus": 503, "checks": checks, "metrics": self._metrics_summary()}
        checks.append({"name": "ontology_loaded", "pass": True})

        # Check 2: cache staleness
        now = datetime.now(timezone.utc)
        cache_age = (now - self._loaded_at).total_seconds() if self._loaded_at else 999999
        max_stale = getattr(settings, 'ONTOLOGY_MAX_STALENESS', 300)
        if cache_age > max_stale:
            healthy = False
            checks.append({
                "name": "cache_freshness",
                "pass": False,
                "detail": f"Cache is {cache_age:.0f}s old (max {max_stale}s). Neo4j may be unreachable.",
                "cacheAgeSeconds": round(cache_age, 1),
                "thresholdSeconds": max_stale,
            })
        else:
            checks.append({
                "name": "cache_freshness",
                "pass": True,
                "cacheAgeSeconds": round(cache_age, 1),
                "thresholdSeconds": max_stale,
            })

        # Check 3: circuit breaker
        max_fail = settings.ONTOLOGY_RELOAD_MAX_FAILURES
        if self._consecutive_failures >= max_fail:
            healthy = False
            checks.append({
                "name": "neo4j_connectivity",
                "pass": False,
                "detail": f"Circuit breaker open: {self._consecutive_failures} consecutive failures",
                "consecutiveFailures": self._consecutive_failures,
                "maxFailures": max_fail,
                "lastFailure": self._last_failure,
            })
        elif self._consecutive_failures > 0:
            checks.append({
                "name": "neo4j_connectivity",
                "pass": True,
                "detail": f"{self._consecutive_failures} recent failure(s), but below threshold ({max_fail})",
                "consecutiveFailures": self._consecutive_failures,
                "maxFailures": max_fail,
            })
        else:
            checks.append({"name": "neo4j_connectivity", "pass": True})

        return {
            "healthy": healthy,
            "suggestedHttpStatus": 200 if healthy else 503,
            "checks": checks,
            "metrics": self._metrics_summary(),
        }

    def _metrics_summary(self) -> dict:
        """Aggregate timing stats for capacity planning and monitoring."""
        reloads = self._reload_durations
        fps = self._fingerprint_durations

        def avg(vals): return round(sum(vals) / len(vals), 1) if vals else None
        def p95(vals):
            if not vals: return None
            s = sorted(vals)
            return round(s[int(len(s) * 0.95)], 1)

        return {
            "totalReloads": self._total_reloads,
            "totalChecks": self._total_checks,
            "reloadDurationMs": {"avg": avg(reloads), "p95": p95(reloads), "last": reloads[-1] if reloads else None},
            "fingerprintCheckMs": {"avg": avg(fps), "p95": p95(fps), "last": fps[-1] if fps else None},
            "sampleCount": len(reloads),
        }

    # ── loading ──

    async def load(self) -> bool:
        """Load ontology from Neo4j. Returns True if loaded.

        Old data is preserved until new data is ready (atomic swap),
        so concurrent getters never see an empty state.
        """
        if not settings.NEO4J_ENABLED:
            log.warning("ontology: Neo4j disabled, cannot load")
            return False

        prev_data = self._data
        prev_source = self._source
        try:
            if await self._load_from_neo4j():
                self._last_full_reload = datetime.now(timezone.utc)
                self._consecutive_failures = 0
                self._last_failure = None
                return True
        except Exception as e:
            log.warning(f"Neo4j ontology load failed: {e}")
            self._consecutive_failures += 1
            self._last_failure = f"{type(e).__name__}: {e}"

        # Restore previous state on failure (if any)
        if prev_data is not None:
            self._data = prev_data
            self._source = prev_source
            return True  # stale is better than nothing
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
        ns_filter, ns_params = self._ns_filter()
        records = await neo4j_service.execute_read(
            f"MATCH (c:Concept{ns_filter}) RETURN c ORDER BY c.name",
            **ns_params,
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
            f"MATCH (c:Concept{ns_filter})-[:HAS_PROPERTY]->(p:Property{ns_filter}) RETURN c.name AS cn, p",
            **ns_params,
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
            f"MATCH (c:Concept{ns_filter})-[:HAS_RELATION]->(r:Relation{ns_filter}) RETURN c.name AS cn, r",
            **ns_params,
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
            f"MATCH (c:Concept{ns_filter})-[:HAS_ACTION]->(a:Action{ns_filter}) RETURN c.name AS cn, a",
            **ns_params,
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
            output_mapping_raw = a.get("outputMapping", "{}")
            try:
                output_mapping = json.loads(output_mapping_raw) if isinstance(output_mapping_raw, str) else (output_mapping_raw or {})
            except (json.JSONDecodeError, TypeError):
                output_mapping = {}

            concept_map[cn]["actions"].append({
                "name": a.get("name", ""),
                "label": a.get("label", ""),
                "description": a.get("description", ""),
                "inputParams": params,
                "outputType": a.get("outputType", ""),
                "outputMapping": output_mapping,
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
                "outputMapping": output_mapping,
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
                f"MATCH (c:Concept{ns_filter})-[:HAS_RULE]->(r:Rule{ns_filter}) RETURN c.name AS cn, r",
                **ns_params,
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
                        "requiresConfirmation": rule.get("requiresConfirmation", False),
                    })
        except Exception as e:
            log.warning(f"[OntologyService] failed to load rules from Neo4j: {e}")

        # 6) DataFilters: MATCH (c:Concept)-[:HAS_DATAFILTER]->(f:DataFilter)
        try:
            df_records = await neo4j_service.execute_read(
                f"MATCH (c:Concept{ns_filter})-[:HAS_DATAFILTER]->(f:DataFilter{ns_filter}) RETURN c.name AS cn, f",
                **ns_params,
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

        # 9) Schema version from Neo4j
        schema_version = await self._load_schema_version()

        self._data = {
            "meta": {
                "projectName": "manufacturing",
                "description": "制造业本体模型",
                "exportedAt": datetime.now(timezone.utc).isoformat(),
                "version": "1.0",
                "conceptCount": len(concept_map),
                "actionCount": len(action_signatures),
                "systemCount": 4,
                "schemaVersion": schema_version,
            },
            "prompt": prompt,
            "tools": tools,
            "concepts": list(concept_map.values()),
            "mappings": mappings,
            "actionSignatures": action_signatures,
        }
        self._source = f"neo4j://{settings.NEO4J_URI}"
        self._loaded_at = datetime.now(timezone.utc)
        # Update fingerprint so future fingerprint checks can short-circuit
        rule_count = sum(len(c.get("rules", [])) for c in concept_map.values())
        prop_count = sum(len(c.get("properties", [])) for c in concept_map.values())
        rel_count = sum(len(c.get("relations", [])) for c in concept_map.values())
        self._fingerprint = f"c{len(concept_map)}a{len(action_signatures)}r{rule_count}p{prop_count}rel{rel_count}"
        log.info(
            f"ontology loaded from Neo4j: {len(concept_map)} concepts, "
            f"{len(action_signatures)} actions"
        )
        return True

    async def _load_mappings_from_neo4j(self) -> list[dict]:
        from app.services.neo4j_service import neo4j_service
        ns_filter, ns_params = self._ns_filter()
        records = await neo4j_service.execute_read(
            f"MATCH (m:Mapping{ns_filter}) RETURN m",
            **ns_params,
        )
        if records:
            return [dict(r["m"]) for r in records]
        return []

    async def _load_schema_version(self) -> Optional[dict]:
        """Query the latest SchemaVersion node from Neo4j."""
        try:
            from app.services.neo4j_service import neo4j_service
            if not neo4j_service.connected:
                return None
            records = await neo4j_service.execute_read(
                "MATCH (v:SchemaVersion) RETURN v ORDER BY v.version DESC LIMIT 1"
            )
            if records:
                v = records[0]["v"]
                return {
                    "version": v.get("version"),
                    "description": v.get("description", ""),
                    "appliedAt": str(v.get("appliedAt", "")),
                    "checksum": v.get("checksum", ""),
                }
        except Exception:
            pass
        return None

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
                pn = p.get("name", "")
                pl = p.get("label", pn)
                lines.append(f"  · {pn}({p.get('type', 'string')}): {pl}{pk}")
            for r in c.get("relations", []):
                lines.append(f"  → [{r.get('label', '')}] {r.get('target', '')}")
        return "\n".join(lines)


ontology_service = OntologyService()
