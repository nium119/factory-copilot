"""GenericQueryAdapter — 为纯查询概念提供统一的 MES API 适配。

设计目的
═══════════════════════════════════════════════════════════════════════════
P2 中有 11 个概念只有 query action，无须为每个概念写独立适配器文件。
GenericQueryAdapter 通过 _CONCEPT_CONFIGS 配置字典统一管理所有纯查询概念的
端点映射，避免大量重复代码。

每个概念的配置项:
  - path:      MES API 路径
  - method:    HTTP 方法 (默认 GET)
  - fieldMap:  本体属性 → MES 请求参数字段映射
  - rowParser: 响应行解析函数名 (默认 "default")

使用方式:
  在 auto_register_adapters() 中为每个概念注册:
    register_adapter("BOM", "app.adapters.mes_generic_query.GenericQueryAdapter")
  GenericQueryAdapter 根据 self.concept_name 自动查找对应配置。

═══════════════════════════════════════════════════════════════════════════
"""

from app.adapters.base import ConceptAdapter
from app.core.logger import log

# ── 概念配置表 ───────────────────────────────────────────────────
# 每个概念定义其 MES 端点、字段映射和行解析器
# 键 = 本体概念名 (须与 manufacturing.onto.yaml 一致)
_CONCEPT_CONFIGS: dict[str, dict] = {

    # ── 产品定义域 (ProductDefinition) ──
    "BOM": {
        "path": "/MESApi/Bom/getBomList",
        "method": "GET",
        "fieldMap": {
            "id": "bomNo",
            "name": "bomName",
            "label": "bomName",
        },
    },
    "BOMItem": {
        "path": "/MESApi/Bom/getBomDetailList",
        "method": "GET",
        "fieldMap": {
            "id": "itemNo",
            "name": "itemName",
            "materialId": "materialCode",
            "quantity": "qty",
            "unit": "unitName",
            "seq": "seq",
            "bomId": "bomNo",
        },
    },
    "WorkOrderBOM": {
        "path": "/MESApi/MPS/MO/getWorkOrderBom",
        "method": "GET",
        "fieldMap": {
            "id": "bomNo",
            "workOrderId": "workOrderNo",
            "materialId": "materialCode",
            "qty": "qty",
        },
    },
    "WorkOrderBOMItem": {
        "path": "/MESApi/MPS/MO/getWorkOrderBom",
        "method": "GET",
        "fieldMap": {
            "id": "itemNo",
            "name": "itemName",
            "materialId": "materialCode",
            "quantity": "qty",
            "unit": "unitName",
            "seq": "seq",
        },
        # items 嵌套在 BOM 响应的 detailList/bomDetailList 字段中
        "rowKey": "detailList",
    },

    # ── 工艺定义域 (ProcessDefinition) ──
    "ProcessRouting": {
        "path": "/MESApi/MPS/Routing/list",
        "method": "GET",
        "fieldMap": {
            "id": "routingNo",
            "name": "routingName",
            "label": "routingName",
        },
    },
    "ProcessOperation": {
        "path": "/MESApi/MPS/Routing/getProcessPages",
        "method": "GET",
        "fieldMap": {
            "id": "processNo",
            "name": "processName",
            "routingId": "routingNo",
            "sequenceNo": "seq",
            "cycleTime": "cycleTime",
            "setupTime": "setupTime",
        },
    },
    "ProcessCard": {
        "path": "/MESApi/ProcessCardRecord/getPages",
        "method": "GET",
        "fieldMap": {
            "id": "cardNo",
            "name": "cardName",
            "label": "cardName",
            "operationId": "processNo",
            "workCenterId": "workCenterCode",
            "materialId": "materialCode",
            "version": "version",
            "status": "status",
        },
    },

    # ── 生产准备域 ──
    "ProductionPreparation": {
        "path": "/MESApi/Preparation/getPages",
        "method": "GET",
        "fieldMap": {
            "id": "preparationNo",
            "name": "preparationName",
            "label": "preparationName",
            "materialId": "materialCode",
        },
    },

    # ── 质量管控域 (QualityControl) ──
    "InspectionPoint": {
        "path": "/QCMApi/ToCheck/CheckPoints",
        "method": "GET",
        "fieldMap": {
            "id": "checkPointCode",
            "name": "checkPointName",
            "label": "checkPointName",
            "seq": "seq",
            "operationId": "processNo",
            "workStationId": "workStationCode",
        },
    },
    "QualityDefect": {
        "path": "/QCMApi/Unqualified/List",
        "method": "GET",
        "fieldMap": {
            "id": "defectCode",
            "name": "defectName",
            "label": "defectName",
            "defectType": "defectTypeName",
            "defectLevel": "defectLevelName",
            "disposition": "dispositionName",
            "qualityCheckId": "qcNo",
            "qcItemResultId": "qcItemResultNo",
            "qty": "defectCount",
        },
    },

    # ── 线边仓域 (LineStock) ──
    "LineStockWarehouse": {
        "path": "/MESApi/LineStock/Warehouse/getPages",
        "method": "GET",
        "fieldMap": {
            "id": "warehouseCode",
            "name": "warehouseName",
            "label": "warehouseName",
            "code": "warehouseCode",
            "factoryId": "factoryCode",
            "plantCode": "plantCode",
        },
    },
}


def _get_config(concept_name: str) -> dict | None:
    """获取概念的 MES API 配置，未配置时返回 None。"""
    return _CONCEPT_CONFIGS.get(concept_name)


class GenericQueryAdapter(ConceptAdapter):
    """通用查询适配器 — 为纯查询概念提供统一的 MES API 翻译。

    不同于每个概念一个适配器文件，GenericQueryAdapter 通过 _CONCEPT_CONFIGS
    配置字典驱动，所有纯查询概念共用同一个类。

    工作流程:
      1. __init__ 时从 _CONCEPT_CONFIGS 读取概念配置
      2. build_request 翻译字段名并构造请求
      3. parse_response 统一解析 MES 分页/数组/错误格式
    """

    def __init__(self, concept_name: str):
        super().__init__(concept_name)
        self._config = _get_config(concept_name) or {}
        self._field_map: dict[str, str] = self._config.get("fieldMap", {})
        self._row_key: str = self._config.get("rowKey", "rows")
        if not self._config:
            log.warning(f"[GenericQueryAdapter] 未找到概念 {concept_name} 的配置，"
                        f"使用默认端点")

    # ── 辅助方法 ──────────────────────────────────────────────

    def _translate_fields(self, data: dict) -> dict:
        """将本体字段名翻译为 MES API 字段名。"""
        result = {}
        for ont_name, value in data.items():
            target = self._field_map.get(ont_name, ont_name)
            result[target] = value
        return result

    # ── 接口实现 ─────────────────────────────────────────────

    def build_request(self, action: str, args: dict) -> dict:
        """构建 MES API 请求。

        从配置表获取 path/method，翻译 args 字段后构造请求体。
        仅支持 query action — 其他 action 返回空路径。
        """
        if action != "query":
            log.warning(f"[GenericQueryAdapter] {self.concept_name} 不支持 "
                        f"action={action}，仅支持 query")
            return {"path": "", "method": "GET", "body": {}}

        path = self._config.get("path", "")
        method = self._config.get("method", "GET")
        body = self._translate_fields(args)
        return {"path": path, "method": method, "body": body}

    def parse_response(self, action: str, data: dict) -> dict:
        """解析 MES API 响应为 Agent 可读结果。

        处理三种通用 MES 响应格式:
          1. 分页格式: {rows: [...], total: N}
          2. 嵌套格式: {rowKey: [...]}  (如 BOM 的 detailList)
          3. 数组格式: [...]
          4. 错误格式: {error/message}

        返回值中的 items 字段将填充到 Agent 上下文供 LLM 使用。
        """
        # ── 查找包含数据的列表 ──
        # 顺序: rows → rowKey → 数组 → 空
        rows = None

        if isinstance(data, list):
            rows = data
        elif data.get("rows"):
            rows = data["rows"]
        elif self._row_key != "rows" and data.get(self._row_key):
            rows = data[self._row_key]

        if rows:
            concept_label = self._config.get("label", self.concept_name)
            items = [_parse_row(r, self._field_map) for r in rows]
            return {
                "success": True,
                "text": f"返回 {len(items)} 条{concept_label}记录",
                "entityId": None,
            }

        # ── 错误处理 ──
        if "error" in data or data.get("success") is False:
            msg = data.get("error") or data.get("message", "操作失败")
            return {"success": False, "text": str(msg), "entityId": None}

        # ── 单条记录 ──
        return {
            "success": True,
            "text": "操作完成",
            "entityId": str(data.get("no") or data.get("code", "")),
        }


def _parse_row(row: dict, field_map: dict) -> dict:
    """从 MES 响应行提取标准字段。

    所有适配器 parse_response 都使用以下标准输出格式:
      {id, name, label, ...扩展字段}
    确保 LLM 拿到一致的字段名 (本体属性名)，而非 MES 原生字段名。
    """
    item: dict[str, object] = {
        "id": row.get("no") or row.get("code") or row.get("id", ""),
        "name": row.get("name", ""),
    }
    # 保留所有 MES 原始字段，供 LLM 上下文使用
    item.update(row)
    return item
