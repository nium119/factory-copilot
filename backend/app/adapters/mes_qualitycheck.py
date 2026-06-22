"""QualityCheck MES 适配器 — 质检记录映射到 MES QCM（质量管控）API。

核心映射逻辑
═══════════════════════════════════════════════════════════════════════════
MES 质量管控模块（QCM，Quality Control Management）由两个子系统组成:

  来料检验 — RBCQC_ReceiveRecord（收货质检记录）
    - 物料到货后的质量检验，关联收货单
    - 字段: receiveRecordId, status, judgeResultTypeId, unqualifiedQty, sampleSize
    - API: /QCMApi/PqcRecord/GetPqcPages (GET, 分页查询 PQC 记录)

  过程检验 — 检验点数据提交
    - 生产过程中的质量检查点数据记录
    - API: /QCMApi/ToCheck/RecordCheckPoint (POST, 提交检验点数据)

本体中 QualityCheck 概念对 Agent 暴露为统一的质检抽象:
  - query: 查询质检记录（走 PQC 分页查询）
  - record: 提交检验点数据（走 RecordCheckPoint）
═══════════════════════════════════════════════════════════════════════════
"""

from app.adapters.base import ConceptAdapter


class QualityCheckMESAdapter(ConceptAdapter):
    """MES 质检适配器 — 质检语义到 QCM API 的双向翻译。

    设计要点
    ────────
    1. 两个 action 路由到 QCM 模块下两个不同的子系统 API
    2. 本体 result → MES status: 本体用 result 表示质检结果（合格/不合格），
       MES 用 status 表示质检状态
    3. 本体 disposition → MES judgeResultTypeId: 本体 disposition 表示处置意见
       （放行/退货/让步接收），MES 用 judgeResultTypeId 编码处置类型
    4. POST 请求需要 receiveRecordId 关联到具体的收货质检记录
    """

    def __init__(self, concept_name: str):
        super().__init__(concept_name)

    # ── 字段映射 ────────────────────────────────────────────
    # 左侧 = 本体属性名（QualityCheck 概念定义在 manufacturing.onto.yaml）
    # 右侧 = MES QCM API 请求参数字段名
    #
    # 映射说明:
    #   workOrderId → workOrderId     : 关联工单，两边同名
    #   result      → status          : 质检结果，本体用 result（合格/不合格），
    #                                   MES 用 status 表示质检单状态
    #   disposition → judgeResultTypeId: 处置意见，本体用 disposition（放行/退货/
    #                                    让步接收），MES 用 judgeResultTypeId 编码
    #   checkDate   → createDate      : 检验日期，本体用 checkDate，
    #                                   MES 用 createDate（记录创建时间）
    #   checkType   → qcTypeId        : 检验类型，本体用 checkType（来料检/过程检/
    #                                    出货检），MES 用 qcTypeId 编码
    #   inspector   → inspectorEmpId  : 检验员工号，本体用 inspector，
    #                                   MES 用 inspectorEmpId
    #   rejectedQty → unqualifiedQty  : 不合格数量，本体用 rejectedQty，
    #                                   MES 用 unqualifiedQty
    #   sampleSize  → sampleSize      : 抽样数量，两边同名

    _FIELD_MAP = {
        "workOrderId": "workOrderId",
        "operationId": "operationId",
        "result": "status",
        "disposition": "judgeResultTypeId",
        "checkDate": "createDate",
        "checkType": "qcTypeId",
        "inspector": "inspectorEmpId",
        "rejectedQty": "unqualifiedQty",
        "sampleSize": "sampleSize",
    }

    # ── Action → MES 端点映射 ──────────────────────────────
    # query  → GET  /QCMApi/PqcRecord/GetPqcPages   : 查询 PQC 记录（分页）
    # record → POST /QCMApi/ToCheck/RecordCheckPoint : 提交检验点数据

    _ACTION_PATHS = {
        "query":  ("/QCMApi/PqcRecord/GetPqcPages", "GET"),
        "record": ("/QCMApi/ToCheck/RecordCheckPoint", "POST"),
    }

    # ── 辅助方法 ──────────────────────────────────────────────

    def _translate_fields(self, data: dict) -> dict:
        """将本体字段名翻译为 MES QCM API 字段名。

        对每个输入字段查找 _FIELD_MAP 获取 MES 对应字段名，
        未映射的字段保持原名。
        """
        result = {}
        for ont_name, value in data.items():
            target = self._FIELD_MAP.get(ont_name, ont_name)
            result[target] = value
        return result

    # ── 接口实现 ─────────────────────────────────────────────

    def build_request(self, action: str, args: dict) -> dict:
        """构建 MES QCM API 请求。

        两种 action 的处理:
          - query: GET 请求，字段翻译后作为查询参数
          - record: POST 请求，需要 receiveRecordId 关联收货质检记录
            （RecordCheckPoint 端点要求 receiveRecordId 标识检验目标）
        """
        ep = self._ACTION_PATHS.get(action)
        if not ep:
            # 未注册的 action 回退到 PQC 查询端点
            ep = ("/QCMApi/PqcRecord/GetPqcPages", "GET")

        path, method = ep
        # 提取质检记录 ID: 优先 id，其次 qualityCheckId
        entity_id = args.pop("id", "") or args.pop("qualityCheckId", "")
        path = path.replace("{id}", str(entity_id)) if entity_id else path

        body = self._translate_fields(args)
        if entity_id and method == "POST":
            # RecordCheckPoint 要求 receiveRecordId 标识检验目标收货记录
            body["receiveRecordId"] = entity_id

        return {"path": path, "method": method, "body": body}

    def parse_response(self, action: str, data: dict) -> dict:
        """解析 MES QCM API 响应 — 统一转为 Agent 可读格式。

        MES QCM API 返回三种格式:
          1. list — 直接数组（PQC 记录列表）
          2. {rows: [...], total: N} — 分页格式（GetPqcPages 标准返回）
          3. dict — 单条操作结果或错误

        PQC 记录关键字段:
          - receiveRecordId: 收货质检记录 ID，标识唯一质检单
          - status: 质检状态
          - materialName: 物料名称，便于 Agent 向用户描述
          - qcTypeName: 质检类型名称
          - createDate: 创建日期
        """
        # 情况1: 直接返回数组
        if isinstance(data, list):
            items = []
            for item in data:
                items.append({
                    "id": item.get("receiveRecordId") or item.get("id", ""),
                    "result": item.get("status", ""),
                    "materialName": item.get("materialName", ""),
                    "checkDate": item.get("createDate", ""),
                })
            return {"success": True, "text": f"返回 {len(items)} 条质检记录", "entityId": None}

        # 情况2: 分页格式 — GetPqcPages 的标准返回 {rows: [...], total: N}
        if data.get("rows"):
            rows = data["rows"]
            items = [{
                "id": r.get("receiveRecordId") or r.get("id", ""),
                "result": r.get("status", ""),
                "materialName": r.get("materialName", ""),
                # qcTypeName 是质检类型的显示名（如"来料检"/"过程检"/"出货检"）
                "checkType": r.get("qcTypeName", ""),
                "checkDate": r.get("createDate", ""),
            } for r in rows]
            return {"success": True, "text": f"返回 {len(items)} 条质检记录", "entityId": None}

        # 情况3: 错误响应
        if "error" in data:
            return {"success": False, "text": str(data["error"]), "entityId": None}

        # 情况4: 操作成功（record 提交检验点数据）
        labels = {
            "record": "质检记录已提交",
        }

        return {
            "success": True,
            "text": labels.get(action, "操作完成"),
            "entityId": str(data.get("receiveRecordId") or data.get("id", "")),
        }
