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
        self._client_configs: dict[str, dict] = {}  # per-system client config

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
        """从 compiler_systems.yaml 加载系统定义。"""
        systems = {}
        try:
            import os, yaml
            path = os.path.join(
                os.path.dirname(__file__), "..", "..",
                "config", "compiler_systems.yaml",
            )
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    config = yaml.safe_load(f) or {}
                for sys_name, cfg in config.get("systems", {}).items():
                    endpoints = cfg.get("endpoints", [])
                    # 也兼容旧格式: concepts 列表转为简单 endpoint
                    for cn in (cfg.get("concepts") or []):
                        if not any(e.get("concept") == cn for e in endpoints):
                            endpoints.append({"concept": cn, "method": "GET", "path": "", "params": [], "response": {"fields": []}})
                    systems[sys_name] = {
                        "name": sys_name,
                        "type": cfg.get("type", "api"),
                        "baseUrl": cfg.get("baseUrl", ""),
                        "authType": cfg.get("authType", "bearer"),
                        "authConfig": cfg.get("authConfig", {}),
                        "endpoints": endpoints,
                    }
        except Exception as e:
            logger.warning(f"[MultiSystemBackend] 加载 compiler_systems.yaml 失败: {e}")

        # 始终包含 Neo4j
        systems["neo4j"] = {
            "name": "neo4j",
            "type": "neo4j",
            "connectionString": "bolt://localhost:7687",
        }

        self._systems = {n: SystemConfig(d) for n, d in systems.items()}
        # 从配置直接构建概念→系统映射
        self._build_concept_system_map()

    def _build_concept_system_map(self):
        """从 compiler_systems.yaml 构建概念→系统映射。"""
        import os, yaml
        try:
            path = os.path.join(
                os.path.dirname(__file__), "..", "..",
                "config", "compiler_systems.yaml",
            )
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    config = yaml.safe_load(f) or {}
                for sys_name, cfg in config.get("systems", {}).items():
                    for cn in (cfg.get("concepts") or []):
                        self._concept_system[cn] = sys_name
        except Exception:
            pass

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
        """通过 API 查询概念数据, 使用端点的参数和响应映射。"""
        client = await self._get_client(system)
        ep = self._resolve_endpoint(concept, system)
        path = ep.get("path", f"/api/{concept.lower()}")
        method = ep.get("method", "GET").upper()
        fmt = ep.get("format", "json")
        mapped_params = self._build_request_params(params, ep)

        try:
            if method == "POST":
                if fmt == "form":
                    response = await client.post(path, data=mapped_params)
                else:
                    response = await client.post(path, json=mapped_params)
            elif method == "PUT":
                if fmt == "form":
                    response = await client.put(path, data=mapped_params)
                else:
                    response = await client.put(path, json=mapped_params)
            else:
                response = await client.get(path, params=mapped_params)
            response.raise_for_status()
            data = response.json()

            parsed = self._parse_response(data, ep)
            items = parsed.get("items", [])
            count = parsed.get("count", len(items))

            if items:
                lines = [f"找到 {count} 条记录："]
                for r in items[:20]:
                    parts = []
                    for k, v in r.items():
                        if v is not None:
                            parts.append(f"{k}={v}")
                    lines.append("  " + " | ".join(parts))
                return "\n".join(lines)
            return f"未找到匹配的记录。"
        except httpx.HTTPError as e:
            logger.warning(f"[MultiSystemBackend] API 查询失败 {concept}: {e}")
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

    def _resolve_endpoint(self, concept: str, system: SystemConfig) -> dict:
        """解析概念对应的 API 端点配置 (path, method, params, response)。"""
        for ep in system.endpoints:
            if ep.get("concept") == concept:
                return ep
        return {"path": f"/api/{concept.lower()}", "method": "GET", "params": [], "response": {"fields": []}}

    def _build_request_params(self, params: dict, endpoint: dict) -> dict:
        """根据端点配置将本体参数映射为 API 请求参数。"""
        param_configs = endpoint.get("params", [])
        if not param_configs:
            return params  # 无配置则原样传递

        result = {}
        for pc in param_configs:
            ont_name = pc.get("name", "")
            api_name = pc.get("apiName", ont_name)
            if ont_name in params and params[ont_name]:
                result[api_name] = params[ont_name]
        return result

    def _parse_response(self, data, endpoint: dict) -> dict:
        """根据端点配置解析 API 响应, 映射回本体属性名, 处理成功/失败判断。"""
        resp_cfg = endpoint.get("response", {})
        root = resp_cfg.get("root", "")
        fields = resp_cfg.get("fields", [])
        resp_type = resp_cfg.get("type", "array")
        success_type = resp_cfg.get("successType", "http")
        success_field = resp_cfg.get("successField", "")
        success_value = resp_cfg.get("successValue", "")
        error_field = resp_cfg.get("errorField", "")
        total_field = resp_cfg.get("totalField", "")

        # 错误信息提取
        error_msg = ""
        if isinstance(data, dict) and error_field and error_field in data:
            error_msg = str(data[error_field])

        # 成功判断: HTTP 2xx 或字段匹配
        if success_type == "field" and success_field and isinstance(data, dict):
            val = str(data.get(success_field, ""))
            if success_value and val != str(success_value):
                return {"items": [], "count": 0, "error": error_msg or f"{success_field}={val} (期望{success_value})"}

        # 提取根路径下的数据
        if root and isinstance(data, dict):
            data = data.get(root, data)

        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = [data]
        else:
            items = []

        # 总数提取
        total = len(items)
        if total_field and isinstance(data, dict) and total_field in data:
            total = int(data[total_field])

        # 字段映射
        mapped = []
        if items:
            for item in items[:200]:  # 最多200条
                if isinstance(item, dict):
                    if fields:
                        m = {}
                        for f in fields:
                            api_name = f.get("apiName", "")
                            ont_name = f.get("name", api_name)
                            if api_name in item:
                                m[ont_name] = item[api_name]
                        mapped.append(m)
                    else:
                        mapped.append(item)

        return {"items": mapped, "count": total, "error": error_msg}

    def _resolve_env_vars(self, value: str) -> str:
        """解析 ${VAR_NAME} 格式的环境变量。"""
        import os, re
        if not value or "${" not in value:
            return value
        def _replace(m):
            var_name = m.group(1)
            return os.environ.get(var_name, m.group(0))
        return re.sub(r'\$\{(\w+)\}', _replace, value)

    async def _get_client(self, system: SystemConfig) -> httpx.AsyncClient:
        """获取或创建 API 客户端 (按 baseUrl 缓存)。"""
        key = system.base_url or system.name
        if key not in self._api_clients:
            headers = {}
            if system.auth_type == "bearer":
                token = self._resolve_env_vars(system.auth_config.get("token", ""))
                if token:
                    headers["Authorization"] = f"Bearer {token}"
            elif system.auth_type == "apikey":
                header_name = system.auth_config.get("header", "X-API-Key")
                api_key = self._resolve_env_vars(system.auth_config.get("key", ""))
                if api_key:
                    headers[header_name] = api_key

            timeout = system.auth_config.get("timeout", 30)
            retries = system.auth_config.get("retries", 1)

            client = httpx.AsyncClient(
                base_url=system.base_url, headers=headers,
                timeout=httpx.Timeout(timeout),
            )
            self._api_clients[key] = client
            self._client_configs[key] = {"timeout": timeout, "retries": retries}
            logger.info(f"[MultiSystemBackend] 客户端创建: {key} timeout={timeout}s retries={retries}")

        return self._api_clients[key]

    def invalidate_client(self, system_name: str):
        """使客户端缓存失效 (配置变更后调用)。"""
        keys = [k for k in self._api_clients if k == self._systems.get(system_name, SystemConfig({})).base_url or k == system_name]
        for k in keys:
            if k in self._api_clients:
                import asyncio as _asyncio
                try: _asyncio.create_task(self._api_clients[k].aclose())
                except: pass
                del self._api_clients[k]
                del self._client_configs[k]

    async def test_connection(self, system_name: str) -> dict:
        """测试系统连接 — 返回 {ok, status, message, elapsed_ms}。"""
        import time
        system = self._systems.get(system_name)
        if not system:
            return {"ok": False, "message": f"系统 '{system_name}' 不存在"}
        if not system.is_api:
            return {"ok": False, "message": "非 API 类型系统，无需测试"}

        client = await self._get_client(system)
        t0 = time.time()
        try:
            # 尝试访问根路径或健康检查
            resp = await client.get("/", follow_redirects=True)
            elapsed = int((time.time() - t0) * 1000)
            logger.info(f"[MultiSystemBackend] 连接测试 {system_name}: {resp.status_code} ({elapsed}ms)")
            self._log_request("GET", f"{system.base_url}/", resp.status_code, elapsed, None)
            return {"ok": resp.is_success, "status": resp.status_code,
                    "message": f"HTTP {resp.status_code}", "elapsed_ms": elapsed}
        except Exception as e:
            elapsed = int((time.time() - t0) * 1000)
            logger.warning(f"[MultiSystemBackend] 连接测试 {system_name} 失败: {e}")
            self._log_request("GET", f"{system.base_url}/", 0, elapsed, str(e))
            return {"ok": False, "message": str(e), "elapsed_ms": elapsed}

    def _log_request(self, method: str, url: str, status: int, elapsed_ms: int, error: str = None):
        """记录 API 请求日志。"""
        if error:
            logger.error(f"[API] {method} {url} → ERR({elapsed_ms}ms): {error}")
        else:
            logger.info(f"[API] {method} {url} → {status} ({elapsed_ms}ms)")

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
