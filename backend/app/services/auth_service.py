"""认证服务 — 基于角色的访问控制（RBAC），含角色层级继承。

通过 Neo4j 查询用户→角色映射和角色层级关系。
"""

from app.core.logger import log


class AuthService:
    """检查用户是否拥有 Action 或 Rule 所需的角色权限。

    同时管理登录会话（token→user_id 内存映射），供 get_current_user_id() 使用。
    测试阶段使用内存字典；生产环境由父应用认证接管。
    """

    def __init__(self):
        self._role_hierarchy: dict[str, set[str]] | None = None
        self._sessions: dict[str, str] = {}  # token → user_id

    async def _build_hierarchy(self) -> dict[str, set[str]]:
        """从 Neo4j Role 数据节点构建 角色→祖先集合 映射。

        push_data 创建的 Role 节点包含属性: id、name、parentRole、description。
        返回 {角色名: set(所有祖先名_含自身)}。
        """
        from app.services.neo4j_service import neo4j_service

        if not neo4j_service.connected:
            log.warning("[Auth] Neo4j 未连接，无法构建角色层级")
            return {}

        try:
            records = await neo4j_service.execute_read(
                "MATCH (r:Role) WHERE r.parentRole IS NOT NULL "
                "RETURN r.name AS name, r.parentRole AS parent"
            )
        except Exception as e:
            log.warning(f"[Auth] Neo4j 角色层级查询失败: {e}")
            return {}

        parent_map: dict[str, str] = {}
        for r in records:
            name = r.get("name", "")
            parent = r.get("parent", "")
            if name and parent:
                parent_map[name] = parent

        if not parent_map:
            log.warning("[Auth] 角色层级未配置（Neo4j 中无 parentRole 值）")
            return {}

        # parentRole = 组织架构上级（汇报对象）。
        # RBAC: 上级角色继承所有下级角色权限（传递闭包）。
        # 为每个角色构建"下级闭包"。
        all_roles = set(parent_map.keys()) | set(parent_map.values())

        # 每个角色 → 其直接下级
        subordinates: dict[str, set[str]] = {r: set() for r in all_roles}
        for role, superior in parent_map.items():
            if superior in subordinates:
                subordinates[superior].add(role)

        # 传递闭包: 每个角色加入其下级的下级...
        def get_all_subordinates(role: str, visited: set[str]) -> set[str]:
            if role in visited:
                return set()
            visited.add(role)
            result: set[str] = set()
            for sub in subordinates.get(role, set()):
                result.add(sub)
                result.update(get_all_subordinates(sub, visited))
            return result

        hierarchy: dict[str, set[str]] = {}
        for role in all_roles:
            hierarchy[role] = {role} | get_all_subordinates(role, set())

        log.info(f"[Auth] 角色层级已构建: {len(hierarchy)} 个角色（来源 Neo4j）")
        return hierarchy

    async def _get_hierarchy(self) -> dict[str, set[str]]:
        if self._role_hierarchy is None:
            self._role_hierarchy = await self._build_hierarchy()
        return self._role_hierarchy

    # 测试阶段：MES 账号 → 角色硬编码映射
    # 生产环境由 MES HRIS API 动态查询替代
    _TEST_USER_ROLES: dict[str, str] = {
        "admin": "管理员",
        "EMP-001": "操作工",
        "EMP-010": "车间主任",
    }

    async def get_effective_roles(self, user_id: str) -> set[str]:
        """返回用户的所有有效角色（直接角色 + 继承角色）。"""
        direct_roles = await self._get_direct_roles_from_neo4j(user_id)

        if not direct_roles:
            # 回退 1: 测试阶段硬编码映射
            test_role = self._TEST_USER_ROLES.get(user_id)
            if test_role:
                direct_roles = {test_role}
            else:
                # 回退 2: 尝试本体元数据
                direct_roles = self._get_direct_roles_from_ontology(user_id)

        hierarchy = await self._get_hierarchy()

        effective: set[str] = set()
        for role in direct_roles:
            effective.update(hierarchy.get(role, {role}))

        return effective

    def _get_direct_roles_from_ontology(self, user_id: str) -> set[str]:
        """从本体个体获取用户直接角色（同步回退方案）。

        匹配优先级:
          1. 个体 name == user_id
          2. 个体 id 属性值 == user_id（MES UserAccount = 工号）
        """
        from app.services.ontology_service import ontology_service

        concepts = ontology_service.get_concepts()
        emp_concept = next((c for c in concepts if c["name"] == "Employee"), None)
        if not emp_concept:
            return set()

        def _get_role(ind: dict) -> str | None:
            for pv in ind.get("values", []):
                if pv.get("propertyName") == "role":
                    return pv.get("value", "")
            return None

        for ind in emp_concept.get("individuals", []):
            # 匹配个体名称
            if ind.get("name") == user_id:
                role = _get_role(ind)
                if role:
                    return {role}
            # 匹配工号（id 属性值）
            for pv in ind.get("values", []):
                if pv.get("propertyName") == "id" and pv.get("value") == user_id:
                    role = _get_role(ind)
                    if role:
                        return {role}
        return set()

    async def _get_direct_roles_from_neo4j(self, user_id: str) -> set[str]:
        """从 Neo4j 业务数据获取用户直接角色。"""
        from app.services.neo4j_service import neo4j_service

        if not neo4j_service.connected:
            return set()

        try:
            records = await neo4j_service.execute_read(
                "MATCH (e:Employee {id: $id})-[:担任角色]->(r:Role) RETURN r.name AS name",
                {"id": user_id},
            )
            return {r["name"] for r in records}
        except Exception as e:
            log.warning(f"[Auth] Neo4j 角色查询失败: {e}")
            return set()

    async def check(self, user_id: str, required_roles: list[str]) -> bool:
        """检查用户是否拥有任一所需角色（含继承）。

        Args:
            user_id: 员工个体名称（如 'emp_001'）
            required_roles: 所需的角色标签列表（如 ['操作工', '班长']）

        Returns:
            用户有效角色与所需角色有交集时返回 True。
            required_roles 为空时返回 True（无限制）。
        """
        if not required_roles:
            return True

        effective = await self.get_effective_roles(user_id)
        if not effective:
            return False

        allowed = bool(effective & set(required_roles))
        if not allowed:
            log.info(
                f"[Auth] 拒绝: user={user_id}, "
                f"effective={effective}, required={required_roles}"
            )
        return allowed

    # ── 会话管理（测试阶段） ──

    def register_session(self, token: str, user_id: str, user_info: dict = None) -> None:
        """建立 token → 用户信息映射（登录成功后调用）。"""
        if token:
            self._sessions[token] = {
                "user_id": user_id,
                "properties": user_info or {},
            }
            log.info(f"[Auth] 会话已注册: {user_id}")

    def resolve_user(self, token: str) -> str | None:
        """根据 Bearer token 解析 user_id，未找到返回 None。"""
        session = self._sessions.get(token)
        if session:
            return session["user_id"] if isinstance(session, dict) else session

        # 解析 JWT（子应用模式：token 即 __SYSTEM_Data_AccessToken）
        # 密钥从 settings 读取；verify_exp 默认 True（token 过期则拒绝，防伪造/重放）
        try:
            # 兼容 JSON 字符串存储的 token（BP 端 localStorage 写入时带引号）
            token = token.strip()
            if len(token) >= 2 and token[0] == '"' and token[-1] == '"':
                token = token[1:-1]
            import jwt as _jwt
            from app.core.config import settings as _settings
            _secret = _settings.JWT_SECRET
            data = _jwt.decode(token, _secret,
                               algorithms=[_settings.JWT_ALGORITHM or 'HS256'],
                               options={'verify_aud': False})
            user_id = data.get('EmpCode', '') or data.get('LoginUserName', '').split('\\')[-1]
            if user_id:
                self.register_session(token, user_id, data)
                return user_id
        except Exception:
            pass
        return None

    async def get_user_property(self, user_id: str, property_name: str):
        """优先从会话属性获取，回退到 Neo4j Employee 节点。"""
        # 从会话查找
        for token, session in self._sessions.items():
            uid = session["user_id"] if isinstance(session, dict) else session
            if uid == user_id and isinstance(session, dict):
                val = session.get("properties", {}).get(property_name)
                if val is not None:
                    return val
                val = session.get("properties", {}).get("NowPlantCode")
                if val is not None and property_name in ("plantCode", "NowPlantCode"):
                    return val
        # 回退 Neo4j
        from app.services.neo4j_service import neo4j_service
        if not neo4j_service.connected or not user_id or not property_name:
            return None
        try:
            records = await neo4j_service.execute_read(
                "MATCH (e:Employee {id: $id}) RETURN e[$prop] AS val",
                {"id": user_id, "prop": property_name},
            )
            if records:
                return records[0].get("val")
        except Exception as e:
            log.warning(f"[Auth] 获取用户属性失败 {user_id}.{property_name}: {e}")
        return None

    def clear_session(self, token: str) -> None:
        """清除 token 对应的会话（退出登录）。"""
        user_id = self._sessions.pop(token, None)
        if user_id:
            log.info(f"[Auth] 会话已清除: {user_id}")

    # ── 角色层级 ──

    def reset(self):
        """清除缓存的层级数据和会话（本体重新加载后调用）。"""
        self._role_hierarchy = None
        self._sessions.clear()


auth_service = AuthService()
