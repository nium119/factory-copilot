"""多系统 DataBackend — 按概念→系统配置路由, 支持多 API + Neo4j。

概念查询时:
  1. 查概念的 system 映射
  2. 查系统配置 (type, baseUrl, auth)
  3. 路由到对应后端 (API / Neo4j / DB)
  4. API 不可用时降级 Neo4j 缓存

API 调用日志通过 contextvars 关联到用户和会话上下文。
"""

import asyncio
import time
import base64
import json as _json
from contextvars import ContextVar

# 请求上下文 — 由 Agent 在每次消息处理时设置
_request_user_id: ContextVar[str] = ContextVar("request_user_id", default="")
_request_conversation_id: ContextVar[str] = ContextVar("request_conversation_id", default="")
_request_message: ContextVar[str] = ContextVar("request_message", default="")
_request_token: ContextVar[str] = ContextVar("request_token", default="")
# JWT claims — 由 _parse_jwt_token 解析后缓存
_request_claims: ContextVar[dict] = ContextVar("request_claims", default={})
import json
from typing import Any, Optional

import httpx
from loguru import logger

# JWT 配置（与 C# 端一致）
_JWT_ISSUER = "JYInfo"
_JWT_AUDIENCE = "JYInfo"
_JWT_KEY = br"#s\opiakdn83oaxce#s\opiakdn83oaxce"


def _parse_jwt_claims(token: str) -> dict:
    """解析 JWT token（验签 + 过期），提取用户属性。

    修复：此前只 base64 解码 payload 不验签，伪造 token 也能提取 claims。
    现在用 settings.JWT_SECRET 验签（verify_exp 默认 True），非法/过期返回空。
    """
    try:
        import jwt as _jwt
        from app.core.config import settings
        parts = token.split(".")
        if len(parts) != 3:
            return {}
        data = _jwt.decode(token, settings.JWT_SECRET,
                           algorithms=[settings.JWT_ALGORITHM or 'HS256'],
                           options={'verify_aud': False})
        return dict(data)
    except Exception:
        return {}


def get_session_value(key: str) -> str:
    """获取当前请求的会话参数值。

    优先从 JWT claims 取，其次从 _request_claims 取。
    """
    claims = _request_claims.get()
    if claims:
        # JWT claims 常见字段映射
        claim_map = {
            "userId": ["sub", "userId", "nameid", "user_id"],
            "empCode": ["empCode", "emp_code", "employee_code"],
            "plantCode": ["plantCode", "plant_code", "NowPlantCode"],
            "userName": ["name", "userName", "unique_name", "user_name"],
            "workStationCode": ["workStationCode", "workstation_code"],
        }
        candidates = claim_map.get(key, [key])
        for c in candidates:
            if c in claims and claims[c]:
                return str(claims[c])
    return ""


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
        self.fallback_on_error: bool = data.get("fallbackOnError", False)

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

    async def load_configs(self, systems_data: list[dict] = None, force: bool = False):
        """加载系统配置。

        force=True: 忽略 _applied 标记（用于测试编辑中的配置）
        force=False: 仅加载 _applied=true 的配置（运行时使用）
        """
        if systems_data:
            self._systems = {
                s["name"]: SystemConfig(s) for s in systems_data
            }
        else:
            await self._load_from_ontology(force=force)

        # 构建概念→系统映射
        self._build_concept_system_map()

        logger.info(
            f"[MultiSystemBackend] 已加载 {len(self._systems)} 个系统: "
            f"{list(self._systems.keys())}"
        )

    async def _load_from_ontology(self, force: bool = False):
        """从 DB 加载当前 namespace 的系统定义（ORM版本）。

        force=True: 忽略 _applied 标记（测试用）
        force=False: 仅加载 _applied=true 的配置（运行时用）
        """
        systems = {}
        try:
            from app.db import get_db
            async for session in get_db():
                from app.repositories.namespace_config_repo import NamespaceConfigRepository
                repo = NamespaceConfigRepository(session)
                ns = self._get_active_ns()
                config = await repo.get(ns, "systems")
                if config and (force or config.get("_applied", True)):
                    for sys_name, cfg in config.get("systems", {}).items():
                        endpoints = cfg.get("endpoints", [])
                        for cn in (cfg.get("concepts") or []):
                            if not any(e.get("concept") == cn for e in endpoints):
                                endpoints.append({"concept": cn, "method": "GET", "path": "", "params": [], "response": {"fields": []}})
                        systems[sys_name] = {
                            "name": sys_name, "type": cfg.get("type", "api"),
                            "baseUrl": cfg.get("baseUrl", ""), "authType": cfg.get("authType", "bearer"),
                            "authConfig": cfg.get("authConfig", {}), "endpoints": endpoints,
                            "fallbackOnError": cfg.get("fallbackOnError", True),
                        }
        except Exception as e:
            logger.warning(f"[MultiSystemBackend] DB加载失败: {e}")

        systems["neo4j"] = {"name": "neo4j", "type": "neo4j", "connectionString": "bolt://localhost:7687"}
        self._systems = {n: SystemConfig(d) for n, d in systems.items()}
        self._build_concept_system_map()
        logger.info(
            f"[MultiSystemBackend] 已加载 {len(self._systems)} 个系统: "
            f"{list(self._systems.keys())}, 概念→系统映射: {dict(self._concept_system)}"
        )

    @staticmethod
    def _get_active_ns() -> str:
        try:
            import os
            path = os.path.join(os.path.dirname(__file__), "..", "..", "config", "active_namespace.txt")
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f: return f.read().strip()
        except Exception: pass
        return "manufacturing"

    def _build_concept_system_map(self):
        """从已加载的 _systems 构建概念→系统映射（尊重 _load_from_ontology 的过滤）。"""
        self._concept_system.clear()
        for sys_name, sys_cfg in self._systems.items():
            if not sys_cfg.is_api:
                continue
            for ep in sys_cfg.endpoints:
                if ep.get("concept") and ep.get("enabled", True):
                    self._concept_system[ep["concept"]] = sys_name

    # ── 公共接口 ───────────────────────────────────────────

    async def query(self, concept: str, params: dict) -> str:
        """查询概念数据, 自动路由到对应系统。"""
        system = self._resolve_system(concept)

        if system.is_api:
            text, _ = await self._query_api(concept, params, system)
            return text
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
    ) -> tuple[str, list]:
        """通过 API 查询概念数据, 返回 (文本摘要, 结构化items)。"""
        client = await self._get_client(system)
        ep = self._resolve_endpoint(concept, system)
        path = ep.get("path", f"/api/{concept.lower()}")
        method = ep.get("method", "GET").upper()
        fmt = ep.get("format", "json")
        mapped_params = self._build_request_params(params, ep)

        # 重试逻辑（auth_config 值可能是空字符串，需转为 int）
        def _int_or(v, default=1):
            try: return int(v)
            except (ValueError, TypeError): return default
        retries = _int_or(system.auth_config.get("retries", 1), 1)
        timeout = _int_or(system.auth_config.get("timeout", 30), 30)
        last_error = None
        t_start = time.time()
        for attempt in range(max(1, retries)):
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
                break  # 成功则跳出重试循环
            except (httpx.HTTPError, httpx.TimeoutException) as e:
                last_error = e
                if attempt < retries - 1:
                    logger.warning(f"[MultiSystemBackend] {concept} 重试 {attempt+1}/{retries}: {e}")
                    await asyncio.sleep(0.5 * (attempt + 1))  # 递增退避
        else:
            # 所有重试都失败 — 明确报错, 不静默降级
            logger.error(f"[MultiSystemBackend] API 查询失败 {concept}: {last_error} (已重试{retries}次)")
            _try_insert_api_log(
                user_id=_request_user_id.get() or "",
                conversation_id=_request_conversation_id.get() or "",
                message=(_request_message.get() or "")[:200],
                concept=concept, method=method, url=f"{system.base_url}{path}",
                status=0, elapsed_ms=0, error=str(last_error),
                request_body=(mapped_params and str(mapped_params)[:2000]) or "",
            )
            return f"❌ {concept} 查询失败：无法连接到 {system.base_url}，请检查数据源配置或网络连接。", []

        # 请求成功 — 解析响应
        elapsed = int((time.time() - t_start) * 1000)
        raw_text = ""
        try:
            data = response.json()
            raw_text = str(data)[:4000]
        except Exception:
            raw_text = response.text[:4000] if response.text else ""
            data = {"_raw": raw_text}
        _try_insert_api_log(
            user_id=_request_user_id.get() or "",
            conversation_id=_request_conversation_id.get() or "",
            message=(_request_message.get() or "")[:200],
            concept=concept, method=method, url=f"{system.base_url}{path}",
            status=response.status_code, elapsed_ms=elapsed,
            request_body=(mapped_params and str(mapped_params)[:2000]) or "",
            response_body=raw_text,
        )
        parsed = self._parse_response(data, ep)
        items = parsed.get("items", [])
        count = parsed.get("count", len(items))

        if items:
            lines = [f"找到 {count} 条记录：", ""]
            keys = list(items[0].keys()) if items else []
            lines.append("| " + " | ".join(keys) + " |")
            lines.append("|" + "|".join(["---" for _ in keys]) + "|")
            for r in items[:20]:
                parts = [str(r.get(k, "-")) if r.get(k) is not None else "-" for k in keys]
                lines.append("| " + " | ".join(parts) + " |")
            return "\n".join(lines), items
        return f"未找到匹配的记录。", []

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
        """根据端点配置将本体参数映射为 API 请求参数, 含分页排序。"""
        param_configs = endpoint.get("params", [])
        result = {}

        # 字段级映射
        for pc in param_configs:
            ont_name = pc.get("name", "")
            api_name = pc.get("apiName", ont_name)
            source = pc.get("source", "user")
            if source == "system":
                # 系统参数: 从环境变量取
                sys_key = pc.get("systemKey", "")
                val = getattr(settings, sys_key, "") if sys_key else pc.get("defaultValue", "")
                if val:
                    result[api_name] = val
            elif source == "session":
                # 会话参数: 从 JWT claims 取（token 已解析到 _request_claims）
                val = get_session_value(ont_name)
                if val:
                    result[api_name] = val
            else:
                # 用户参数: 从 Agent 提取的参数中取
                if ont_name in params and params[ont_name]:
                    result[api_name] = params[ont_name]
                elif pc.get("defaultValue"):
                    result[api_name] = pc.get("defaultValue")

        # 通用分页参数
        page_param = endpoint.get("pageParam", "")
        size_param = endpoint.get("sizeParam", "")
        if page_param:
            result[page_param] = params.get("_page", 1)
        if size_param:
            result[size_param] = params.get("_size", 50)

        # 排序参数
        sort_param = endpoint.get("sortParam", "")
        order_param = endpoint.get("orderParam", "")
        if sort_param and params.get("_sort"):
            result[sort_param] = params["_sort"]
        if order_param and params.get("_order"):
            result[order_param] = params["_order"]
        if sort_param and "_sort" in params:
            result[sort_param] = params["_sort"]
            if order_param:
                result[order_param] = params.get("_order", "desc")

        if not param_configs and not result:
            return params  # 无配置则原样传递
        return result

    def _parse_response(self, data, endpoint: dict) -> dict:
        """根据端点配置解析 API 响应, 映射回本体属性名, 处理成功/失败判断。

        支持:
        - JSON/XML 切换
        - 嵌套根路径 (如 result.data.items)
        - 字段级映射
        """
        import xml.etree.ElementTree as ET
        resp_cfg = endpoint.get("response", {})
        root = resp_cfg.get("root", "")
        fields = resp_cfg.get("fields", [])
        resp_format = resp_cfg.get("format", "json")
        conditions = resp_cfg.get("successConditions", [{"type": "http", "field": "status", "operator": "eq", "value": "200"}])
        error_field = resp_cfg.get("errorField", "")
        total_field = resp_cfg.get("totalField", "")

        # XML 解析
        if resp_format == "xml" and isinstance(data, str):
            try:
                root_elem = ET.fromstring(data)
                data = self._xml_to_dict(root_elem)
            except Exception:
                return {"items": [], "count": 0, "error": "XML 解析失败"}

        # 错误信息提取
        error_msg = ""
        if isinstance(data, dict) and error_field:
            error_msg = str(data.get(error_field, ""))

        # 成功判断: 多条件 AND
        if isinstance(data, dict):
            for cond in conditions:
                if cond.get("type") == "http":
                    continue  # HTTP 状态码由 raise_for_status 保证
                field_val = str(data.get(cond.get("field", ""), ""))
                op = cond.get("operator", "eq")
                expected = str(cond.get("value", ""))
                if op == "exists":
                    if not field_val:
                        return {"items": [], "count": 0, "error": error_msg or f"字段 {cond['field']} 为空"}
                elif op == "eq" and expected and field_val != expected:
                    return {"items": [], "count": 0, "error": error_msg or f"{cond['field']}={field_val} (期望{expected})"}
                elif op == "gte" and expected:
                    try:
                        if float(field_val) < float(expected):
                            return {"items": [], "count": 0, "error": error_msg or f"{cond['field']}={field_val} < {expected}"}
                    except ValueError:
                        return {"items": [], "count": 0, "error": error_msg or f"{cond['field']} 非数字"}
                elif op == "lte" and expected:
                    try:
                        if float(field_val) > float(expected):
                            return {"items": [], "count": 0, "error": error_msg or f"{cond['field']}={field_val} > {expected}"}
                    except ValueError:
                        return {"items": [], "count": 0, "error": error_msg or f"{cond['field']} 非数字"}

        # 提取嵌套根路径 (支持点号分隔, 如 result.data.items)
        if root and isinstance(data, dict):
            for part in root.split("."):
                if isinstance(data, dict) and part in data:
                    data = data[part]
                else:
                    break

        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = [data]
        else:
            items = []

        # 总数提取
        total = len(items)
        if total_field and isinstance(data, dict) and total_field in data:
            try:
                total = int(data[total_field])
            except (ValueError, TypeError):
                pass

        # 字段映射
        mapped = []
        for item in items[:200]:
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

    @staticmethod
    def _xml_to_dict(element) -> dict:
        """简单的 XML → dict 转换。"""
        import xml.etree.ElementTree as ET
        result = {}
        for child in element:
            if len(child) > 0:
                child_data = MultiSystemBackend._xml_to_dict(child)
                tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                if tag in result:
                    if not isinstance(result[tag], list):
                        result[tag] = [result[tag]]
                    result[tag].append(child_data)
                else:
                    result[tag] = child_data
            else:
                tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                result[tag] = child.text
        return result

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
                # 优先用静态配置的 token（环境变量），否则透传请求的 Bearer token
                token = self._resolve_env_vars(system.auth_config.get("token", ""))
                if not token:
                    # 从当前请求上下文获取（前端 interceptor 已设置）
                    import contextvars
                    req_token = _request_token.get()
                    if req_token:
                        token = req_token
                if token:
                    headers["Authorization"] = f"Bearer {token}"
            elif system.auth_type == "basic":
                import base64
                username = self._resolve_env_vars(system.auth_config.get("username", ""))
                password = self._resolve_env_vars(system.auth_config.get("password", ""))
                if username and password:
                    creds = base64.b64encode(f"{username}:{password}".encode()).decode()
                    headers["Authorization"] = f"Basic {creds}"
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
            # 任何 HTTP 响应都说明连接成功（404 只是没有根路径，网络是通的）
            if resp.is_success:
                msg = f"连接成功 HTTP {resp.status_code}"
            else:
                msg = f"已连通 HTTP {resp.status_code}（服务器无根路径，网络正常）"
            return {"ok": True, "status": resp.status_code,
                    "message": msg, "elapsed_ms": elapsed}
        except Exception as e:
            elapsed = int((time.time() - t0) * 1000)
            logger.warning(f"[MultiSystemBackend] 连接测试 {system_name} 失败: {e}")
            self._log_request("GET", f"{system.base_url}/", 0, elapsed, str(e))
            msg = str(e)
            if "connection" in msg.lower() or "connect" in msg.lower():
                msg = f"无法连接到 {system.base_url}，请检查地址和网络"
            elif "timeout" in msg.lower():
                msg = f"连接 {system.base_url} 超时"
            return {"ok": False, "message": msg, "elapsed_ms": elapsed}

    def _log_request(self, method: str, url: str, status: int, elapsed_ms: int, error: str = None, concept: str = ""):
        """记录 API 请求日志到 DB，含用户和会话上下文。"""
        uid = _request_user_id.get() or ""
        cid = _request_conversation_id.get() or ""
        msg = _request_message.get() or ""
        ctx = f"user={uid} conv={cid} concept={concept}"
        if error:
            logger.error(f"[API] {method} {url} → ERR({elapsed_ms}ms) | {ctx} | {error}")
        else:
            logger.info(f"[API] {method} {url} → {status} ({elapsed_ms}ms) | {ctx}")
        # 写入结构化日志到 DB
        _try_insert_api_log(
            user_id=uid, conversation_id=cid, message=msg[:200] if msg else "",
            concept=concept, method=method, url=url[:500],
            status=status, elapsed_ms=elapsed_ms, error=error[:500] if error else "",
        )

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


def _try_insert_api_log(user_id="", conversation_id="", message="", concept="",
                        method="", url="", status=0, elapsed_ms=0, error="",
                        request_body="", response_body=""):
    """写入 API 调用日志到 agent.db。"""
    import asyncio, datetime
    # 组装 context
    context_parts = []
    if method and url:
        context_parts.append(f"{method} {url}")
    if request_body:
        try:
            rb = _json.loads(request_body) if isinstance(request_body, str) else request_body
            context_parts.append(f"> 请求: {_json.dumps(rb, ensure_ascii=False, indent=2)}")
        except Exception:
            context_parts.append(f"> 请求: {request_body}")
    if response_body:
        try:
            rb = _json.loads(response_body) if isinstance(response_body, str) else response_body
            context_parts.append(f"< 响应: {_json.dumps(rb, ensure_ascii=False, indent=2)}")
        except Exception:
            context_parts.append(f"< 响应: {response_body}")
    if error:
        context_parts.append(f"!! 错误: {error}")
    context = "\n".join(context_parts)
    async def _insert():
        from app.db import get_db
        async for session in get_db():
            from app.repositories.api_log_repo import ApiLogRepository
            repo = ApiLogRepository(session)
            await repo.insert(
                user_id=user_id, conversation_id=conversation_id, message=message,
                concept=concept, method=method, url=url, status=status,
                elapsed_ms=elapsed_ms, error=error, request_body=request_body or "",
                response_body=response_body or "", context=context,
            )
    from app.db import run_async
    try:
        run_async(_insert())
    except Exception:
        import asyncio as _asyncio
        try:
            loop = _asyncio.get_running_loop()
            loop.create_task(_insert())
        except RuntimeError:
            pass
    except Exception:
        pass


# 全局单例
multi_system_backend = MultiSystemBackend()
