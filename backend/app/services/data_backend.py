"""Data Backend — unified business data access abstraction.

Three backends, one interface:
  Neo4jBackend  — Cypher queries against the graph database
  SqliteBackend — SQL queries against mes_demo.db (existing logic)
  ApiBackend    — HTTP calls to MES/ERP REST APIs

The fallback chain runs: Neo4j → Api → Sqlite (first healthy backend wins).
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.core.logger import log


# ── Abstract interface ──────────────────────────────────────────────

class DataBackend(ABC):
    """Business data access abstraction.

    Three core operations cover all entity resolution and query needs:
      resolve_entity — single entity lookup by keyword
      query         — filtered list with optional relation traversal
      create        — insert a new entity
    """

    @abstractmethod
    async def resolve_entity(
        self, concept: str, keyword: str,
    ) -> Optional[dict]:
        """Look up a single entity by keyword (id or name).

        Returns the full entity dict, or None if not found.
        """

    @abstractmethod
    async def query(
        self,
        concept: str,
        filters: Dict[str, Any],
        relations: Optional[List[str]] = None,
    ) -> List[dict]:
        """Query entities with filters, optionally traversing relations.

        Args:
            concept: e.g. "WorkOrder", "Equipment"
            filters: {property_name: value, ...}
            relations: related concept names to include, e.g. ["Product", "QualityCheck"]
        """

    @abstractmethod
    async def create(
        self, concept: str, data: Dict[str, Any],
    ) -> dict:
        """Create a new entity. Returns the created entity dict."""

    @abstractmethod
    async def health(self) -> dict:
        """Health check — returns {"ok": true, "backend": "neo4j", ...}"""


# ── Neo4j Backend ────────────────────────────────────────────────────

class Neo4jBackend(DataBackend):
    """Cypher-based backend using the graph database."""

    # Concept → neo4j label mapping (same labels as Ontology-Graph push_schema creates)
    _LABELS: Dict[str, str] = {
        "WorkOrder": "WorkOrder", "Product": "Product",
        "QualityCheck": "QualityCheck", "Equipment": "Equipment",
        "Material": "Material", "Routing": "Routing",
        "WorkCenter": "WorkCenter", "Operation": "Operation",
        "ProductionLine": "ProductionLine", "WorkStation": "WorkStation",
        "Employee": "Employee",
    }

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

    async def resolve_entity(
        self, concept: str, keyword: str,
    ) -> Optional[dict]:
        if not self._available:
            return None
        label = self._LABELS.get(concept, concept)
        # Exact id match first
        records = await self._execute(
            f"MATCH (n:{label}) WHERE n.id = $kw RETURN n LIMIT 1",
            {"kw": keyword},
        )
        if records:
            return dict(records[0]["n"])
        # Fuzzy name match
        records = await self._execute(
            f"MATCH (n:{label}) WHERE n.name CONTAINS $kw RETURN n LIMIT 1",
            {"kw": keyword},
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
        label = self._LABELS.get(concept, concept)

        where_clauses = []
        params = {}
        cross_entity = filters.pop('_cross_concept', None)
        cross_id = filters.pop('_cross_entity', None)
        cross_name = filters.pop('_cross_entity_name', None)

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

        if cross_entity and cross_id and self._LABELS.get(cross_entity):
            # Prefer property-based FK filter (n.employeeId = "EMP-001")
            # over graph traversal — it's more precise and avoids
            # false positives from Cartesian-product relationships.
            fk_prop = self._infer_fk_prop(cross_entity)
            if fk_prop:
                pname = f"fk_{cross_entity}"
                if isinstance(cross_id, str):
                    where_clauses.append(f"n.{fk_prop} CONTAINS ${pname}")
                else:
                    where_clauses.append(f"n.{fk_prop} = ${pname}")
                params[pname] = cross_id
                cypher = f"MATCH (n:{label})"
            else:
                cross_label = self._LABELS.get(cross_entity, cross_entity)
                cypher = (
                    f"MATCH (e:{cross_label} {{id: $cross_id}})"
                    f"-[*1..2]-(n:{label})"
                )
                params["cross_id"] = cross_id
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
            return {"error": "neo4j unavailable"}
        label = self._LABELS.get(concept, concept)
        # Generate ID
        count_records = await self._execute(
            f"MATCH (n:{label}) RETURN count(n) AS cnt",
        )
        count = count_records[0]["cnt"] if count_records else 0
        prefix = self._id_prefix(concept)
        new_id = f"{prefix}-{count + 1:03d}"

        props = {**data, "id": new_id}
        set_clauses = ", ".join(f"n.{k} = ${k}" for k in props)
        params = {k: v for k, v in props.items()}
        await self._execute_write(
            f"CREATE (n:{label}) SET {set_clauses} RETURN n", params,
        )
        return props

    async def health(self) -> dict:
        ok = self._available
        return {
            "ok": ok, "backend": "neo4j",
            "uri": settings.NEO4J_URI if ok else None,
        }

    @staticmethod
    def _id_prefix(concept: str) -> str:
        prefixes = {
            "WorkOrder": "WO", "QualityCheck": "QC", "Equipment": "EQUIP",
            "Material": "MAT", "Product": "PROD", "Operation": "OP",
            "WorkCenter": "WC", "Routing": "ROUTE",
        }
        return prefixes.get(concept, concept[:4].upper())

    @staticmethod
    def _infer_fk_prop(concept: str) -> Optional[str]:
        """Infer the FK property name on related nodes.

        e.g., Employee → employeeId, WorkOrder → workOrderId.
        """
        return concept[0].lower() + concept[1:] + "Id"


# ── SQLite Backend ───────────────────────────────────────────────────

class SqliteBackend(DataBackend):
    """SQL-based backend wrapping the existing mes_demo.db.

    Delegates to ActionExecutor for SQL generation and execution.
    """

    _CONCEPT_TABLE: Dict[str, str] = {
        "WorkOrder": "work_orders", "Product": "products",
        "QualityCheck": "quality_checks", "Equipment": "equipment",
        "Material": "materials", "Routing": "routings",
        "WorkCenter": "work_centers", "Operation": "operations",
        "ProductionLine": "production_lines", "WorkStation": "work_stations",
        "Employee": "employees", "WorkReport": "work_reports",
    }

    async def resolve_entity(
        self, concept: str, keyword: str,
    ) -> Optional[dict]:
        try:
            from app.services.action_executor import action_executor
            import asyncio
            result = await asyncio.to_thread(
                action_executor.lookup_entity, concept, keyword,
            )
            return result
        except Exception as e:
            log.warning(f"[SqliteBackend] resolve_entity error: {e}")
            return None

    async def query(
        self,
        concept: str,
        filters: Dict[str, Any],
        relations: Optional[List[str]] = None,
    ) -> List[dict]:
        """Execute a query via ActionExecutor.

        Note: the current ActionExecutor SQL generation is tightly coupled
        to action signatures. This method provides basic single-table query
        support. For full ontology-driven queries, use execute_structured().
        """
        try:
            from app.services.action_executor import action_executor
            import asyncio

            table = self._CONCEPT_TABLE.get(concept, concept.lower())

            where_parts = []
            params = []
            for k, v in filters.items():
                if v is None or v == "":
                    continue
                if k.startswith('_'):  # skip synthetic params
                    continue
                if isinstance(v, str):
                    where_parts.append(f"{k} LIKE ?")
                    params.append(f"%{v}%")
                else:
                    where_parts.append(f"{k} = ?")
                    params.append(v)

            sql = f"SELECT * FROM {table}"
            if where_parts:
                sql += " WHERE " + " AND ".join(where_parts)

            rows = await asyncio.to_thread(action_executor._query, sql, params)
            cols = await asyncio.to_thread(
                action_executor._get_db_columns, table,
            )
            return [dict(zip(cols, row)) for row in rows] if rows else []
        except Exception as e:
            log.warning(f"[SqliteBackend] query error: {e}")
            return []

    async def create(
        self, concept: str, data: Dict[str, Any],
    ) -> dict:
        try:
            from app.services.action_executor import action_executor
            import asyncio

            table = self._CONCEPT_TABLE.get(concept, concept.lower())
            columns = list(data.keys())
            values = list(data.values())
            placeholders = ", ".join("?" for _ in values)
            await asyncio.to_thread(
                action_executor._execute,
                f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
                values,
            )
            return data
        except Exception as e:
            log.warning(f"[SqliteBackend] create error: {e}")
            return {"error": str(e)}

    async def health(self) -> dict:
        return {"ok": True, "backend": "sqlite"}


# ── API Backend ──────────────────────────────────────────────────────

class ApiBackend(DataBackend):
    """REST API backend for MES/ERP systems.

    Requires DATA_BACKEND=api and MES_API_BASE_URL to be configured.
    Each concept is mapped to an API endpoint pattern:
      resolve_entity → GET /api/{concept_lower}?search={keyword}
      query          → POST /api/{concept_lower}/search
      create         → POST /api/{concept_lower}
    """

    def __init__(self):
        self._client = None

    @property
    def _base_url(self) -> str:
        return settings.MES_API_BASE_URL.rstrip("/") if settings.MES_API_BASE_URL else ""

    @property
    def _available(self) -> bool:
        return bool(self._base_url)

    async def _get_client(self):
        if self._client is None:
            import httpx
            headers = {}
            if settings.MES_API_TOKEN:
                headers["Authorization"] = f"Bearer {settings.MES_API_TOKEN}"
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                headers=headers,
                timeout=10.0,
            )
        return self._client

    async def resolve_entity(
        self, concept: str, keyword: str,
    ) -> Optional[dict]:
        if not self._available:
            return None
        try:
            client = await self._get_client()
            path = f"/api/{concept.lower()}s"
            resp = await client.get(path, params={"search": keyword})
            if resp.status_code == 200:
                data = resp.json()
                items = data if isinstance(data, list) else data.get("items", [])
                return items[0] if items else None
        except Exception as e:
            log.warning(f"[ApiBackend] resolve_entity({concept}, {keyword}): {e}")
        return None

    async def query(
        self,
        concept: str,
        filters: Dict[str, Any],
        relations: Optional[List[str]] = None,
    ) -> List[dict]:
        if not self._available:
            return []
        try:
            client = await self._get_client()
            path = f"/api/{concept.lower()}s/search"
            body = {"filters": {k: v for k, v in filters.items() if v and not k.startswith("_")}}
            if relations:
                body["include"] = relations
            resp = await client.post(path, json=body)
            if resp.status_code == 200:
                data = resp.json()
                return data if isinstance(data, list) else data.get("items", [])
        except Exception as e:
            log.warning(f"[ApiBackend] query({concept}): {e}")
        return []

    async def create(
        self, concept: str, data: Dict[str, Any],
    ) -> dict:
        if not self._available:
            return {"error": "api unavailable"}
        try:
            client = await self._get_client()
            path = f"/api/{concept.lower()}s"
            resp = await client.post(path, json=data)
            if resp.status_code in (200, 201):
                return resp.json()
            return {"error": f"HTTP {resp.status_code}: {resp.text}"}
        except Exception as e:
            log.warning(f"[ApiBackend] create({concept}): {e}")
            return {"error": str(e)}

    async def health(self) -> dict:
        ok = self._available
        return {"ok": ok, "backend": "api", "base_url": self._base_url or None}


# ── Fallback Chain ────────────────────────────────────────────────────

class FallbackDataBackend(DataBackend):
    """Chains multiple backends, falling through on failure/unavailability.

    Priority order (from config or default):
      DATA_BACKEND=neo4j  → Neo4jBackend → SqliteBackend
      DATA_BACKEND=api    → ApiBackend   → SqliteBackend
      DATA_BACKEND=sqlite → SqliteBackend only
    """

    def __init__(self):
        self._backends: List[DataBackend] = []
        self._primary: Optional[DataBackend] = None

    async def initialize(self) -> None:
        """Load backends in priority order based on config."""
        backend_name = settings.DATA_BACKEND.lower()
        neo4j = Neo4jBackend()
        api = ApiBackend()
        sqlite = SqliteBackend()

        if backend_name == "neo4j":
            self._backends = [neo4j, api, sqlite]
            self._primary = neo4j
        elif backend_name == "api":
            self._backends = [api, sqlite]
            self._primary = api
        else:
            self._backends = [sqlite]
            self._primary = sqlite

        # Log which backends are available
        for b in self._backends:
            h = await b.health()
            log.info(f"[DataBackend] {h['backend']}: {'OK' if h['ok'] else 'UNAVAILABLE'}")

    async def _try(self, method: str, *args, **kwargs):
        """Try method on each backend in order until one succeeds.

        Returns result from the first healthy backend. Empty results (None, [], {})
        are returned directly — fallback only triggers on unavailability or exception.
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
                log.warning(f"[DataBackend] {backend.__class__.__name__}.{method} failed: {e}")
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
        return await self._try(
            "query", concept, filters, relations,
        )

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


# Singleton
data_backend = FallbackDataBackend()
