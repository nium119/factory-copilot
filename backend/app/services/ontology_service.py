"""本体服务 — 从 Neo4j 加载本体元数据，提供上下文注入。

单一数据源：Neo4j 图数据库（由 OntoStudio 推送）。
无 JSON/YAML 回退 — 如果 Neo4j 不可用，Agent 无法运行。
"""

import asyncio
import json
import threading
from datetime import datetime, timezone
from typing import Optional


from app.core.config import settings
from app.core.logger import log


def _parse_json(raw):
    """将 JSON 字符串解析为 dict。解析失败返回空 dict。"""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            import json
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            pass
    return {}


def _parse_json_list(raw) -> list:
    """将 JSON 字符串或列表解析为 Python list。"""
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
    """从 Neo4j 加载并缓存本体元数据，用于 Agent 上下文增强。

    当缓存数据超过 TTL（默认 5 秒）时自动刷新，因此
    OntoStudio 推送到 Neo4j 的更改可在数秒内反映，
    无需手动重新加载。
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
        # 指标环形缓冲区
        self._reload_durations: list[float] = []     # 最近 N 次完整重载耗时（毫秒）
        self._fingerprint_durations: list[float] = [] # 最近 N 次指纹检查耗时（毫秒）
        self._total_reloads: int = 0
        self._total_checks: int = 0

    @property
    def _ns(self) -> str:
        return settings.NEO4J_NAMESPACE

    def _ns_filter(self, alias: str = "") -> tuple[str, dict]:
        """返回命名空间过滤的 (match_clause, params_dict)。
        当命名空间为空时，返回 ('', None) 以保持向后兼容。
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

    # ── 新鲜度检查 ──

    def _ensure_fresh(self):
        """当缓存 TTL 过期时，安排一个后台指纹检查。

        在每个 getter 开始时调用。非阻塞 — 当前
        调用返回缓存数据；下一次调用获取新鲜数据。

        使用轻量级指纹查询来避免在 Neo4j 未变化时
        进行完整重载。仅当指纹与上次加载不同时，
        才执行昂贵的完整加载（7 个查询）。
        """
        if not self._data or not self._loaded_at:
            return
        # 熔断器：连续失败次数过多后停止自动刷新
        if self._consecutive_failures >= settings.ONTOLOGY_RELOAD_MAX_FAILURES:
            return
        age = (datetime.now(timezone.utc) - self._loaded_at).total_seconds()
        if age < self._cache_ttl:
            return  # 仍然新鲜
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # 无事件循环，跳过自动刷新
        with self._refresh_lock:
            if self._refresh_scheduled:
                return
            self._refresh_scheduled = True
        loop.create_task(self._auto_refresh())

    async def _auto_refresh(self):
        """后台：指纹检查 → 仅在数据变化时完整重载，
        并带有强制重载回退和重复失败时的熔断机制。"""
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
                    # 触发热重编译 (Skill + Agent + 链)
                    try:
                        from app.agents import compile_and_register
                        await compile_and_register()
                    except Exception:
                        pass  # 热编译失败不影响本体刷新
                    tag = "force-reload" if force and not changed else "refreshed"
                    log.info(
                        f"[Ontology] {tag} ({len(self.get_concepts())} concepts)"
                        f" fp={fp_ms:.0f}ms reload={reload_ms:.0f}ms"
                    )
                else:
                    self._consecutive_failures += 1
                    self._last_failure = f"reload returned False ({datetime.now(timezone.utc).isoformat()})"
                    log.warning(f"[Ontology] 重新加载失败 ({self._consecutive_failures}/{settings.ONTOLOGY_RELOAD_MAX_FAILURES})")
            else:
                self._loaded_at = datetime.now(timezone.utc)
        except Exception as e:
            self._consecutive_failures += 1
            self._last_failure = f"{type(e).__name__}: {e}"
            log.warning(f"[Ontology] 自动刷新出错 ({self._consecutive_failures}/{settings.ONTOLOGY_RELOAD_MAX_FAILURES}): {e}")
        finally:
            with self._refresh_lock:
                self._refresh_scheduled = False

    async def _fingerprint_changed(self) -> bool:
        """轻量级检查：按类型统计节点数量。如果数据变化则返回 True。

        仅运行一条快速的 Cypher 查询，而非完整的 7 查询重载。
        可检测增加/删除。修改（例如 requiresConfirmation
        变化）由 OntoStudio 的推送通知处理。
        """
        from app.services.neo4j_service import neo4j_service
        if not neo4j_service.connected:
            return True  # 无法检查，假定已变化
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
            return True  # 出错时，执行完整重载以确保安全

    # ── 公开 API ──

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
        """返回完整的本体系统提示词（所有概念）。"""
        self._ensure_fresh()
        if not self._data:
            return ""
        return self._data.get("prompt", "")

    def get_prompt_for_agent(self, agent_name: str) -> str:
        """返回完整的本体提示词 — 不按 Agent 过滤。"""
        return self.get_prompt()

    def get_prompt_for(self, concept_names: list[str]) -> str:
        """返回仅包含指定概念及其关系的过滤后提示词。

        同时包含被选中概念的关系所引用的目标概念，
        以便 Agent 能看到每个关系的两端。
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
        """从本体 actions 返回 OpenAI 格式的工具定义。"""
        if not self._data:
            return []
        return self._data.get("tools", [])

    def get_tools_for_agent(self, agent_name: str) -> list[dict]:
        """返回所有本体工具 — 不按 Agent 过滤。

        Agent 区分在系统提示词中完成，而非硬编码的工具白名单。
        Neo4j 是唯一数据源。
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

    def resolve_scope(self, concept_name: str) -> Optional[dict]:
        """解析概念的 scope 配置，沿父链向上继承。

        返回 {"scopeConcept", "scopeProperty", "scopeMatchProperty"} 或 None。
        子概念自动继承最近父概念的 scope 配置。
        """
        concept = self.get_concept(concept_name)
        if not concept:
            return None
        # 检查自身
        sc = concept.get("scopeConcept", "")
        if sc:
            return {
                "scopeConcept": sc,
                "scopeProperty": concept.get("scopeProperty", ""),
                "scopeMatchProperty": concept.get("scopeMatchProperty", ""),
            }
        # 沿父链向上查找
        visited = {concept_name}
        queue = list(concept.get("parents", []))
        while queue:
            pname = queue.pop(0)
            if pname in visited:
                continue
            visited.add(pname)
            pc = self.get_concept(pname)
            if not pc:
                continue
            sc = pc.get("scopeConcept", "")
            if sc:
                return {
                    "scopeConcept": sc,
                    "scopeProperty": pc.get("scopeProperty", ""),
                    "scopeMatchProperty": pc.get("scopeMatchProperty", ""),
                }
            queue.extend(pc.get("parents", []))
        return None

    def get_rules_for_agent(self, agent_name: str) -> list[dict]:
        """返回所有规则 — 不按 Agent 过滤。"""
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
                    "targetProperty": r.get("targetProperty", ""),
                })
        return matched

    def get_mappings(self) -> list[dict]:
        if not self._data:
            return []
        return self._data.get("mappings", [])

    def status(self) -> dict:
        """返回管理 API 的当前本体状态。

        包含缓存新鲜度元数据，以便运维监控数据陈旧程度。
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
        """返回负载均衡器/监控用的健康检查状态。

        返回包含以下字段的 dict：
          - healthy: bool — 整体健康状况
          - suggestedHttpStatus: 200 | 503
          - checks: 各检查项结果列表
          - metrics: 用于容量规划的聚合耗时统计

        以下情况视为不健康：
          - 缓存超过 ONTOLOGY_MAX_STALENESS（Neo4j 不可达）
          - 熔断器触发（连续失败次数 >= 最大值）
        """
        checks: list[dict] = []
        healthy = True

        # 检查 1：本体是否已加载
        if not self._data:
            checks.append({"name": "ontology_loaded", "pass": False, "detail": "本体数据未加载"})
            return {"healthy": False, "suggestedHttpStatus": 503, "checks": checks, "metrics": self._metrics_summary()}
        checks.append({"name": "ontology_loaded", "pass": True})

        # 检查 2：缓存新鲜度
        now = datetime.now(timezone.utc)
        cache_age = (now - self._loaded_at).total_seconds() if self._loaded_at else 999999
        max_stale = getattr(settings, 'ONTOLOGY_MAX_STALENESS', 300)
        if cache_age > max_stale:
            healthy = False
            checks.append({
                "name": "cache_freshness",
                "pass": False,
                "detail": f"缓存已过期 {cache_age:.0f} 秒（最大 {max_stale} 秒）。Neo4j 可能不可达。",
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

        # 检查 3：熔断器
        max_fail = settings.ONTOLOGY_RELOAD_MAX_FAILURES
        if self._consecutive_failures >= max_fail:
            healthy = False
            checks.append({
                "name": "neo4j_connectivity",
                "pass": False,
                "detail": f"熔断器已触发：连续 {self._consecutive_failures} 次失败",
                "consecutiveFailures": self._consecutive_failures,
                "maxFailures": max_fail,
                "lastFailure": self._last_failure,
            })
        elif self._consecutive_failures > 0:
            checks.append({
                "name": "neo4j_connectivity",
                "pass": True,
                "detail": f"最近 {self._consecutive_failures} 次失败，但未超过阈值 ({max_fail})",
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
        """聚合耗时统计，用于容量规划和监控。"""
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

    # ── 加载 ──

    async def load(self) -> bool:
        """从 Neo4j 加载本体。加载成功返回 True。

        旧数据在新数据就绪之前保留（原子交换），
        因此并发的 getter 永远不会看到空状态。
        """
        if not settings.NEO4J_ENABLED:
            log.warning("本体：Neo4j 已禁用，无法加载")
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
            log.warning(f"Neo4j 本体加载失败: {e}")
            self._consecutive_failures += 1
            self._last_failure = f"{type(e).__name__}: {e}"

        # 失败时恢复之前的状态（如果有的话）
        if prev_data is not None:
            self._data = prev_data
            self._source = prev_source
            return True  # 陈旧数据总比没有好
        return False

    async def reload(self) -> bool:
        """从 Neo4j 重新加载本体。"""
        return await self.load()

    # ── 内部实现 ──

    async def _load_from_neo4j(self) -> bool:
        """从 Neo4j 加载本体元数据。查询 Concept/Action/Property/Relation
        节点并将其组装为所有消费者期望的 bundle 格式。

        需要 Neo4j 已推送 Ontology-Graph schema（push_schema）。
        """
        from app.services.neo4j_service import neo4j_service

        if not neo4j_service.connected:
            return False

        # 1) Concepts
        ns_filter, ns_params = self._ns_filter()
        records = await neo4j_service.execute_read(
            f"MATCH (c:Concept{ns_filter}) RETURN c ORDER BY coalesce(c.seq, 999), c.name",
            params=ns_params,
        )
        if not records:
            log.warning("[Ontology] Neo4j 中没有 Concept 节点 — 请先执行 push_schema")
            return False

        concept_map: dict[str, dict] = {}
        for r in records:
            c = r["c"]
            concept_map[c["name"]] = {
                "name": c["name"],
                "label": c.get("label", c["name"]),
                "description": c.get("description", ""),
                "scopeConcept": c.get("scopeConcept", ""),
                "scopeProperty": c.get("scopeProperty", ""),
                "scopeMatchProperty": c.get("scopeMatchProperty", ""),
                "parents": _parse_json_list(c.get("parents", "[]")),
                "authorized_roles": _parse_json_list(c.get("authorized_roles", "[]")),
                "seq": c.get("seq", 999),
                "properties": [],
                "relations": [],
                "actions": [],
                "rules": [],
                "dataFilters": [],
            }

        # 2) Properties: MATCH (c:Concept)-[:HAS_PROPERTY]->(p:Property)
        prop_records = await neo4j_service.execute_read(
            f"MATCH (c:Concept{ns_filter})-[:HAS_PROPERTY]->(p:Property{ns_filter}) RETURN c.name AS cn, p ORDER BY coalesce(p.seq, 999), p.name",
            params=ns_params,
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
            params=ns_params,
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
            params=ns_params,
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

            # 构建 actionSignatures 条目
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
                        "defaultValue": p.get("defaultValue", ""),
                        "conceptPropertyRef": p.get("conceptPropertyRef", ""),
                        "enumValues": p.get("enumValues") or [],
                    }
                    for p in params
                ],
            })

            # 构建 tools 条目
            props = {}
            required_list = []
            for p in params:
                json_type = {
                    "int": "integer", "float": "number", "bool": "boolean",
                }.get(p.get("paramType", p.get("type", "string")), "string")
                desc = p.get("description", "") or p.get("label", p.get("name", ""))
                props[p["name"]] = {
                    "type": json_type,
                    "description": f"{p.get('label', p.get('name', ''))}: {desc}" if desc else p.get("label", p.get("name", "")),
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
                params=ns_params,
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
                        "targetProperty": rule.get("targetProperty", ""),
                        "authorized_roles": _parse_json_list(rule.get("authorized_roles", "[]")),
                        "nextRules": _parse_json_list(rule.get("nextRules", "[]")),
                        "requiresConfirmation": rule.get("requiresConfirmation", False),
                    })
        except Exception as e:
            log.warning(f"[OntologyService] 从 Neo4j 加载规则失败: {e}")

        # 6) DataFilters: MATCH (c:Concept)-[:HAS_DATAFILTER]->(f:DataFilter)
        try:
            df_records = await neo4j_service.execute_read(
                f"MATCH (c:Concept{ns_filter})-[:HAS_DATAFILTER]->(f:DataFilter{ns_filter}) RETURN c.name AS cn, f",
                params=ns_params,
            )
            for r in df_records:
                cn = r.get("cn", "")
                if cn in concept_map:
                    f = r["f"]
                    concept_map[cn]["dataFilters"].append({
                        "property": f.get("property", ""),
                        "matchProperty": f.get("matchProperty", ""),
                        "scopeConcept": f.get("scopeConcept", ""),
                        "scopeProperty": f.get("scopeProperty", ""),
                        "scopeMatchProperty": f.get("scopeMatchProperty", ""),
                        "roles": _parse_json_list(f.get("roles", "[]")),
                        "visibleProperties": _parse_json_list(f.get("visibleProperties", "[]")),
                    })
        except Exception as e:
            log.warning(f"[OntologyService] 从 Neo4j 加载数据过滤器失败: {e}")

        # 7) 从概念构建提示词
        prompt = self._build_prompt_from_concepts(list(concept_map.values()))

        # 8) 从 Neo4j 加载映射
        mappings = await self._load_mappings_from_neo4j()

        # 9) 从 Neo4j 加载 Schema 版本
        schema_version = await self._load_schema_version()

        # 10) 从 Neo4j 加载项目元数据（由 OntoStudio 推送）
        project_meta = await self._load_project_meta()

        self._data = {
            "meta": {
                "projectName": project_meta.get("name", ""),
                "description": project_meta.get("description", ""),
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
        # 更新指纹，以便后续指纹检查可以快速跳过
        rule_count = sum(len(c.get("rules", [])) for c in concept_map.values())
        prop_count = sum(len(c.get("properties", [])) for c in concept_map.values())
        rel_count = sum(len(c.get("relations", [])) for c in concept_map.values())
        self._fingerprint = f"c{len(concept_map)}a{len(action_signatures)}r{rule_count}p{prop_count}rel{rel_count}"
        log.info(
            f"本体已从 Neo4j 加载: {len(concept_map)} concepts, "
            f"{len(action_signatures)} actions"
        )
        return True

    async def _load_mappings_from_neo4j(self) -> list[dict]:
        from app.services.neo4j_service import neo4j_service
        ns_filter, ns_params = self._ns_filter()
        records = await neo4j_service.execute_read(
            f"MATCH (m:Mapping{ns_filter}) RETURN m",
            params=ns_params,
        )
        if records:
            return [dict(r["m"]) for r in records]
        return []

    async def _load_schema_version(self) -> Optional[dict]:
        """从 Neo4j 查询最新的 SchemaVersion 节点。"""
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

    async def _load_project_meta(self) -> dict:
        """从 Neo4j 查询项目元数据节点（由 OntoStudio 推送）。"""
        try:
            from app.services.neo4j_service import neo4j_service
            if not neo4j_service.connected:
                return {}
            records = await neo4j_service.execute_read(
                "MATCH (p:Project {namespace: $ns}) RETURN p.name AS name, p.description AS description",
                {"ns": settings.NEO4J_NAMESPACE},
            )
            if records:
                return {"name": records[0].get("name", ""), "description": records[0].get("description", "")}
        except Exception:
            pass
        return {}

    def _build_prompt_from_concepts(self, concepts: list[dict]) -> str:
        """根据概念定义构建系统提示词字符串，包含计算字段与列头规则。"""
        domain_desc = self.meta.get("description") or self.meta.get("projectName") or "通用领域"
        lines = [f"你是一个{domain_desc}领域的查询助手。", "", "## 领域概念", ""]
        computed_rules = []
        for c in concepts:
            lines.append(f"### {c.get('label', '')} ({c.get('name', '')})")
            if c.get("description"):
                lines.append(f"  {c['description']}")
            for p in c.get("properties", []):
                pk = " [主键]" if p.get("isPrimary") else ""
                pn = p.get("name", "")
                pl = p.get("label", pn)
                suffix = " [计算]" if p.get("type") == "computed" else ""
                lines.append(f"  · {pn}({p.get('type', 'string')}): {pl}{pk}{suffix}")
            for r in c.get("rules", []):
                if r.get("ruleType") == "computed" and r.get("expression"):
                    computed_rules.append({
                        "concept": c["name"],
                        "target": r.get("targetProperty", ""),
                        "label": r.get("label", ""),
                        "expr": r["expression"],
                    })
            for r in c.get("relations", []):
                lines.append(f"  → [{r.get('label', '')}] {r.get('target', '')}")

        # 按概念分组计算字段，生成强制性查询模板
        concept_computed: dict[str, list] = {}
        for cr in computed_rules:
            concept_computed.setdefault(cr['concept'], []).append(cr)

        if concept_computed:
            lines.append("")
            lines.append("## 查询模板（CRITICAL — 必须使用以下精确模板！）")
            lines.append("**禁止自己写 RETURN 子句！** 直接复制模板中的 MATCH + OPTIONAL MATCH + RETURN。")
            for cn, crs in concept_computed.items():
                concept = next((c for c in concepts if c["name"] == cn), None)
                label = concept["label"] if concept else cn
                props = concept["properties"] if concept else []
                disp_cols = [p for p in props if p["name"].endswith("Display")]
                pk_col = next((p for p in props if p.get("isPrimary")), None)
                pk_alias = f"a.{pk_col['name']} AS {pk_col['label']}" if pk_col else f"a.id AS 编号"

                lines.append(f"### {label}（{cn}）")
                lines.append("```")
                lines.append(f"MATCH (a:{cn})")
                aliases = []
                for i, cr in enumerate(crs):
                    alias = f"b{i+1}"
                    aliases.append(alias)
                    lines.append(f"OPTIONAL MATCH {cr['expr']}")
                ret_cols = [pk_alias]
                for p in props[:10]:
                    if p["name"] in [cr["target"] for cr in crs] or p["name"].endswith("Display"):
                        continue
                    if p.get("isPrimary"):
                        continue
                    ret_cols.append(f"a.{p['name']} AS {p.get('label', p['name'])}")
                for dp in disp_cols:
                    base = dp["name"].replace("Display", "")
                    for p in props:
                        if p["name"] == base:
                            ret_cols.append(f"a.{dp['name']} AS {p.get('label', base)}")
                            break
                    else:
                        ret_cols.append(f"a.{dp['name']} AS {dp.get('label', dp['name'])}")
                for i, cr in enumerate(crs):
                    ret_cols.append(f"{aliases[i]} IS NOT NULL AS {cr['target']}")
                lines.append("RETURN " + ",\n  ".join(ret_cols))
                lines.append("```")
                lines.append("")

        # 关系路径 — 告诉 LLM 可以做多跳遍历
        edge_set = set()
        for c in concepts:
            for r in c.get("relations", []):
                edge = f"({c['name']})-[:{r.get('label', '')}]->({r.get('target', '')})"
                edge_set.add(edge)
        if edge_set:
            lines.append("")
            lines.append("## 关系路径（可做多跳遍历 + 聚合统计）")
            lines.append("查询涉及跨概念分析时，沿以下路径 MATCH，可用 sum/count/avg/round 做聚合：")
            for e in sorted(edge_set):
                lines.append(f"  {e}")

        lines.append("")
        lines.append("## 重要规则")
        lines.append("- 简单查询：复制上述模板为基础，添加 WHERE 条件即可")
        lines.append("- 跨概念查询/统计/聚合：沿「关系路径」做 MATCH 遍历，**可以自定义 RETURN** 使用 sum/count/avg/round 等聚合函数")
        lines.append("- 字符串匹配用 CONTAINS，数值用 =")
        lines.append("- 必须含 LIMIT")

        return "\n".join(lines)


ontology_service = OntologyService()
