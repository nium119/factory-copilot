"""概念适配器注册中心 — 概念到适配器类的映射。

设计目的
═══════════════════════════════════════════════════════════════════════════
DataBackend 体系中的 ApiBackend 需要知道「每个概念由哪个适配器处理」。
本模块维护一个全局注册表 _ADAPTER_REGISTRY，将概念名映射到适配器类路径。

工作流程:
  1. 启动时 auto_register_adapters() 扫描 app.adapters 包并注册所有适配器
  2. 运行时 ApiBackend 调用 get_adapter_class(concept_name) 获取适配器类
  3. 适配器类通过动态导入加载，失败时返回 None（走 FallbackDataBackend 降级）

注册方式:
  - 自动注册: 在 auto_register_adapters() 中添加 try/except 块
  - 手动注册: 调用 register_adapter(concept_name, class_path)

不依赖 YAML 配置 — 所有翻译逻辑在适配器代码中。
═══════════════════════════════════════════════════════════════════════════
"""

from app.core.logger import log

# {概念名: 适配器类路径}
# 类路径格式: "app.adapters.<模块>.<类名>"
# 示例: {"WorkOrder": "app.adapters.mes_workorder.WorkOrderMESAdapter"}
_ADAPTER_REGISTRY: dict[str, str] = {}


def register_adapter(concept_name: str, class_path: str):
    """注册概念的适配器。

    参数:
      concept_name: 本体中的概念名（如 "WorkOrder", "Equipment"）
      class_path:   适配器类的完整路径
                    格式: "app.adapters.<模块名>.<类名>"
                    示例: "app.adapters.mes_workorder.WorkOrderMESAdapter"
    """
    _ADAPTER_REGISTRY[concept_name] = class_path
    log.info(f"[AdapterRegistry] 已注册: {concept_name} -> {class_path}")


def get_adapter_class(concept_name: str):
    """获取概念的适配器类，未注册时返回 None。

    通过动态导入加载注册表中记录的类路径:
      1. 从 _ADAPTER_REGISTRY 查找类路径
      2. 拆分路径为 模块路径 + 类名
      3. __import__ 动态加载模块
      4. getattr 获取类对象

    ApiBackend 在收到概念操作请求时调用此函数，
    获取适配器类后实例化并调用 build_request / parse_response。

    返回 None 时，ApiBackend 会通过 FallbackDataBackend 降级到其他后端。
    """
    class_path = _ADAPTER_REGISTRY.get(concept_name)
    if not class_path:
        return None
    try:
        parts = class_path.rsplit(".", 1)
        if len(parts) != 2:
            log.warning(f"[AdapterRegistry] 无效的类路径: {class_path}")
            return None
        mod = __import__(parts[0], fromlist=[parts[1]])
        return getattr(mod, parts[1])
    except Exception as e:
        log.warning(f"[AdapterRegistry] 加载适配器失败 {concept_name}: {e}")
        return None


def get_all_backends() -> dict:
    """返回所有已注册适配器的概念列表。

    返回格式: {概念名: {backend: "api"}}
    供前端或其他服务查询哪些概念已对接外部 API。
    """
    return {name: {"backend": "api"} for name in _ADAPTER_REGISTRY}


def auto_register_adapters():
    """启动时自动扫描并注册所有 MES 适配器。

    每个 try/except 块独立处理一个适配器模块:
      - 导入成功: 调用 register_adapter 注册
      - ImportError: 静默跳过（适配器文件可能尚未创建或已删除）

    新增适配器时在此添加对应的 try/except 块即可。

    当前已注册的适配器（~30 个）:
      P0 — WorkOrder + WorkOrderTask + Equipment + QualityCheck
      P1 — Employee + WorkStation + Material + WorkCenter + LineStockInventory + LineStockTransaction
      P2 — 通用查询(17): BOM, Recipe, ESOP, Factory, ProductionLine, LineStockPosition, etc.
          + 独立适配器(4): Mould, Tooling, Andon, ProcessFlowCard
    """
    # ── WorkOrder: 生产工单 ──
    try:
        from app.adapters import mes_workorder
        register_adapter("WorkOrder", "app.adapters.mes_workorder.WorkOrderMESAdapter")
    except ImportError:
        pass

    # ── WorkOrderTask: 工单任务（排产+执行） ──
    try:
        from app.adapters import mes_workordertask
        register_adapter("WorkOrderTask", "app.adapters.mes_workordertask.WorkOrderTaskMESAdapter")
    except ImportError:
        pass

    # ── Equipment: 设备管理 ──
    try:
        from app.adapters import mes_equipment
        register_adapter("Equipment", "app.adapters.mes_equipment.EquipmentMESAdapter")
    except ImportError:
        pass

    # ── QualityCheck: 质检管理 ──
    try:
        from app.adapters import mes_qualitycheck
        register_adapter("QualityCheck", "app.adapters.mes_qualitycheck.QualityCheckMESAdapter")
    except ImportError:
        pass

    # ── Employee: 人员信息 ──
    try:
        from app.adapters import mes_employee
        register_adapter("Employee", "app.adapters.mes_employee.EmployeeMESAdapter")
    except ImportError:
        pass

    # ── WorkStation: 工位管理（含登录/登出） ──
    try:
        from app.adapters import mes_workstation
        register_adapter("WorkStation", "app.adapters.mes_workstation.WorkStationMESAdapter")
    except ImportError:
        pass

    # ── Material: 物料主数据 ──
    try:
        from app.adapters import mes_material
        register_adapter("Material", "app.adapters.mes_material.MaterialMESAdapter")
    except ImportError:
        pass

    # ── WorkCenter: 工作中心 ──
    try:
        from app.adapters import mes_workcenter
        register_adapter("WorkCenter", "app.adapters.mes_workcenter.WorkCenterMESAdapter")
    except ImportError:
        pass

    # ── LineStockInventory: 线边库存 ──
    try:
        from app.adapters import mes_linestock_inventory
        register_adapter("LineStockInventory", "app.adapters.mes_linestock_inventory.LineStockInventoryMESAdapter")
    except ImportError:
        pass

    # ── LineStockTransaction: 线边库存流水 ──
    try:
        from app.adapters import mes_linestock_transaction
        register_adapter("LineStockTransaction", "app.adapters.mes_linestock_transaction.LineStockTransactionMESAdapter")
    except ImportError:
        pass

    # ── P2: 纯查询概念 — 共用 GenericQueryAdapter ──
    try:
        from app.adapters import mes_generic_query

        p2_concepts = [
            "BOM", "BOMItem", "WorkOrderBOM", "WorkOrderBOMItem",
            "ProcessRouting", "ProcessOperation", "ProcessCard",
            "ProductionPreparation",
            "InspectionPoint", "QualityDefect",
            "LineStockWarehouse", "LineStockPosition",
            "WorkStationProcessRecord",
            "Recipe", "ESOP",
            "Factory", "ProductionLine",
        ]
        for concept in p2_concepts:
            register_adapter(concept, "app.adapters.mes_generic_query.GenericQueryAdapter")
    except ImportError:
        pass

    # ── Mould: 模具管理（含领用/归还） ──
    try:
        from app.adapters import mes_mould
        register_adapter("Mould", "app.adapters.mes_mould.MouldMESAdapter")
    except ImportError:
        pass

    # ── Tooling: 工装管理（含领用/归还） ──
    try:
        from app.adapters import mes_tooling
        register_adapter("Tooling", "app.adapters.mes_tooling.ToolingMESAdapter")
    except ImportError:
        pass

    # ── AndonEvent: 安灯异常呼叫 ──
    try:
        from app.adapters import mes_andon
        register_adapter("AndonEvent", "app.adapters.mes_andon.AndonMESAdapter")
    except ImportError:
        pass

    # ── ProcessFlowCard: 流转卡（本体扩展概念） ──
    try:
        from app.adapters import mes_process_flow_card
        register_adapter("ProcessFlowCard", "app.adapters.mes_process_flow_card.ProcessFlowCardMESAdapter")
    except ImportError:
        pass
