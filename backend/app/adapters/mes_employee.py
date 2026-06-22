"""Employee MES 适配器 — 员工信息映射到 MES HRIS API。

核心映射逻辑
═══════════════════════════════════════════════════════════════════════════
MES 人员信息来自两个渠道：

  HRIS — 人力资源系统
    - 员工基本信息（工号、姓名、岗位、班组）
    - API: GET /MESApi/HRIS/EmpList, GET /MESApi/HRIS/TeamGroupList

  工位执行 — 工位人员
    - 当前在工位上操作的员工
    - API: GET /MESApi/WorkOrderExecute/EmpList (workStationId)

本体 Employee 概念目前只有 query 操作，Agent 需要查员工基本信息即可。
═══════════════════════════════════════════════════════════════════════════
"""

from app.adapters.base import ConceptAdapter


class EmployeeMESAdapter(ConceptAdapter):
    """MES 员工适配器 — 员工语义到 MES HRIS API 的翻译。

    设计要点
    ────────
    1. 本体 id → MES empCode: 本体用 id 表示工号，MES HRIS 用 empCode
    2. 本体 name → MES empName: 两边一致
    3. 本体 role/skillLevel/shift — MES HRIS 不直接返回这些字段，
       通过岗位(JobName)和班组(TeamGroupName)间接关联
    """

    def __init__(self, concept_name: str):
        super().__init__(concept_name)

    # ── 字段映射 ────────────────────────────────────────────
    # 左侧 = 本体属性名（Employee 概念定义在 manufacturing.onto.yaml）
    # 右侧 = MES API 请求参数字段名
    #
    # 映射说明:
    #   id         → empCode       : 工号，本体用 id，MES 用 empCode
    #   name       → empName       : 姓名，MES 用 empName
    #   workshop   → workshop      : 车间，两边一致
    #   shift      → teamGroupName : 班组，本体用 shift，MES 用班组名
    #   skillLevel → jobName       : 技能等级，MES HRIS 用岗位名称替代

    _FIELD_MAP = {
        "id": "empCode",
        "name": "empName",
        "workshop": "workshop",
        "shift": "teamGroupName",
        "skillLevel": "jobName",
    }

    # ── Action → MES 端点映射 ──────────────────────────────
    # query → GET /MESApi/HRIS/EmpList : 查询员工列表（按工厂筛选）
    # login → POST /SysWebApi/api/OAuth/Authenticate : 员工登录认证
    # getCurrentUser → GET /SysWebApi/api/LoginUserAuthInfo/CurrentUserInfo : 获取当前用户
    # logout → None : 本地清除会话，无 MES 端点

    _ACTION_PATHS = {
        "query": ("/MESApi/HRIS/EmpList", "GET"),
        "login": ("/SysWebApi/api/OAuth/Authenticate", "POST"),
        "getCurrentUser": ("/SysWebApi/api/LoginUserAuthInfo/CurrentUserInfo", "GET"),
        "logout": (None, None),  # 本地操作，不调用 MES
    }

    # ── 辅助方法 ──────────────────────────────────────────────

    def _translate_fields(self, data: dict) -> dict:
        """将本体字段名翻译为 MES API 字段名。"""
        result = {}
        for ont_name, value in data.items():
            target = self._FIELD_MAP.get(ont_name, ont_name)
            result[target] = value
        return result

    # ── 接口实现 ─────────────────────────────────────────────

    def build_request(self, action: str, args: dict) -> dict:
        """构建 MES API 请求。

        query → EmpList 按 keyword 模糊搜索，按 plantCode 过滤工厂。
        login → OAuth Authenticate，使用 Domain/UserAccount/Password/plantCode。
        getCurrentUser → CurrentUserInfo，按 loginUserName 查询当前用户。
        logout → 本地操作，返回空请求。
        """
        ep = self._ACTION_PATHS.get(action)
        if not ep:
            ep = ("/MESApi/HRIS/EmpList", "GET")

        path, method = ep

        if action == "login":
            body = {
                "Domain": args.get("plantCode", "") or "local",
                "UserAccount": args.get("empCode", ""),
                "Password": args.get("password", ""),
                "plantCode": args.get("plantCode", ""),
            }
            return {"path": path, "method": method, "body": body}

        if action == "getCurrentUser":
            login_user = args.get("loginUserName", "")
            plant = args.get("plantCode", "")
            query_path = f"{path}?plantCode={plant}&loginUserName={login_user}"
            return {"path": query_path, "method": method, "body": {}}

        if action == "logout":
            return {"path": "", "method": "GET", "body": {}}

        # query: 标准字段翻译
        body = self._translate_fields(args)
        return {"path": path, "method": method, "body": body}

    def parse_response(self, action: str, data: dict) -> dict:
        """解析 MES API 响应。

        根据 action 类型不同，响应格式不同:
          - login: OAuth 认证响应，提取 AccessToken 和用户信息
          - getCurrentUser: 当前用户信息
          - logout: 本地操作，直接返回
          - query: HRIS 员工列表或分页格式
        """
        # ── login 响应 ──
        if action == "login":
            if data.get("IsSuccess"):
                d = data.get("Data", {})
                token = d.get("AccessToken", "")
                profile = d.get("TokenProfile", {})
                user_name = profile.get("LoginUserName", "")
                # 注册会话映射
                if token:
                    from app.services.auth_service import auth_service as _auth_svc
                    _auth_svc.register_session(token, user_name)
                return {
                    "success": True,
                    "text": f"登录成功，欢迎 {user_name}",
                    "entityId": user_name,
                    "token": token,
                    "loginUserName": user_name,
                }
            msg = data.get("Message") or data.get("message", "认证失败")
            return {"success": False, "text": str(msg), "entityId": None}

        # ── getCurrentUser 响应 ──
        if action == "getCurrentUser":
            user_name = data.get("NowLoginUser", "") or data.get("LoginUserName", "")
            return {
                "success": True,
                "text": f"当前用户: {user_name}（{data.get('RealName', '')}）",
                "entityId": user_name,
                "data": data,
            }

        # ── logout 响应 ──
        if action == "logout":
            return {"success": True, "text": "已退出登录", "entityId": None}

        # ── query 响应 ──
        if isinstance(data, list):
            items = [{
                "id": item.get("empCode", ""),
                "name": item.get("empName", ""),
                "skillLevel": item.get("jobName", ""),
                "shift": item.get("teamGroupName", ""),
                "workshop": item.get("plantName", ""),
            } for item in data]
            return {"success": True, "text": f"返回 {len(items)} 名员工", "entityId": None}

        if data.get("rows"):
            rows = data["rows"]
            items = [{
                "id": r.get("empCode", ""),
                "name": r.get("empName", ""),
                "skillLevel": r.get("jobName", ""),
                "shift": r.get("teamGroupName", ""),
                "workshop": r.get("plantName", ""),
            } for r in rows]
            return {"success": True, "text": f"返回 {len(items)} 名员工", "entityId": None}

        if "error" in data or data.get("success") is False:
            msg = data.get("error") or data.get("message", "操作失败")
            return {"success": False, "text": str(msg), "entityId": None}

        return {
            "success": True,
            "text": "操作完成",
            "entityId": str(data.get("empCode", "")),
        }
