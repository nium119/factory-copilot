"""Neo4j 服务 — 统一图数据库访问，用于本体和业务数据查询。

使用异步驱动，避免阻塞 FastAPI 事件循环。
本服务仅管理连接生命周期 — 业务数据访问通过 DataBackend（data_backend.py）。
"""

from typing import Any, Optional

from neo4j import AsyncGraphDatabase, AsyncManagedTransaction
from neo4j.exceptions import Neo4jError, ServiceUnavailable

from app.core.config import settings
from app.core.logger import log


class Neo4jService:
    """异步 Neo4j 连接池 + 查询执行。"""

    def __init__(self):
        self._driver = None
        self._connected: bool = False

    @property
    def connected(self) -> bool:
        return self._connected and self._driver is not None

    async def connect(self) -> bool:
        if not settings.NEO4J_ENABLED:
            log.info("[Neo4j] 配置已禁用 (NEO4J_ENABLED=False)")
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
            log.info(f"[Neo4j] 已连接到 {settings.NEO4J_URI}")
            return True
        except ServiceUnavailable as e:
            log.warning(f"[Neo4j] 服务不可用 {settings.NEO4J_URI}: {e}")
            return False
        except Exception as e:
            log.error(f"[Neo4j] 连接失败: {e}")
            return False

    async def disconnect(self) -> None:
        if self._driver:
            try:
                await self._driver.close()
            except Exception:
                pass
            self._driver = None
            self._connected = False
            log.info("[Neo4j] 已断开连接")

    async def execute_read(self, cypher: str, params: dict = None) -> list[dict]:
        """执行只读 Cypher 查询。返回记录字典列表。"""
        if not self.connected:
            log.warning("[Neo4j] 未连接，返回空")
            return []
        try:
            async with self._driver.session(database=settings.NEO4J_DATABASE) as session:
                result = await session.run(cypher, params or {})
                records = await result.data()
                await result.consume()
                if not records:
                    log.warning(f"[Neo4j] 查询返回0行 cypher_len={len(cypher)} params={params}")
            return records
        except Neo4jError as e:
            log.error(f"[Neo4j] 查询错误: {e}\n  CYPHER: {cypher[:200]}")
            return []

    async def execute_write(self, cypher: str, params: dict = None) -> list[dict]:
        """执行写 Cypher 查询。"""
        if not self.connected:
            return []
        try:
            async with self._driver.session(database=settings.NEO4J_DATABASE) as session:
                result = await session.run(cypher, params or {})
                records = await result.data()
                await result.consume()
            return records
        except Neo4jError as e:
            log.error(f"[Neo4j] 写入错误: {e}\n  CYPHER: {cypher[:200]}")
            return []

    async def execute_read_tx(
        self, cypher: str, params: dict = None,
    ) -> list[dict]:
        """在读事务中执行查询。"""
        if not self.connected:
            return []
        try:
            async with self._driver.session(database=settings.NEO4J_DATABASE) as session:
                records = await session.execute_read(
                    lambda tx: self._run_and_collect(tx, cypher, params),
                )
            return records
        except Neo4jError as e:
            log.error(f"[Neo4j] 读事务错误: {e}")
            return []

    @staticmethod
    async def _run_and_collect(
        tx: AsyncManagedTransaction, cypher: str, params: dict = None,
    ) -> list[dict]:
        result = await tx.run(cypher, params or {})
        return await result.data()

    # ── Cypher 安全检查 ──
    FORBIDDEN_KEYWORDS = [
        "DELETE", "SET", "CREATE", "MERGE", "DETACH", "REMOVE", "DROP", "CALL",
    ]

    @staticmethod
    def validate_readonly(cypher: str):
        """校验 Cypher 查询是否为只读。

        返回 (is_valid, error_message)。
        仅允许 MATCH / RETURN / WHERE / ORDER BY / LIMIT / SKIP / WITH。
        """
        import re
        upper = cypher.upper().strip()
        if not upper.startswith("MATCH"):
            return False, "只允许 MATCH 开头的只读查询"
        for kw in Neo4jService.FORBIDDEN_KEYWORDS:
            if re.search(r'\b' + kw + r'\b', upper):
                return False, f"禁止使用 {kw}（只允许只读操作）"
        return True, ""

    async def health(self) -> dict:
        if not self.connected:
            return {"connected": False, "uri": settings.NEO4J_URI, "error": "未连接"}
        try:
            records = await self.execute_read("RETURN 1 AS ok")
            return {"connected": True, "uri": settings.NEO4J_URI, "ok": bool(records)}
        except Exception as e:
            return {"connected": False, "uri": settings.NEO4J_URI, "error": str(e)}

    async def ensure_unique_constraint(self, label: str, property_name: str = "id") -> None:
        """为 `label.property_name` 创建唯一约束（如果不存在）。"""
        constraint_name = f"unique_{label}_{property_name}"
        cypher = (
            f"CREATE CONSTRAINT {constraint_name} IF NOT EXISTS "
            f"FOR (n:{label}) REQUIRE n.{property_name} IS UNIQUE"
        )
        try:
            await self.execute_write(cypher)
        except Exception as e:
            if "IndexAlreadyExists" in str(e) or "index already exists" in str(e).lower():
                # 已有索引，尝试先删除再创建约束
                try:
                    await self.execute_write(f"DROP INDEX idx_{label}_{property_name} IF EXISTS")
                    await self.execute_write(cypher)
                except Exception:
                    pass  # 索引和约束冲突时放弃
            else:
                pass  # 其他错误静默忽略
            try:
                await self.execute_write(legacy)
            except Exception as e:
                log.warning(f"[Neo4j] 约束创建已跳过: {e}")

    async def next_sequence(self, label: str) -> int:
        """原子递增并返回 `label` 的序列值。

        使用 :Sequence 节点 — Neo4j 对同一节点的写入是串行的，
        因此并发调用方始终获得不同的值。
        """
        records = await self.execute_write(
            "MERGE (s:Sequence {name: $label}) "
            "ON CREATE SET s.value = 1 "
            "ON MATCH SET s.value = s.value + 1 "
            "RETURN s.value AS value",
            {"label": label},
        )
        return records[0]["value"] if records else 1


neo4j_service = Neo4jService()
