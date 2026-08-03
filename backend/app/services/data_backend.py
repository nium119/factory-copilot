"""数据后端 — 统一业务数据访问抽象。

两个后端，一个接口：
  Neo4jBackend  — Cypher 查询图数据库
  ApiBackend    — HTTP 调用外部服务（MES 系统配置端点）

查询结果通过 format_concept_items 按本体概念属性定义统一格式化。
降级链: Neo4j → Api（首个可用的后端返回结果）。
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.core.logger import log


# ── 抽象接口 ──────────────────────────────────────────────────

class DataBackend(ABC):
    """业务数据访问抽象。

    三个核心操作覆盖所有实体查询与创建需求：
      resolve_entity — 按关键字查找单个实体
      query         — 过滤查询，可附带关系遍历
      create        — 创建新实体
    """

    @abstractmethod
    async def resolve_entity(
        self, concept: str, keyword: str,
    ) -> Optional[dict]:
        """按关键字（id 或名称）查找单个实体。返回实体字典或 None。"""

    @abstractmethod
    async def query(
        self,
        concept: str,
        filters: Dict[str, Any],
        relations: Optional[List[str]] = None,
    ) -> List[dict]:
        """带过滤条件的列表查询，可附带关系遍历。

        参数:
            concept:   概念名，如 "WorkOrder"、"Equipment"
            filters:   {属性名: 值, ...}
            relations: 要包含的关联概念名，如 ["Product", "QualityCheck"]
        """

    @abstractmethod
    async def create(
        self, concept: str, data: Dict[str, Any],
    ) -> dict:
        """创建新实体。返回创建后的实体字典。"""

    @abstractmethod
    async def delete(
        self, concept: str, pk_name: str, pk_value: str,
    ) -> bool:
        """删除实体。pk_name 为主键属性名，pk_value 为主键值。"""

    @abstractmethod
    async def health(self) -> dict:
        """健康检查。返回 {"ok": true, "backend": "neo4j", ...}"""


# ── Neo4j 后端 ────────────────────────────────────────────────

class Neo4jBackend(DataBackend):
    """基于 Cypher 的图数据库后端。"""

    async def _execute(self, cypher: str, params: dict = None) -> list[dict]:
        from app.services.neo4j_service import neo4j_service
        return await neo4j_service.execute_read(cypher, params)

    async def _execute_write(self, cypher: str, params: dict = None) -> list[dict]:
        from app.services.neo4j_service import neo4j_service
        return await neo4j_service.execute_write(cypher, params)

    @property
    def _available(self) -> bool:
        from app.services.neo4j_service import neo4j_service
        return neo4j_service.connected

    @staticmethod
    def _ns_where(concept_name: str = "") -> tuple[str, dict]:
        """返回业务数据 namespace 过滤的 (where子句, 参数)。
        优先从概念取值，没有则用全局配置兜底。
        """
        ns = ""
        if concept_name:
            from app.services.ontology_service import ontology_service
            concept = ontology_service.get_concept(concept_name)
            ns = (concept or {}).get("namespace", "")
        ns = ns or settings.NEO4J_NAMESPACE
        if not ns:
            return "", {}
        return "n._namespace = $ns", {"ns": ns}

    async def resolve_entity(
        self, concept: str, keyword: str,
    ) -> Optional[dict]:
        if not self._available:
            return None
        # 动态获取概念主键名
        from app.services.ontology_service import ontology_service
        c = ontology_service.get_concept(concept)
        pk_name = 'id'
        if c:
            for pp in c.get('properties', []):
                if pp.get('isPrimary'):
                    pk_name = pp['name']
                    break
        ns_clause, ns_params = self._ns_where(concept)
        ns_where = f" AND {ns_clause}" if ns_clause else ""
        label = concept
        # 先精确匹配主键
        records = await self._execute(
            f"MATCH (n:{label}) WHERE n.`{pk_name}` = $kw{ns_where} RETURN n LIMIT 1",
            {"kw": keyword, **ns_params},
        )
        if records:
            return dict(records[0]["n"])
        # 再模糊匹配 name
        records = await self._execute(
            f"MATCH (n:{label}) WHERE n.name CONTAINS $kw{ns_where} RETURN n LIMIT 1",
            {"kw": keyword, **ns_params},
        )
        if records:
            return dict(records[0]["n"])
        return None

    async def query(
        self,
        concept: str,
        filters: Dict[str, Any],
        relations: Optional[List[str]] = None,
    ) -> List[dict]:
        if not self._available:
            return []
        label = concept

        where_clauses = []
        params = {}
        cross_entity = filters.pop('_cross_concept', None)
        cross_id = filters.pop('_cross_entity', None)
        cross_name = filters.pop('_cross_entity_name', None)
        scope_concept = filters.pop('_scope_concept', None)
        scope_property = filters.pop('_scope_property', None)
        scope_value = filters.pop('_scope_value', None)

        # 模糊搜索（企业级多字段 OR + 命中分级排序）
        fuzzy_kw = filters.pop('_fuzzy', None)
        fuzzy_op = filters.pop('_fuzzy_op', 'contains') or 'contains'
        fuzzy_fields = filters.pop('_fuzzy_fields', []) or []
        fuzzy_order = []
        if fuzzy_kw and fuzzy_fields:
            params['_fuzzy'] = fuzzy_kw
            # 主条件：多字段 OR（prefix → STARTS WITH，否则 CONTAINS）
            op_fn = 'STARTS WITH' if fuzzy_op == 'prefix' else 'CONTAINS'
            or_parts = [f"n.`{f}` {op_fn} $_fuzzy" for f in fuzzy_fields]
            if or_parts:
                where_clauses.append("(" + " OR ".join(or_parts) + ")")
            # 命中分级排序：精确 > 前缀 > 包含（名称字段优先）
            exact_parts = [f"n.`{f}` = $_fuzzy" for f in fuzzy_fields]
            prefix_parts = [f"n.`{f}` STARTS WITH $_fuzzy" for f in fuzzy_fields]
            fuzzy_order = [
                f"CASE WHEN {' OR '.join(exact_parts)} THEN 0 ELSE 1 END",
                f"CASE WHEN {' OR '.join(prefix_parts)} THEN 0 ELSE 1 END",
            ]

        # namespace 过滤，按概念自动切换
        ns_clause, ns_params2 = self._ns_where(concept)
        if ns_clause:
            where_clauses.append(ns_clause)
            params.update(ns_params2)

        for i, (k, v) in enumerate(filters.items()):
            if k.startswith('_'):
                continue
            if v is None or v == "":
                continue
            pname = f"p{i}"
            if isinstance(v, str):
                where_clauses.append(f"n.{k} CONTAINS ${pname}")
            else:
                where_clauses.append(f"n.{k} = ${pname}")
            params[pname] = v

        scope_match = ""
        if scope_concept and scope_property and scope_value:
            scope_match = (
                f"-[:*1..3]->(scope:{scope_concept} {{{scope_property}: $scope_value}})"
            )
            params["scope_value"] = scope_value

        cross_match = ""
        if cross_entity and cross_id:
            cross_label = cross_entity
            from app.services.ontology_service import ontology_service
            concept_def = ontology_service.get_concept(concept)
            rel_label = None
            if concept_def:
                for rel in concept_def.get("relations", []):
                    if rel["target"] == cross_entity:
                        rel_label = rel.get("label", "")
                        break
            if rel_label:
                cross_match = f"-[:{rel_label}]->(e:{cross_label} {{id: $cross_id}})"
            else:
                cross_match = (
                    f"-[*1..2]-(e:{cross_label} {{id: $cross_id}})"
                )
            params["cross_id"] = cross_id

        # 构建 MATCH：scope traversal 融入主路径，cross 用逗号并联
        if scope_match and cross_match:
            cypher = f"MATCH (n:{label}){scope_match}, (n){cross_match}"
        elif scope_match:
            cypher = f"MATCH (n:{label}){scope_match}"
        elif cross_match:
            cypher = f"MATCH (n:{label}){cross_match}"
        else:
            cypher = f"MATCH (n:{label})"

        if where_clauses:
            cypher += " WHERE " + " AND ".join(where_clauses)
        # 模糊搜索命中分级排序：精确 > 前缀 > 默认
        _order_by = ", ".join(fuzzy_order) + ", " if fuzzy_order else ""
        cypher += f" RETURN DISTINCT n ORDER BY {_order_by}n.id LIMIT 50"

        records = await self._execute(cypher, params)
        return [dict(r["n"]) for r in records]

    async def create(
        self, concept: str, data: Dict[str, Any],
    ) -> dict:
        if not self._available:
            return {"error": "neo4j 不可用"}
        from app.services.neo4j_service import neo4j_service

        label = concept

        # 确定主键：从本体概念定义读取，默认 "code"
        pk_name = "code"
        try:
            from app.services.ontology_service import ontology_service
            concept_def = ontology_service.get_concept(concept)
            if concept_def:
                for prop in concept_def.get("properties", []):
                    if prop.get("isPrimary"):
                        pk_name = prop.get("name", "code")
                        break
        except Exception:
            pass

        pk_value = data.get(pk_name)
        # 无主键值时生成新 ID
        if not pk_value:
            await neo4j_service.ensure_unique_constraint(label, pk_name)
            seq = await neo4j_service.next_sequence(label)
            prefix = concept[:4].upper()
            pk_value = f"{prefix}-{seq:03d}"
            data[pk_name] = pk_value

        # 填充概念属性的默认值
        try:
            from app.services.ontology_service import ontology_service
            concept_def = ontology_service.get_concept(concept)
            if concept_def:
                for prop in concept_def.get("properties", []):
                    pname = prop.get("name", "")
                    pdefault = prop.get("defaultValue")
                    if pname and pname not in data and pdefault:
                        data[pname] = pdefault
        except Exception:
            pass

        # 提取 scope 建边参数（caller 可选传入，不写入节点属性）
        scope_concept = data.pop("_scope_concept", None)
        scope_property = data.pop("_scope_property", None)
        scope_value = data.pop("_scope_value", None)

        # 自动从本体解析 scope（caller 未传入时）
        if not scope_concept:
            try:
                from app.services.ontology_service import ontology_service
                s = ontology_service.resolve_scope(concept)
                if s and s.get("scopeProperty"):
                    scope_concept = s["scopeConcept"]
                    scope_property = s["scopeProperty"]
                    # scope_value 从数据中提取，或回退到配置
                    scope_value = data.get(scope_property) or settings.MES_PLANT_CODE
            except Exception:
                pass

        props = dict(data)
        ns = settings.NEO4J_NAMESPACE
        if ns:
            props["_namespace"] = ns
        set_clauses = ", ".join(f"n.{k} = ${k}" for k in props if k != pk_name)
        props["_pk_value"] = pk_value
        await self._execute_write(
            f"MERGE (n:{label} {{{pk_name}: $_pk_value}}) SET {set_clauses} RETURN n",
            props,
        )

        # 建 scope 关系边
        if scope_concept and scope_property and scope_value and scope_concept != label:
            try:
                await self._execute_write(
                    f"MATCH (n:{label} {{{pk_name}: $pk}}) "
                    f"MERGE (s:{scope_concept} {{{scope_property}: $scope_value}}) "
                    f"MERGE (n)-[:BELONGS_TO]->(s)",
                    {"pk": pk_value, "scope_value": scope_value},
                )
            except Exception:
                pass

        result = {pk_name: pk_value, **data}
        return result

    async def delete(self, concept: str, pk_name: str, pk_value: str) -> bool:
        """删除概念节点及其所有关系。返回实际删除数 > 0。"""
        if not self._available:
            return False
        label = concept
        ns = settings.NEO4J_NAMESPACE
        where = f" AND n._namespace = $ns" if ns else ""
        # RETURN count(n) 确保能判断是否真的删除了
        cypher = f"MATCH (n:{label} {{{pk_name}: $pk_value}}) WHERE true{where} DETACH DELETE n RETURN count(n)"
        params: dict = {"pk_value": pk_value}
        if ns:
            params["ns"] = ns
        try:
            records = await self._execute_write(cypher, params)
            return bool(records and records[0].get("count(n)", 0) > 0)
        except Exception:
            return False

    async def health(self) -> dict:
        ok = self._available
        return {
            "ok": ok, "backend": "neo4j",
            "uri": settings.NEO4J_URI if ok else None,
        }


# ── API 后端 ──────────────────────────────────────────────────

class ApiBackend(DataBackend):
    """REST API 后端，通过系统配置的端点直连外部 API。"""

    def __init__(self):
        pass

    @property
    def _available(self) -> bool:
        return bool(settings.MES_API_BASE_URL)

    async def resolve_entity(
        self, concept: str, keyword: str,
    ) -> Optional[dict]:
        if not self._available:
            return None
        from app.services.multi_system_backend import multi_system_backend
        system = multi_system_backend._resolve_system(concept)
        if not system:
            return None
        result_text = await multi_system_backend._query_api(concept, {"keyword": keyword}, system)
        return None  # resolve_entity 暂不走 API，统一走 Neo4j

    async def query(
        self,
        concept: str,
        filters: Dict[str, Any],
        relations: Optional[List[str]] = None,
    ) -> List[dict]:
        if not self._available:
            return []
        from app.services.multi_system_backend import multi_system_backend
        system = multi_system_backend._resolve_system(concept)
        if not system:
            return []
        raw = {k: v for k, v in filters.items() if v and not k.startswith("_")}
        _, items = await multi_system_backend._query_api(concept, raw, system)
        return items

    async def create(
        self, concept: str, data: Dict[str, Any],
    ) -> dict:
        return {"error": "API 创建走系统配置端点，暂不支持"}

    async def delete(self, concept: str, pk_name: str, pk_value: str) -> bool:
        return False  # API 删除暂不支持

    async def health(self) -> dict:
        ok = self._available
        return {"ok": ok, "backend": "api", "base_url": settings.MES_API_BASE_URL or ""}


# ── 降级链 ────────────────────────────────────────────────────

class FallbackDataBackend(DataBackend):
    """本体驱动的多后端编排器。

    路由由本体定义自动决定，无需手动 dataSource 标记：
      _needs_neo4j  → DataFilter / 跨概念关系 / 规则 → 数据必须入图
      _has_api_config  → 概念注册了 MES 适配器 → 可通过 API 读写

    查写分离：
      query/resolve → _needs_neo4j ? Neo4jBackend : ApiBackend
      create → _has_api_config ? ApiBackend → Neo4j 同步 : Neo4jBackend
    """

    def __init__(self):
        self._neo4j: Optional[Neo4jBackend] = None
        self._api: Optional[ApiBackend] = None

    async def initialize(self) -> None:
        """初始化 Neo4j + API 双后端。"""
        self._neo4j = Neo4jBackend()
        self._api = ApiBackend()

        for b in [self._neo4j, self._api]:
            h = await b.health()
            log.info(f"[DataBackend] {h['backend']}: {'可用' if h['ok'] else '不可用'}")

    def _needs_neo4j(self, concept_name: str) -> bool:
        """概念是否需要在 Neo4j 中有数据。

        scope / DataFilter / 规则 / 跨概念关系 需要图数据库才能生效。
        父概念关系不算（继承链是树结构，不需要图遍历）。
        """
        try:
            from app.services.ontology_service import ontology_service
            concept = ontology_service.get_concept(concept_name)
        except Exception:
            return True
        if not concept:
            return True  # 未找到定义，保守回退到 Neo4j
        # 概念级 scope 需要图遍历
        if concept.get("scopeConcept"):
            return True
        # 继承的 scope 也需要图遍历
        if ontology_service.resolve_scope(concept_name):
            return True
        if concept.get("dataFilters"):
            return True
        if concept.get("rules"):
            return True
        parents = set(concept.get("parents", []))
        for r in concept.get("relations", []):
            if r.get("target") not in parents:
                return True  # 有指向其他业务概念的跨概念关系
        return False

    def _has_api_config(self, concept_name: str) -> bool:
        """概念是否配置了 API 系统端点（不等于 neo4j 默认系统）。"""
        try:
            from app.services.multi_system_backend import multi_system_backend
            system = multi_system_backend._resolve_system(concept_name)
            return system is not None and system.is_api
        except Exception:
            return False

    def _get_api_config(self, concept_name: str) -> dict:
        """获取概念的 API 端点配置（含 dualWriteNeo4j 等选项）。"""
        try:
            from app.services.multi_system_backend import multi_system_backend
            system = multi_system_backend._resolve_system(concept_name)
            if system and system.is_api:
                for ep in (system.endpoints or []):
                    if ep.get("concept") == concept_name:
                        return ep
        except Exception:
            pass
        return {}

    async def _try_backend(self, backend, method: str, concept: str, *args, **kwargs):
        """单后端调用，含 health check。失败返回 None。"""
        if backend is None:
            return None
        h = await backend.health()
        if not h.get("ok"):
            return None
        try:
            fn = getattr(backend, method)
            return await fn(concept, *args, **kwargs)
        except Exception as e:
            log.warning(f"[DataBackend] {backend.__class__.__name__}.{method}({concept}) 失败: {e}")
            return None

    async def _cache_results(self, concept: str, records: list):
        """API 结果写回 Neo4j，每条打入 _cached_at 时间戳。Neo4jBackend.create 自动处理 scope 建边。"""
        if not self._neo4j or not records:
            return
        nh = await self._neo4j.health()
        if not nh.get("ok"):
            return
        from datetime import datetime, timezone

        now_ts = datetime.now(timezone.utc).isoformat()
        cached = 0
        for record in records:
            try:
                record["_cached_at"] = now_ts
                await self._neo4j.create(concept, record)
                cached += 1
            except Exception:
                pass
        if cached:
            log.info(f"[DataBackend] 缓存 {cached}/{len(records)} 条 {concept} 到 Neo4j")

    async def resolve_entity(
        self, concept: str, keyword: str,
    ) -> Optional[dict]:
        if self._needs_neo4j(concept):
            return await self._try_backend(self._neo4j, "resolve_entity", concept, keyword)
        return await self._try_backend(self._api, "resolve_entity", concept, keyword)

    async def query(
        self,
        concept: str,
        filters: Dict[str, Any],
        relations: Optional[List[str]] = None,
    ) -> List[dict]:
        if self._has_api_config(concept):
            return await self._try_backend(self._api, "query", concept, filters, relations) or []
        if self._needs_neo4j(concept):
            return await self._try_backend(self._neo4j, "query", concept, filters, relations) or []
        return await self._try_backend(self._api, "query", concept, filters, relations) or []

    async def create(
        self, concept: str, data: Dict[str, Any],
    ) -> dict:
        if self._has_api_config(concept):
            result = await self._try_backend(self._api, "create", concept, data)
            if result and not result.get("error") and self._needs_neo4j(concept):
                await self._cache_results(concept, [result])
            return result if result is not None else {"error": "api 创建失败"}
        result = await self._try_backend(self._neo4j, "create", concept, data)
        return result if result is not None else {"error": "neo4j 创建失败"}

    async def delete(self, concept: str, pk_name: str, pk_value: str) -> bool:
        """删除：API 优先，失败/不支持时降级 Neo4j。"""
        if self._has_api_config(concept):
            result = await self._try_backend(self._api, "delete", concept, pk_name, pk_value)
            if result:
                # 双写清理: 从 API 配置读取 dualWriteNeo4j 开关
                api_cfg = self._get_api_config(concept)
                if api_cfg and api_cfg.get("dualWriteNeo4j"):
                    await self._try_backend(self._neo4j, "delete", concept, pk_name, pk_value)
                return True
        return bool(await self._try_backend(self._neo4j, "delete", concept, pk_name, pk_value))

    async def health(self) -> dict:
        backends = {}
        all_ok = False
        for b in [self._neo4j, self._api]:
            if b:
                h = await b.health()
                backends[h["backend"]] = h
                if h["ok"]:
                    all_ok = True
        return {"ok": all_ok, "primary": "neo4j", "backends": backends}


# ── 统一输出格式化 ─────────────────────────────────────────────────
# 按本体概念属性定义统一格式化查询结果（列顺序 + 中文标签）

def format_concept_items(concept_name: str, rows: list[dict]) -> list[dict]:
    """按本体概念属性定义统一格式化查询结果。

    规则：
    - 列顺序 = 概念属性在 YAML 中的定义顺序
    - key = 属性 label（中文），无 label 时用 name
    - bool → "是" / "否"
    - ref 有 Display → 用 Display，否则用原始值
    """
    from app.services.ontology_service import ontology_service
    concepts = ontology_service.get_concepts() or []
    props = []
    for c in concepts:
        if c.get("name") == concept_name:
            for p in c.get("properties", []):
                props.append({
                    "name": p.get("name", ""),
                    "label": p.get("label", ""),
                    "type": p.get("type", ""),
                    "refConcept": p.get("refConcept", ""),
                    "enumValues": _parse_enum(p.get("enumValues")),
                })
            break
    items = []
    for row in rows:
        item = {}
        # 补填缺失的 bool 属性（Neo4j driver 不返回 null 属性）
        for prop in props:
            if prop["type"] == "bool" and prop["name"] not in row:
                row[prop["name"]] = False
        for prop in props:
            key = prop["label"] or prop["name"]
            val = row.get(prop["name"])
            if val is None:
                val = row.get(prop["name"] + "Display", "")
            if prop["type"] == "bool":
                val = "✅" if val in (True, "true", "True", 1, "1") else "❌"
            elif prop["type"] == "ref" and prop.get("refConcept"):
                display = row.get(prop["name"] + "Display")
                if display:
                    val = display
                elif prop["enumValues"]:
                    sv = str(val) if val is not None else ""
                    if sv in prop["enumValues"]:
                        val = prop["enumValues"][sv]
                elif val is not None:
                    val = _lookup_dict_label(prop["refConcept"], val)
            elif prop["enumValues"]:
                ev = prop["enumValues"]
                sv = str(val) if val is not None else ""
                if sv in ev:
                    val = ev[sv]
            item[key] = val if val is not None else ""
        items.append(item)
    return items


def _parse_enum(ev):
    """解析 enumValues: 支持 dict / list / JSON 字符串格式。"""
    if isinstance(ev, dict):
        return ev
    if isinstance(ev, str) and ev.strip():
        try:
            import json
            return json.loads(ev)
        except (json.JSONDecodeError, TypeError):
            pass
    if isinstance(ev, list):
        return {str(i): str(v) for i, v in enumerate(ev)}
    return {}


def _lookup_dict_label(ref_concept: str, code) -> str:
    """从本体数据字典中查找 code 对应的 label。

    字典概念如 OrderStatus / DefectLevel，individuals 中的 name 是编码，label 是显示名。
    """
    try:
        from app.services.ontology_service import ontology_service
        concepts = ontology_service.get_concepts() or []
        code_str = str(code) if code is not None else ""
        for c in concepts:
            if c.get("name") == ref_concept:
                for ind in c.get("individuals", []):
                    if ind.get("name") == code_str:
                        return ind.get("label") or code_str
                break
    except Exception:
        pass
    return code_str


# 单例
data_backend = FallbackDataBackend()
