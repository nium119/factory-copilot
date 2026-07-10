"""数据后端 — 统一业务数据访问抽象。

两个后端，一个接口：
  Neo4jBackend  — Cypher 查询图数据库
  ApiBackend    — HTTP 调用外部服务（MES/ERP）

所有请求/响应翻译由 ConceptAdapter 处理，不走 YAML 字段映射。
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
    def _ns_where() -> tuple[str, dict]:
        """返回业务数据 namespace 过滤的 (where子句, 参数)。"""
        ns = settings.NEO4J_NAMESPACE
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
        ns_clause, ns_params = self._ns_where()
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

        # namespace 过滤，实现多项目隔离
        ns_clause, ns_ns_params = self._ns_where()
        if ns_clause:
            where_clauses.append(ns_clause)
            params.update(ns_ns_params)

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
        cypher += " RETURN DISTINCT n ORDER BY n.id LIMIT 50"

        records = await self._execute(cypher, params)
        return [dict(r["n"]) for r in records]

    async def create(
        self, concept: str, data: Dict[str, Any],
    ) -> dict:
        if not self._available:
            return {"error": "neo4j 不可用"}
        from app.services.neo4j_service import neo4j_service

        label = concept

        await neo4j_service.ensure_unique_constraint(label)
        seq = await neo4j_service.next_sequence(label)

        # 从已有节点提取 id 前缀
        records = await self._execute(
            f"MATCH (n:{label}) RETURN n.id AS id LIMIT 1"
        )
        if records and records[0].get("id"):
            prefix = records[0]["id"].split("-")[0]
        else:
            prefix = concept[:4].upper()

        new_id = f"{prefix}-{seq:03d}"

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

        props = {**data, "id": new_id}
        ns = settings.NEO4J_NAMESPACE
        if ns:
            props["_namespace"] = ns
        set_clauses = ", ".join(f"n.{k} = ${k}" for k in props)
        params = {k: v for k, v in props.items()}
        await self._execute_write(
            f"MERGE (n:{label} {{id: $id}}) ON CREATE SET {set_clauses} RETURN n",
            params,
        )

        # 建 scope 关系边：数据节点 → scope 锚点概念
        # 跳过自环：节点自身就是 scope 锚点时不建边
        if scope_concept and scope_property and scope_value and scope_concept != label:
            try:
                await self._execute_write(
                    f"MATCH (n:{label} {{id: $new_id}}) "
                    f"MERGE (s:{scope_concept} {{{scope_property}: $scope_value}}) "
                    f"MERGE (n)-[:BELONGS_TO]->(s)",
                    {"new_id": new_id, "scope_value": scope_value},
                )
            except Exception:
                pass  # 建边失败不阻塞节点创建

        return props

    async def health(self) -> dict:
        ok = self._available
        return {
            "ok": ok, "backend": "neo4j",
            "uri": settings.NEO4J_URI if ok else None,
        }


# ── API 后端 ──────────────────────────────────────────────────

class ApiBackend(DataBackend):
    """REST API 后端，用于调用外部服务。

    所有翻译通过 ConceptAdapter 完成 — 每个外部概念必须有注册的适配器，
    不走 YAML 字段映射。
    """

    def __init__(self):
        self._clients: dict[str, object] = {}

    @staticmethod
    def _get_adapter(concept: str):
        from app.services.concept_backend_config_service import get_adapter_class

        cls = get_adapter_class(concept)
        if cls:
            return cls(concept)
        return None

    @property
    def _base_url(self) -> str:
        return settings.MES_API_BASE_URL.rstrip("/") if settings.MES_API_BASE_URL else ""

    @property
    def _available(self) -> bool:
        return bool(self._base_url)

    async def _get_client(self):
        url = self._base_url
        if url not in self._clients:
            import httpx
            headers = {}
            if settings.MES_API_TOKEN:
                headers["Authorization"] = f"Bearer {settings.MES_API_TOKEN}"
            self._clients[url] = httpx.AsyncClient(
                base_url=url, headers=headers, timeout=10.0,
            )
        return self._clients[url]

    async def _call(self, concept: str, action: str, data: dict) -> dict:
        """通过适配器构建请求 → 调用 API → 解析响应。"""
        import time as _time
        t0 = _time.time()
        adapter = self._get_adapter(concept)
        if not adapter:
            return {"error": f"概念 '{concept}' 未注册适配器"}
        req = adapter.build_request(action, data)
        client = await self._get_client()
        url = f"{self._base_url}{req['path']}"
        try:
            if req["method"].upper() == "GET":
                params = dict(req.get("body", {}))
                if settings.MES_PLANT_CODE and "plantCode" not in params:
                    params["plantCode"] = settings.MES_PLANT_CODE
                resp = await client.get(req["path"], params=params)
            elif req.get("params") is not None:
                resp = await client.post(req["path"], params=req["params"], json={})
            else:
                resp = await client.post(req["path"], json=req["body"])
            elapsed = int((_time.time() - t0) * 1000)
            import json as _json
            resp_json = {}
            try:
                resp_json = resp.json()
            except Exception:
                pass
            # 记录 API 日志（含请求体和响应体）
            from app.services.multi_system_backend import _request_user_id, _request_conversation_id, _request_message, _try_insert_api_log
            _try_insert_api_log(
                user_id=_request_user_id.get() or "",
                conversation_id=_request_conversation_id.get() or "",
                message=(_request_message.get() or "")[:200],
                concept=concept, method=req["method"].upper(), url=url,
                status=resp.status_code, elapsed_ms=elapsed,
                request_body=_json.dumps(req.get("body", {}), ensure_ascii=False)[:2000],
                response_body=_json.dumps(resp_json, ensure_ascii=False)[:2000],
            )
            if resp.status_code in (200, 201):
                parsed = adapter.parse_response(action, resp_json)
                return {**resp_json, "_parsed": parsed}
            return {"error": f"HTTP {resp.status_code}: {resp.text}"}
        except Exception as e:
            elapsed = int((_time.time() - t0) * 1000)
            import json as _json
            from app.services.multi_system_backend import _try_insert_api_log
            _try_insert_api_log(
                concept=concept, method=req["method"].upper(), url=url,
                status=0, elapsed_ms=elapsed, error=str(e),
                request_body=_json.dumps(req.get("body", {}), ensure_ascii=False)[:2000],
            )
            raise

    async def resolve_entity(
        self, concept: str, keyword: str,
    ) -> Optional[dict]:
        if not self._available:
            return None
        result = await self._call(concept, "query", {"keyword": keyword})
        if "error" in result:
            return None
        items = result if isinstance(result, list) else result.get("items", [])
        return items[0] if items else None

    async def query(
        self,
        concept: str,
        filters: Dict[str, Any],
        relations: Optional[List[str]] = None,
    ) -> List[dict]:
        if not self._available:
            return []
        raw = {k: v for k, v in filters.items() if v and not k.startswith("_")}
        result = await self._call(concept, "query", raw)
        if "error" in result:
            return []
        return self._extract_items(result)

    @staticmethod
    def _extract_items(data: dict) -> list:
        """从多种 API 响应格式中提取记录列表。"""
        # 直接是列表
        if isinstance(data, list):
            return data
        # ThreeApi 格式: {success: true, data: {rows: [...], total: N}}
        if isinstance(data.get("data"), dict) and "rows" in data["data"]:
            return data["data"]["rows"]
        # data 字段直接是列表
        if isinstance(data.get("data"), list):
            return data["data"]
        # 明确返回 items
        if "items" in data:
            return data["items"]
        return []

    async def create(
        self, concept: str, data: Dict[str, Any],
    ) -> dict:
        if not self._available:
            return {"error": "api 不可用"}
        return await self._call(concept, "create", dict(data))

    async def health(self) -> dict:
        ok = self._available
        return {"ok": ok, "backend": "api", "base_url": self._base_url or None}


# ── 降级链 ────────────────────────────────────────────────────

class FallbackDataBackend(DataBackend):
    """本体驱动的多后端编排器。

    路由由本体定义自动决定，无需手动 dataSource 标记：
      _needs_neo4j  → DataFilter / 跨概念关系 / 规则 → 数据必须入图
      _has_adapter  → 概念注册了 MES 适配器 → 可通过 API 读写

    查写分离：
      query/resolve → _needs_neo4j ? Neo4jBackend : ApiBackend
      create → _has_adapter ? ApiBackend → Neo4j 同步 : Neo4jBackend
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

    def _has_adapter(self, concept_name: str) -> bool:
        """概念是否注册了 MES 适配器。"""
        from app.services.concept_backend_config_service import get_adapter_class
        return get_adapter_class(concept_name) is not None

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
        if self._needs_neo4j(concept):
            result = await self._try_backend(self._neo4j, "query", concept, filters, relations)
            return result if result is not None else []
        result = await self._try_backend(self._api, "query", concept, filters, relations)
        return result if result is not None else []

    async def create(
        self, concept: str, data: Dict[str, Any],
    ) -> dict:
        if self._has_adapter(concept):
            result = await self._try_backend(self._api, "create", concept, data)
            if result and not result.get("error") and self._needs_neo4j(concept):
                await self._cache_results(concept, [result])
            return result if result is not None else {"error": "api 创建失败"}
        result = await self._try_backend(self._neo4j, "create", concept, data)
        return result if result is not None else {"error": "neo4j 创建失败"}

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


# 单例
data_backend = FallbackDataBackend()
