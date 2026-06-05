"""Equipment MES 适配器 — 设备查询和状态变更映射到 MES 设备管理 API。

核心映射逻辑
═══════════════════════════════════════════════════════════════════════════
MES 设备管理基于 MES_Equipment 表，通过标准 REST 端点暴露：

  设备列表 — GET /MESApi/Basic/Equipment/GetPages
    - 分页查询所有设备，返回 {rows: [...], total: N} 格式
    - 字段: code（设备编码）, name（设备名称）, isActive（启用状态）,
            modelCode（型号编码）, modelName（型号名称）

  设备编辑 — POST /MESApi/Basic/Equipment/Edit
    - 编辑设备属性（包括启用/停用状态变更）
    - 需要传设备 id（即 MES 的 code）定位目标设备

本体中的 Equipment 概念对外暴露两个核心操作:
  - query: 查询设备列表（含分页格式处理）
  - changeStatus: 切换设备启用/停用状态
═══════════════════════════════════════════════════════════════════════════
"""

from app.adapters.base import ConceptAdapter


class EquipmentMESAdapter(ConceptAdapter):
    """MES 设备适配器 — 设备语义到 MES Equipment API 的双向翻译。

    设计要点
    ────────
    1. 本体 id → MES code: 本体用 id 标识设备，MES 用 code 作为唯一编码
    2. 本体 status → MES isActive: 本体 status 是文本（如"运行"/"停机"），
       MES isActive 是 bool（true=启用/运行，false=停用/停机）
    3. 本体 type → MES modelCode: 本体 type 表示设备类型，
       MES 用 modelCode 表示设备型号编码
    """

    def __init__(self, concept_name: str):
        super().__init__(concept_name)

    # ── 字段映射 ────────────────────────────────────────────
    # 左侧 = 本体属性名（Equipment 概念定义在 manufacturing.onto.yaml）
    # 右侧 = MES API 请求参数字段名
    #
    # 映射说明:
    #   id              → code            : 设备标识，本体用 id，MES 用 code 作为唯一编码
    #   name            → name            : 设备名称，两边一致
    #   status          → isActive        : 运行状态，本体用文本描述，MES 用布尔值
    #                                        (true=启用/运行, false=停用/停机)
    #   lastMaintenance → lastMaintenance : 最后维护日期，两边一致
    #   type            → modelCode       : 设备类型/型号，本体用 type，MES 用 modelCode

    _FIELD_MAP = {
        "id": "code",
        "name": "name",
        "status": "isActive",
        "lastMaintenance": "lastMaintenance",
        "type": "modelCode",
    }

    # ── Action → MES 端点映射 ──────────────────────────────
    # query        → GET  /MESApi/Basic/Equipment/GetPages  : 分页查询设备列表
    # changeStatus → POST /MESApi/Basic/Equipment/Edit      : 编辑设备（含状态变更）

    _ACTION_PATHS = {
        "query":          ("/MESApi/Basic/Equipment/GetPages", "GET"),
        "changeStatus":   ("/MESApi/Basic/Equipment/Edit", "POST"),
    }

    # ── 辅助方法 ──────────────────────────────────────────────

    def _translate_fields(self, data: dict) -> dict:
        """将本体字段名翻译为 MES API 字段名。

        对每个输入字段查找 _FIELD_MAP 获取 MES 对应字段名，
        未映射的字段保持原名不变。
        """
        result = {}
        for ont_name, value in data.items():
            target = self._FIELD_MAP.get(ont_name, ont_name)
            result[target] = value
        return result

    # ── 接口实现 ─────────────────────────────────────────────

    def build_request(self, action: str, args: dict) -> dict:
        """构建 MES 设备 API 请求。

        query 和 changeStatus 两种 action 的处理差异:
          - query: GET 请求，字段作为查询参数
          - changeStatus: POST 请求，需要将设备 id 作为 body.id 传递给 Edit 端点
            （MES Edit 端点通过 id 字段定位要编辑的设备记录）
        """
        ep = self._ACTION_PATHS.get(action)
        if not ep:
            # 未注册的 action 回退到查询端点
            ep = ("/MESApi/Basic/Equipment/GetPages", "GET")

        path, method = ep
        # 提取设备 ID: 优先取 id，其次取 equipmentId
        entity_id = args.pop("id", "") or args.pop("equipmentId", "")
        path = path.replace("{id}", str(entity_id)) if entity_id else path

        body = self._translate_fields(args)
        if entity_id and method == "POST":
            # MES Edit 端点需要 id 来定位更新目标
            body["id"] = entity_id

        return {"path": path, "method": method, "body": body}

    def parse_response(self, action: str, data: dict) -> dict:
        """解析 MES 设备 API 响应 — 统一转为 Agent 可读格式。

        MES 设备 API 返回三种格式:
          1. list — 直接数组（某些旧版端点直接返回设备数组）
          2. {rows: [...], total: N} — 分页格式（GetPages 标准返回）
          3. dict — 单条操作结果或错误

        isActive 布尔值统一翻译为中文: true→"运行", false→"停机"
        这是为了让 Agent 直接用中文向用户汇报设备状态。
        """
        # 情况1: 直接返回数组
        if isinstance(data, list):
            items = []
            for item in data:
                items.append({
                    "id": item.get("code") or item.get("id", ""),
                    "name": item.get("name", ""),
                    "status": item.get("isActive", ""),
                    "model": item.get("modelName", ""),
                })
            return {"success": True, "text": f"返回 {len(items)} 台设备", "entityId": None}

        # 情况2: 分页格式 — GetPages 的标准返回格式 {rows: [...], total: N}
        if data.get("rows"):
            rows = data["rows"]
            items = [{
                "id": r.get("code", ""),
                "name": r.get("name", ""),
                # isActive 布尔值翻译为中文状态描述
                "status": "运行" if r.get("isActive") else "停机",
                "model": r.get("modelName", ""),
            } for r in rows]
            return {"success": True, "text": f"返回 {len(items)} 台设备", "entityId": None}

        # 情况3: 错误响应
        if "error" in data:
            return {"success": False, "text": str(data["error"]), "entityId": None}

        # 情况4: 操作成功（changeStatus 等）
        labels = {
            "changeStatus": "设备状态已更新",
        }

        return {
            "success": True,
            "text": labels.get(action, "操作完成"),
            "entityId": str(data.get("id", "")),
        }
