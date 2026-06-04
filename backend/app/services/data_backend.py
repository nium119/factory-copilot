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
        ns_clause, ns_params = self._ns_where()
        ns_where = f" AND {ns_clause}" if ns_clause else ""
        label = concept
        # 先精确匹配 id
        records = await self._execute(
            f"MATCH (n:{label}) WHERE n.id = $kw{ns_where} RETURN n LIMIT 1",
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

        cross_match = ""
        if cross_entity and cross_id:
            cross_label = cross_entity
            # 从本体解析关系标签，实现精确关联遍历
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
        adapter = self._get_adapter(concept)
        if not adapter:
            return {"error": f"概念 '{concept}' 未注册适配器"}
        req = adapter.build_request(action, data)
        client = await self._get_client()
        if req["method"].upper() == "GET":
            resp = await client.get(req["path"], params=req["body"])
        else:
            resp = await client.post(req["path"], json=req["body"])
        if resp.status_code in (200, 201):
            parsed = adapter.parse_response(action, resp.json())
            return {**resp.json(), "_parsed": parsed}
        return {"error": f"HTTP {resp.status_code}: {resp.text}"}

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
        data = result
        return data if isinstance(data, list) else data.get("items", [])

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
    """多后端降级链，按优先级依次尝试。

    优先级顺序（从配置读取）:
      DATA_BACKEND=neo4j  → Neo4jBackend → ApiBackend
      DATA_BACKEND=api    → ApiBackend 仅用
    """

    def __init__(self):
        self._backends: List[DataBackend] = []
        self._primary: Optional[DataBackend] = None

    async def initialize(self) -> None:
        """按配置优先级加载后端。"""
        backend_name = settings.DATA_BACKEND.lower()
        neo4j = Neo4jBackend()
        api = ApiBackend()

        if backend_name == "neo4j":
            self._backends = [neo4j, api]
            self._primary = neo4j
        elif backend_name == "api":
            self._backends = [api]
            self._primary = api
        else:
            self._backends = [neo4j]
            self._primary = neo4j

        # 记录各后端可用状态
        for b in self._backends:
            h = await b.health()
            log.info(f"[DataBackend] {h['backend']}: {'可用' if h['ok'] else '不可用'}")

    async def _try(self, method: str, *args, **kwargs):
        """在降级链上依次尝试方法调用。

        返回首个可用后端的执行结果。空结果（None、[]、{}）直接返回 —
        仅在不可用或异常时才会降级到下一个后端。
        """
        for backend in self._backends:
            h = await backend.health()
            if not h.get("ok"):
                continue
            try:
                fn = getattr(backend, method)
                result = await fn(*args, **kwargs)
                return result
            except Exception as e:
                log.warning(f"[DataBackend] {backend.__class__.__name__}.{method} 失败: {e}")
                continue
        return None if method == "resolve_entity" else [] if method == "query" else {}

    async def resolve_entity(
        self, concept: str, keyword: str,
    ) -> Optional[dict]:
        return await self._try("resolve_entity", concept, keyword)

    async def query(
        self,
        concept: str,
        filters: Dict[str, Any],
        relations: Optional[List[str]] = None,
    ) -> List[dict]:
        return await self._try("query", concept, filters, relations)

    async def create(
        self, concept: str, data: Dict[str, Any],
    ) -> dict:
        return await self._try("create", concept, data)

    async def health(self) -> dict:
        backends = {}
        all_ok = False
        for b in self._backends:
            h = await b.health()
            backends[h["backend"]] = h
            if h["ok"]:
                all_ok = True
        return {"ok": all_ok, "primary": settings.DATA_BACKEND, "backends": backends}


# 单例
data_backend = FallbackDataBackend()
