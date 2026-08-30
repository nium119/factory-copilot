"""Neo4j 服务 — 统一图数据库访问，用于本体和业务数据查询。

使用异步驱动，避免阻塞 FastAPI 事件循环。
本服务仅管理连接生命周期 — 业务数据访问通过 DataBackend（data_backend.py）。
"""

import asyncio
from contextlib import asynccontextmanager
from typing import Any, Optional

from neo4j import AsyncGraphDatabase, AsyncManagedTransaction
from neo4j.exceptions import Neo4jError, ServiceUnavailable

from app.core.config import settings
from app.core.logger import log


@asynccontextmanager
async def _null_async_ctx():
    """空异步上下文管理器：无事件循环（无法取读锁）时用作 async with 的无锁占位。"""
    yield


async def _get_sys_cfg(key: str) -> str:
    """从 DB agent_system_configs 读取配置，不存在返回空字符串。

    用同步 sqlite3 直接读（settings.DB_PATH）：原实现走 SQLAlchemy AsyncEngine，
    在 pytest/独立脚本的独立事件循环下会跨循环挂起、导致 connect() 卡死。
    同步读单个 key 极快，且仅在连接/少量配置读取时调用，阻塞影响可忽略。
    """
    try:
        import sqlite3
        from app.core.config import settings as _cfg
        con = sqlite3.connect(_cfg.DB_PATH, timeout=1.0)
        try:
            row = con.execute(
                "SELECT value FROM agent_system_configs WHERE key = ?", (key,)
            ).fetchone()
            return (row[0] or "") if row else ""
        finally:
            con.close()
    except Exception:
        return ""


class Neo4jService:
    """异步 Neo4j 连接池 + 查询执行。"""

    def __init__(self):
        self._driver = None
        self._connected: bool = False
        self._uri: str = settings.NEO4J_URI
        self._database: str = "neo4j"
        self._read_lock = None  # 读锁惰性创建（绑定当前事件循环）

    @property
    def connected(self) -> bool:
        return self._connected and self._driver is not None

    async def connect(self) -> bool:
        # 读 DB 覆盖配置：一次性 + 超时。pytest/独立脚本的 SQLAlchemy engine 绑定在别的
        # 事件循环时，逐次 await 会跨循环挂起；这里用 wait_for 兜底，超时后全部落回 settings 凭据。
        _dyn_cfg = {}
        try:
            async def _read_cfg():
                return {
                    "neo4j_enabled": await _get_sys_cfg("neo4j_enabled"),
                    "uri": await _get_sys_cfg("neo4j_uri"),
                    "user": await _get_sys_cfg("neo4j_user"),
                    "password": await _get_sys_cfg("neo4j_password"),
                    "database": await _get_sys_cfg("neo4j_database"),
                }
            _dyn_cfg = await asyncio.wait_for(_read_cfg(), timeout=2.0)
        except (asyncio.TimeoutError, Exception):
            _dyn_cfg = {}

        # 优先从 DB 读取启用开关，fallback 到 .env
        enabled_str = _dyn_cfg.get("neo4j_enabled") or ""
        if enabled_str == "false":
            log.info("[Neo4j] 前端已禁用 (neo4j_enabled=false)")
            return False
        if enabled_str != "true" and not settings.NEO4J_ENABLED:
            log.info("[Neo4j] 配置已禁用 (NEO4J_ENABLED=False)")
            return False

        # 优先从 DB 读取连接配置，fallback 到 .env
        uri = _dyn_cfg.get("uri") or settings.NEO4J_URI
        user = _dyn_cfg.get("user") or settings.NEO4J_USER
        password = _dyn_cfg.get("password") or settings.NEO4J_PASSWORD
        database = _dyn_cfg.get("database") or settings.NEO4J_DATABASE

        try:
            self._driver = AsyncGraphDatabase.driver(
                uri,
                auth=(user, password),
                max_connection_lifetime=settings.NEO4J_MAX_CONNECTION_LIFETIME,
                max_connection_pool_size=settings.NEO4J_MAX_CONNECTION_POOL_SIZE,
                connection_timeout=settings.NEO4J_CONNECTION_TIMEOUT,
            )
            async with self._driver.session(database=database) as session:
                result = await session.run("RETURN 1 AS ok")
                await result.consume()
            self._uri = uri
            self._database = database
            self._connected = True
            log.info(f"[Neo4j] 已连接到 {self._uri}")
            return True
        except ServiceUnavailable as e:
            log.warning(f"[Neo4j] 服务不可用 {uri}: {e}")
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

    async def execute_read(self, cypher: str, params: dict = None, retries: int = 7) -> list[dict]:
        """执行只读 Cypher 查询。返回记录字典列表。加 READ 模式避免写锁冲突。"""
        import asyncio
        if not self.connected:
            log.warning("[Neo4j] 未连接，返回空")
            return []
        # 读锁绑定当前事件循环：pytest-asyncio 每个测试可能用不同循环，
        # 沿用旧 asyncio.Lock 会跨循环挂起，这里检测循环变化就重建（惰性）。
        _lk = None
        try:
            _cur = asyncio.get_running_loop()
            if getattr(self._read_lock, "_loop", None) is not _cur:
                self._read_lock = asyncio.Lock()
            _lk = self._read_lock
        except RuntimeError:
            _lk = None
        for attempt in range(retries):
            try:
                async with (_lk if _lk is not None else _null_async_ctx()):
                    async with self._driver.session(database=self._database, default_access_mode="READ") as session:
                        result = await session.run(cypher, params or {})
                        records = await result.data()
                        await result.consume()
                    if not records:
                        log.warning(f"[Neo4j] 查询返回0行 cypher_len={len(cypher)} params={params}")
                return records
            except Neo4jError as e:
                msg = str(e)
                if 'locked' in msg.lower() and attempt < retries - 1:
                    delay = 1.0 * (2 ** attempt)  # 1s, 2s, 4s = 7s total
                    log.warning(f"[Neo4j] 读锁冲突, {delay:.0f}s 后重试 ({attempt+1}/{retries}): {msg[:100]}")
                    await asyncio.sleep(delay)
                else:
                    log.error(f"[Neo4j] 查询错误: {e}\n  CYPHER: {cypher[:200]}")
                    return []

    async def execute_write(self, cypher: str, params: dict = None) -> list[dict]:
        """执行写 Cypher 查询。"""
        if not self.connected:
            return []
        try:
            async with self._driver.session(database=self._database) as session:
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
            async with self._driver.session(database=self._database) as session:
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
            return {"connected": False, "uri": self._uri, "error": "未连接"}
        try:
            records = await self.execute_read("RETURN 1 AS ok")
            return {"connected": True, "uri": self._uri, "ok": bool(records)}
        except Exception as e:
            return {"connected": False, "uri": self._uri, "error": str(e)}

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
