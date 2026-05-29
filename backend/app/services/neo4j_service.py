"""Neo4j Service — unified graph database access for ontology metadata.

Uses async driver to avoid blocking the FastAPI event loop.
This service manages the connection lifecycle only — business data
access goes through DataBackend (data_backend.py).
"""

from typing import Any, Optional

from neo4j import AsyncGraphDatabase, AsyncManagedTransaction
from neo4j.exceptions import Neo4jError, ServiceUnavailable

from app.core.config import settings
from app.core.logger import log


class Neo4jService:
    """Async Neo4j connection pool + query execution."""

    def __init__(self):
        self._driver = None
        self._connected: bool = False

    @property
    def connected(self) -> bool:
        return self._connected and self._driver is not None

    async def connect(self) -> bool:
        if not settings.NEO4J_ENABLED:
            log.info("[Neo4j] disabled by config (NEO4J_ENABLED=False)")
            return False
        try:
            self._driver = AsyncGraphDatabase.driver(
                settings.NEO4J_URI,
                auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
                max_connection_lifetime=settings.NEO4J_MAX_CONNECTION_LIFETIME,
                max_connection_pool_size=settings.NEO4J_MAX_CONNECTION_POOL_SIZE,
                connection_timeout=settings.NEO4J_CONNECTION_TIMEOUT,
            )
            async with self._driver.session(database=settings.NEO4J_DATABASE) as session:
                result = await session.run("RETURN 1 AS ok")
                await result.consume()
            self._connected = True
            log.info(f"[Neo4j] connected to {settings.NEO4J_URI}")
            return True
        except ServiceUnavailable as e:
            log.warning(f"[Neo4j] service unavailable at {settings.NEO4J_URI}: {e}")
            return False
        except Exception as e:
            log.error(f"[Neo4j] connection failed: {e}")
            return False

    async def disconnect(self) -> None:
        if self._driver:
            await self._driver.close()
            self._connected = False
            log.info("[Neo4j] disconnected")

    async def execute_read(self, cypher: str, params: dict = None) -> list[dict]:
        """Execute a read-only Cypher query. Returns list of record dicts."""
        if not self.connected:
            return []
        try:
            async with self._driver.session(database=settings.NEO4J_DATABASE) as session:
                result = await session.run(cypher, params or {})
                records = await result.data()
                await result.consume()
            return records
        except Neo4jError as e:
            log.error(f"[Neo4j] query error: {e}\n  CYPHER: {cypher[:200]}")
            return []

    async def execute_write(self, cypher: str, params: dict = None) -> list[dict]:
        """Execute a write Cypher query."""
        if not self.connected:
            return []
        try:
            async with self._driver.session(database=settings.NEO4J_DATABASE) as session:
                result = await session.run(cypher, params or {})
                records = await result.data()
                await result.consume()
            return records
        except Neo4jError as e:
            log.error(f"[Neo4j] write error: {e}\n  CYPHER: {cypher[:200]}")
            return []

    async def execute_read_tx(
        self, cypher: str, params: dict = None,
    ) -> list[dict]:
        """Execute a read query in a managed read transaction."""
        if not self.connected:
            return []
        try:
            async with self._driver.session(database=settings.NEO4J_DATABASE) as session:
                records = await session.execute_read(
                    lambda tx: self._run_and_collect(tx, cypher, params),
                )
            return records
        except Neo4jError as e:
            log.error(f"[Neo4j] read tx error: {e}")
            return []

    @staticmethod
    async def _run_and_collect(
        tx: AsyncManagedTransaction, cypher: str, params: dict = None,
    ) -> list[dict]:
        result = await tx.run(cypher, params or {})
        return await result.data()

    async def health(self) -> dict:
        if not self.connected:
            return {"connected": False, "uri": settings.NEO4J_URI, "error": "not connected"}
        try:
            records = await self.execute_read("RETURN 1 AS ok")
            return {"connected": True, "uri": settings.NEO4J_URI, "ok": bool(records)}
        except Exception as e:
            return {"connected": False, "uri": settings.NEO4J_URI, "error": str(e)}


neo4j_service = Neo4jService()
