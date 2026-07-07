"""多系统 DataBackend — 按概念→系统配置路由, 支持多 API + Neo4j。

概念查询时:
  1. 查概念的 system 映射
  2. 查系统配置 (type, baseUrl, auth)
  3. 路由到对应后端 (API / Neo4j / DB)
  4. API 不可用时降级 Neo4j 缓存
"""

import json
from typing import Any, Optional

import httpx
from loguru import logger


class SystemConfig:
    """系统配置 — 从 Neo4j 本体元数据或 YAML 加载。"""

    def __init__(self, data: dict):
        self.name: str = data.get("name", "")
        self.type: str = data.get("type", "neo4j")  # api / neo4j / mssql / ...
        self.base_url: str = data.get("baseUrl", "")
        self.auth_type: str = data.get("authType", "bearer")
        self.auth_config: dict = data.get("authConfig", {})
        self.endpoints: list[dict] = data.get("endpoints", [])
        self.connection_string: str = data.get("connectionString", "")

    @property
    def is_api(self) -> bool:
        return self.type == "api"

    @property
    def is_neo4j(self) -> bool:
        return self.type == "neo4j"


class MultiSystemBackend:
    """多系统数据后端 — 按概念路由到正确的系统。

    用法:
        backend = MultiSystemBackend()
        await backend.load_configs()
        result = await backend.query("WorkOrder", {"code": "MO001"})
    """

    def __init__(self):
        self._systems: dict[str, SystemConfig] = {}
        self._concept_system: dict[str, str] = {}  # 概念名 → 系统名
        self._api_clients: dict[str, httpx.AsyncClient] = {}

    # ── 配置加载 ─────────────────────────────────────────

    async def load_configs(self, systems_data: list[dict] = None):
        """加载系统配置。可从 Neo4j 项目元数据或 compiler_domains 读取。"""
        if systems_data:
            self._systems = {
                s["name"]: SystemConfig(s) for s in systems_data
            }
        else:
            # 从本体服务加载
            await self._load_from_ontology()

        # 构建概念→系统映射
        self._build_concept_system_map()

        logger.info(
            f"[MultiSystemBackend] 已加载 {len(self._systems)} 个系统: "
            f"{list(self._systems.keys())}"
        )

    async def _load_from_ontology(self):
        """从 Neo4j 本体加载系统定义。"""
        try:
            from app.services.ontology_service import ontology_service
            concepts = ontology_service.get_concepts() or []
        except Exception:
            concepts = []

        systems = {}
        for c in concepts:
            for prop in c.get("properties", []):
                for m in prop.get("mappings", []):
                    sys_name = m.get("system", "")
                    if sys_name and sys_name not in systems:
                        systems[sys_name] = {
                            "name": sys_name,
                            "type": "api",
                            "baseUrl": "",
                            "authType": "bearer",
                            "authConfig": {},
                        }

        # 始终包含 Neo4j
        systems["neo4j"] = {
            "name": "neo4j",
            "type": "neo4j",
            "connectionString": "bolt://localhost:7687",
        }

        self._systems = {n: SystemConfig(d) for n, d in systems.items()}

    def _build_concept_system_map(self):
        """从编译器产出或 ontology service 构建概念→系统映射。"""
        try:
            from app.services.ontology_service import ontology_service
            concepts = ontology_service.get_concepts() or []
        except Exception:
            concepts = []

        for c in concepts:
            for prop in c.get("properties", []):
                for m in prop.get("mappings", []):
                    sys_name = m.get("system", "")
                    if sys_name and sys_name in self._systems:
                        self._concept_system[c["name"]] = sys_name
                        break
                if c["name"] in self._concept_system:
                    break

    # ── 公共接口 ───────────────────────────────────────────

    async def query(self, concept: str, params: dict) -> str:
        """查询概念数据, 自动路由到对应系统。"""
        system = self._resolve_system(concept)

        if system.is_api:
            return await self._query_api(concept, params, system)
        else:
            return await self._query_neo4j(concept, params)

    async def create(self, concept: str, data: dict) -> dict:
        """写入概念数据, 走 API → 源系统 + 异步回写 Neo4j。"""
        system = self._resolve_system(concept)

        if system.is_api:
            result = await self._create_api(concept, data, system)
            # 异步回写到 Neo4j 缓存
            try:
                await self._cache_to_neo4j(concept, data)
            except Exception as e:
                logger.warning(f"[MultiSystemBackend] 回写 Neo4j 缓存失败: {e}")
            return result
        else:
            return await self._create_neo4j(concept, data)

    # ── 路由 ───────────────────────────────────────────────

    def _resolve_system(self, concept: str) -> SystemConfig:
        """解析概念对应的系统配置。"""
        sys_name = self._concept_system.get(concept, "neo4j")
        return self._systems.get(sys_name) or self._systems.get("neo4j")

    def _needs_neo4j(self, concept: str) -> bool:
        """概念是否必须走 Neo4j (有关系/规则需要图遍历)。"""
        try:
            from app.services.ontology_service import ontology_service
            c = ontology_service.get_concept(concept)
            if c:
                has_relations = bool(c.get("relations", []))
                has_rules = bool(c.get("rules", []))
                return has_relations or has_rules
        except Exception:
            pass
        return False

    # ── API 查询 ───────────────────────────────────────────

    async def _query_api(
        self, concept: str, params: dict, system: SystemConfig
    ) -> str:
        """通过 API 查询概念数据。"""
        client = await self._get_client(system)
        endpoint = self._resolve_endpoint(concept, system)

        try:
            response = await client.get(endpoint, params=params)
            response.raise_for_status()
            data = response.json()

            if isinstance(data, list):
                lines = [f"找到 {len(data)} 条记录："]
                for r in data[:20]:
                    parts = []
                    for k, v in (r.items() if isinstance(r, dict) else enumerate(r)):
                        if v is not None:
                            parts.append(f"{k}={v}")
                    lines.append("  " + " | ".join(parts))
                return "\n".join(lines)
            elif isinstance(data, dict):
                return json.dumps(data, ensure_ascii=False, indent=2)
            return str(data)
        except httpx.HTTPError as e:
            logger.warning(f"[MultiSystemBackend] API 查询失败 {concept}: {e}")
            # 降级 Neo4j
            return await self._query_neo4j(concept, params)

    async def _create_api(
        self, concept: str, data: dict, system: SystemConfig
    ) -> dict:
        """通过 API 创建记录。"""
        client = await self._get_client(system)
        endpoint = self._resolve_endpoint(concept, system)

        try:
            response = await client.post(endpoint, json=data)
            response.raise_for_status()
            return {"success": True, "data": response.json()}
        except httpx.HTTPError as e:
            logger.error(f"[MultiSystemBackend] API 创建失败 {concept}: {e}")
            return {"success": False, "error": str(e)}

    def _resolve_endpoint(self, concept: str, system: SystemConfig) -> str:
        """解析概念对应的 API 端点。"""
        # 1. 检查手动配置的端点
        for ep in system.endpoints:
            if ep.get("concept") == concept:
                return ep.get("path", f"/api/{concept.lower()}")

        # 2. 默认推导
        return f"/api/{concept.lower()}"

    async def _get_client(self, system: SystemConfig) -> httpx.AsyncClient:
        """获取或创建 API 客户端 (按 baseUrl 缓存)。"""
        key = system.base_url
        if key not in self._api_clients:
            headers = {}
            if system.auth_type == "bearer":
                token = system.auth_config.get("token", "")
                if token:
                    headers["Authorization"] = f"Bearer {token}"
            elif system.auth_type == "apikey":
                header_name = system.auth_config.get("header", "X-API-Key")
                api_key = system.auth_config.get("key", "")
                if api_key:
                    headers[header_name] = api_key

            self._api_clients[key] = httpx.AsyncClient(
                base_url=system.base_url,
                headers=headers,
                timeout=30.0,
            )
        return self._api_clients[key]

    # ── Neo4j 查询 ──────────────────────────────────────────

    async def _query_neo4j(self, concept: str, params: dict) -> str:
        """通过 Neo4j 查询 (走现有 ActionExecutor)。"""
        try:
            from app.services.action_executor import action_executor
            tool_name = f"{concept}_query"
            sig = action_executor._sigs.get(tool_name)
            if sig:
                return await action_executor._execute_query(sig, params)
        except Exception as e:
            logger.error(f"[MultiSystemBackend] Neo4j 查询失败 {concept}: {e}")
        return "未找到匹配的记录。"

    async def _create_neo4j(self, concept: str, data: dict) -> dict:
        """直接写入 Neo4j。"""
        try:
            from app.services.neo4j_service import neo4j_service
            if neo4j_service.connected:
                props = ", ".join(
                    f"n.{k} = ${k}" for k in data if not k.startswith("_")
                )
                if props:
                    cypher = f"MERGE (n:{concept} {{id: $id}}) SET {props}"
                    await neo4j_service.execute_write(cypher, {"id": data.get("id", ""), **data})
                return {"success": True}
        except Exception as e:
            logger.error(f"[MultiSystemBackend] Neo4j 创建失败: {e}")
        return {"success": False, "error": "Neo4j 写入失败"}

    async def _cache_to_neo4j(self, concept: str, data: dict):
        """异步回写 Neo4j 缓存 (API 写入成功后)。"""
        await self._create_neo4j(concept, data)

    # ── 生命周期 ───────────────────────────────────────────

    async def close(self):
        """关闭所有 API 客户端。"""
        for client in self._api_clients.values():
            await client.aclose()
        self._api_clients.clear()


# 全局单例
multi_system_backend = MultiSystemBackend()
