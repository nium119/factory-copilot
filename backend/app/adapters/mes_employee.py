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

    _ACTION_PATHS = {
        "query": ("/MESApi/HRIS/EmpList", "GET"),
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
        """构建 MES HRIS API 请求。

        EmpList 支持按 keyword 模糊搜索，按 plantCode 过滤工厂。
        """
        ep = self._ACTION_PATHS.get(action)
        if not ep:
            ep = ("/MESApi/HRIS/EmpList", "GET")

        path, method = ep
        body = self._translate_fields(args)
        return {"path": path, "method": method, "body": body}

    def parse_response(self, action: str, data: dict) -> dict:
        """解析 MES HRIS API 响应。

        HRIS 返回格式:
          - list: 员工数组 [{empCode, empName, jobName, teamGroupName, ...}]
          - {rows: [...]}: 分页格式
          - {error: "..."}: 错误
        """
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
