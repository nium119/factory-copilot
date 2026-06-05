"""ProcessFlowCard MES 适配器 — 流转卡映射到 MES ProcessFlowCard API。

核心映射逻辑
═══════════════════════════════════════════════════════════════════════════
MES 流转卡（ProcessFlowCard）：
  - 运行时工艺卡实例，随物料流转贯穿多道工序
  - 生产过程的核心追踪实体，记录每道工序的加工状态

API 端点：
  - GET  /MESApi/ProcessFlowCard/getPages          : 分页查询流转卡
  - POST /MESApi/ProcessFlowCard/createProcessFlow  : 创建流转卡（工序开工时）
  - POST /MESApi/ProcessFlowCard/processFlowStart   : 流转卡开始流转
  - POST /MESApi/ProcessFlowCard/processFlowEnd     : 流转卡完成流转（工序完工时）
  - POST /MESApi/ProcessFlowCard/processFlowCancel  : 取消流转

本体 ProcessFlowCard 有三个 action:
  - query:    查询流转卡
  - create:   创建流转卡（为工单创建，参数: workOrderId + cardId）
  - complete: 完成流转（标记流转卡完成，最后一道工序完工时触发）

流转卡生命周期: 创建 → 流转中 → 已完成 / 已关闭
═══════════════════════════════════════════════════════════════════════════
"""

from app.adapters.base import ConceptAdapter


class ProcessFlowCardMESAdapter(ConceptAdapter):
    """MES 流转卡适配器 — 流转卡语义到 ProcessFlowCard API 的翻译。

    设计要点
    ────────
    1. query → ProcessFlowCard/getPages（分页查询）
    2. create → ProcessFlowCard/createProcessFlow（参数: workOrderNo + cardNo）
    3. complete → ProcessFlowCard/processFlowEnd（标记完成）
    4. MES 流转卡字段: flowCardNo, workOrderNo, processNo, status, currentOperation
    """

    def __init__(self, concept_name: str):
        super().__init__(concept_name)

    # ── 字段映射 ────────────────────────────────────────────
    # 左侧 = 本体属性名（ProcessFlowCard 概念定义在 manufacturing.onto.yaml）
    # 右侧 = MES API 请求参数字段名
    #
    # 映射说明:
    #   id/name          → flowCardNo  : 流转卡编号
    #   workOrderId      → workOrderNo : 工单号
    #   cardId           → cardNo      : 工艺卡编号
    #   currentOperation → processNo   : 当前工序编号
    #   status           → status      : 状态

    _FIELD_MAP = {
        "id": "flowCardNo",
        "name": "flowCardNo",
        "workOrderId": "workOrderNo",
        "cardId": "cardNo",
        "currentOperation": "processNo",
        "status": "status",
    }

    # ── Action → MES 端点映射 ──────────────────────────────
    # query    → GET  /MESApi/ProcessFlowCard/getPages
    # create   → POST /MESApi/ProcessFlowCard/createProcessFlow
    # complete → POST /MESApi/ProcessFlowCard/processFlowEnd

    _ACTION_PATHS = {
        "query":    ("/MESApi/ProcessFlowCard/getPages", "GET"),
        "create":   ("/MESApi/ProcessFlowCard/createProcessFlow", "POST"),
        "complete": ("/MESApi/ProcessFlowCard/processFlowEnd", "POST"),
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
        """构建 MES 流转卡 API 请求。

        create → 构造 createProcessFlow 请求体: {workOrderNo, cardNo}
        complete → 构造 processFlowEnd 请求体: {flowCardNo}
        """
        ep = self._ACTION_PATHS.get(action)
        if not ep:
            ep = ("/MESApi/ProcessFlowCard/getPages", "GET")

        path, method = ep
        body = self._translate_fields(args)

        if action == "create":
            # 创建流转卡需要: workOrderNo + cardNo
            body = {
                "workOrderNo": body.get("workOrderNo", args.get("workOrderId", "")),
                "cardNo": body.get("cardNo", args.get("cardId", "")),
            }

        elif action == "complete":
            # 完成流转需要: flowCardNo
            entity_id = args.get("id", "") or args.get("name", "")
            body = {"flowCardNo": entity_id or body.get("flowCardNo", "")}

        return {"path": path, "method": method, "body": body}

    def parse_response(self, action: str, data: dict) -> dict:
        """解析 MES 流转卡 API 响应。

        getPages 返回: {rows: [{flowCardNo, workOrderNo, status, ...}]}
        createProcessFlow 返回: {success: true, flowCardNo: "..."}
        processFlowEnd 返回: {success: true}
        """
        # 情况1: 分页查询
        if data.get("rows"):
            rows = data["rows"]
            items = [{
                "id": r.get("flowCardNo", ""),
                "name": r.get("flowCardNo", ""),
                "workOrderId": r.get("workOrderNo", ""),
                "status": r.get("status", ""),
                "currentOperation": r.get("processNo", ""),
            } for r in rows]
            return {"success": True, "text": f"返回 {len(items)} 张流转卡", "entityId": None}

        # 情况2: 数组
        if isinstance(data, list):
            items = [{
                "id": item.get("flowCardNo", ""),
                "name": item.get("flowCardNo", ""),
                "status": item.get("status", ""),
            } for item in data]
            return {"success": True, "text": f"返回 {len(items)} 张流转卡", "entityId": None}

        # 情况3: 错误
        if "error" in data or data.get("success") is False:
            msg = data.get("error") or data.get("message", "操作失败")
            return {"success": False, "text": str(msg), "entityId": None}

        # 情况4: create/complete 操作结果
        card_no = data.get("flowCardNo", "")
        labels = {
            "create": f"流转卡 {card_no} 已创建",
            "complete": f"流转卡 {card_no} 已完成",
        }

        return {
            "success": True,
            "text": labels.get(action, "操作完成"),
            "entityId": str(card_no),
        }
