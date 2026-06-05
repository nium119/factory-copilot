# MES 业务分析 & 本体对照 & 适配器规划

> 基于 AL.Extend.MESSolution2 三端（Admin/Api/Execute）前端实际调用代码分析，结合 manufacturing.onto.yaml 47 个概念逐一对照。
> 分析日期: 2026-06-05

---

## 第一部分：MES 业务全景

### 1. 工位执行（Execute 前端 — 操作工）

操作工在工位终端上完成的核心业务流程：

```
Login → ExecuteBaseInfo → 操作工选工单 → ExecuteInfo → PrepareStatus?
                                              │
                          ≠2(未就绪)           =2(就绪)
                         换型验证页            执行页
                             │                   │
                  逐一验证准备项:                ├─ 流转卡扫码
                  Equipment/Mould/              ├─ 报工(RecordReport)
                  Material/Tooling/             ├─ 返工(RecordReReport)
                  ProcessCard/Recipe            ├─ 暂停/恢复
                        │                       ├─ 质检触发
                  全部就绪→自动跳转              └─ 人员/班组管理
```

**核心接口:**

| 阶段 | 接口 | 方法 | 说明 |
|------|------|------|------|
| 登录 | `WorkOrderExecute/Login` | POST | 扫工位码+员工码 |
| 基础信息 | `WorkOrderExecute/ExecuteBaseInfo` | GET | 获取工位基础信息 |
| 工单队列 | `WorkOrderExecute/UnfinishedOrderList` | GET | 待完成工单列表 |
| **执行上下文** | **`WorkOrderExecute/ExecuteInfo`** | **GET** | **核心：返回 ProcessRecordId + PrepareStatus + 按钮/卡片可见性** |
| 报工 | `WorkOrderExecute/RecordReport` | POST | 合格数/报废数/缺陷 |
| 返工 | `WorkOrderExecute/RecordReReport` | POST | 返工报工 |
| 暂停/恢复 | `WorkOrderExecute/RecordPause` / `RecordContinue` | POST | 异常暂停 |
| 按钮状态 | `WorkOrderExecute/ButtonInfos` | GET | 当前可用操作按钮 |
| 报工日志 | `WorkOrderExecute/ReportLog` | GET | 报工历史 |
| 流转卡操作 | `WorkOrderExecute/RecordCard/RecordCardConfirm` | GET/POST | 流转卡查询/确认 |
| 人员管理 | `WorkOrderExecute/EmpList/AddEmp/DelEmp` | GET/POST | 多人操作 |
| 班组交接 | `WorkOrderExecute/TeamGroupLog` | GET/POST/DELETE | 班组日志 |
| 登出 | `WorkOrderExecute/Logout` | POST | 操作工登出 |
| 实时推送 | SignalR `chathub` | WS | refreshPage / refreshExecuteBtnListData |

### 2. 换型验证

操作工选工单后 PrepareStatus ≠ 2 时，逐一扫码确认：

| 准备项 | 扫码验证 | 确认 |
|--------|----------|------|
| 设备 | `CheckEquipmentCode` | `StatusEquipmentConfirm` |
| 模具 | `CheckMouldCode` | `StatusMouldConfirm` |
| 物料 | `CheckMaterialCode` | `RecordMaterialConfirm` |
| 工装 | `CheckToolingCode` | `StatusToolingConfirm` |
| 工艺卡 | `RecordProcessCard` | `StatusProcessCardConfirm` |
| 配方 | `RecordRecipe` | — |
| 物料标签 | `CheckMaterialTagCode` | `StatusMaterialTagConfirm` |
| 浆料 | `CheckSlurryNo` | `StatusSlurryConfirm` |
| 离型纸 | `CheckReleasePaperNo` | `StatusReleasePaperConfirm` |

### 3. 质检体系

**分类维度:**

| 维度 | 值 | 说明 |
|------|-----|------|
| RecordType | 1=IQC 进货, 2=PQC 过程, 3=配方, 4=FQC 成品, 5=OQC 终检 | 检验单据类型 |
| PqcType | 1=首检, 2=巡检, 3=终检 | 过程检子类型 |
| QcTypeCode | Q001=首检(按钮直发), Q003=巡检(按钮直发), Q005=末检(抽屉扫码), Q009=通用过程检(抽屉扫码) | 触发编码 |

**三种触发机制:**

| 方式 | 适用 | 流程 |
|------|------|------|
| 直接触发 | Q001/Q003 | OPBlock 一键 → CreatePqc → 生成 ReceiveRecord |
| 抽屉扫码 | Q005/Q009 | 开 QualityTask → 选流转卡 → CreatePqc |
| 自动抽检 | 报工后 | OrderBoxCardPqcComponemt 面板 → RecordReportPqc |

**检验数据流:**

```
CreatePqc → MES侧: WorkStationProcessRecordPqc
         → QCM侧: ReceiveRecord(Status=0)
→ GenerateReceiveRecord → 查 CheckConfig → 创建 CheckPoints + CheckProjects + CheckResults
→ RecordCheckPoint → 填检验值 → UpdateResult(对比USL/LSL合格/不合格)
→ Confirm → ReceiveRecord.Status = 2(合格) 或 3(不合格→Unqualified处置)
```

**IQC vs PQC/FQC 差异:** IQC 检验点完成后直接 Completed 无需人工判定；PQC/FQC/OQC 需要人工判定。

**配置层级:**

```
CheckConfig（质检方案）
  ├─ 关联 PreparationWorkStationId（按工位匹配）或 MaterialId（FQC/OQC）
  ├─ CheckRule（抽检规则: 步进/比例/次数）
  └─ CheckPoint（检验点）→ CheckPointToProject（项目关联）
       ├─ QcTypes（适用的 QC 类型码）
       ├─ TestStandards（USL/LSL/标准值）
       └─ CheckProject（检验项目: 数值/选项/描述型 + SCADA 自动采集项）
```

### 4. 物料管理（三层库存模型）

```
线边仓库存 (MESLineStockMaterialListLocation)    ← Admin LineStock 管理
  PositionId 物理库位, BarCode 标签, Qty 数量
       │  上料
       ▼
工位物料箱 (MESPrepareCheckLogMaterialStock)     ← 按 WorkStationCode + BatchNo + QrCode 唯一
  Qty(加载数) / UseQty(正常消耗) / ScrapUseQty(报废消耗) / BackQty(回称)
  IsDown(是否已下料) / StockStatus(0=未处理, 1=退库, 2=报废)
       │  关联
       ▼
加工记录物料 (MESPrepareCheckLogEntryMaterial)    ← 按 ProcessRecordId
  Status: 0=未检查, 1=已上料, 2=已下料
```

**操作流水** (MaterialStockEntry Type): 1=上料(Load), 2=下料(Down), 3=正常消耗(Use), 4=报废消耗(Scrap), 5=自动上料(Init)

**生命周期:**

```
① 上料: 扫码 CheckMaterialCode → 冲突检查(同标签不能同时在两工位) → 创建 Stock
② 确认: RecordMaterialConfirm → 创建 StockEntry(Type=Load) → 标记已处理
③ 消耗: 报工时自动扣减 UseQty/ScrapUseQty; 剩余 = Qty - UseQty - ScrapUseQty - BackQty
④ 下料: DownRecordMaterial(加工记录级) 或 DownRecordMaterialStock(工位库存级) → IsDown=true
⑤ 核销: 输入回称重量 BackQty → 差异计算 → 公差校验
         StockStatus=1(退库): 差异值 = Qty - 总消耗 - BackQty
         StockStatus=2(报废): 总消耗 += BackQty → 生成报废单
```

**线边仓状态** (MesStockStatus LEFT JOIN 工位物料箱计算):

| 值 | 含义 | 判断 |
|----|------|------|
| -1 | 未上料 | 线边仓有库存，工位物料箱无记录 |
| 0 | 未下料 | 已加载到工位，IsDown=false, StockStatus=0 |
| 1 | 已下料 | 已从工位移除 |

### 5. 排产计划 (MPS)

| 模块 | 路由前缀 | 说明 |
|------|----------|------|
| MO 制造订单 | `MESApi/MPS/MO` | ERP→MES 制造订单 CRUD，生成工单 BOM |
| 工艺路线 | `MESApi/MPS/Routing` | 物料工艺路线维护、审核 |
| 线计划 | `MESApi/MPS/LinePlan` | 产线排产、排序调整 |
| 生产准备 | `MESApi/MPS/Prepare` | 排产层生产准备 |
| 断线任务 | `MESApi/MPS/Cutting/CuttingTask` | 线材切割排产、负载 |
| 多工位派工 | `MESApi/MPS/BraidTask` | 编织/多工位任务分配 |
| 物料齐套 | `MESApi/MPS/Kit` | 物料齐套检查/确认 |
| 数据导入 | `MESApi/MPS/Import` | Excel 导入 MO/工艺路线/BOM |
| 看板 | `MESApi/MPS/Kanban` | 切割/齐套/工作中心看板 |

### 6. 管理后台 (Admin)

| 模块 | 路由前缀 | 说明 |
|------|----------|------|
| 工单管理 | `MESApi/WorkOrder` | 工单准备、流转卡创建、执行记录、报工查询 |
| 返工审核 | `MESApi/WorkOrderExecute/Reverse` | 返工申请/审核 |
| 流转卡 | `MESApi/ProcessFlowCard` | 流转卡创建/查询 |
| 企业建模 | `MESApi/Basic/WorkShop\|WorkArea\|ProductLine\|WorkStation` | 车间/产线/工位 CRUD |
| 工作中心 | `MESApi/Basic/WorkCenter` | 工作中心 CRUD |
| 设备管理 | `MESApi/Basic/Equipment` | 设备/型号 CRUD |
| 物料扩展 | `MESApi/MaterialExtend` | 生产物料属性扩展 |
| 生产准备 | `MESApi/Preparation` | 工位级物料/设备/模具/工装/E-SOP 配置 |
| BOM | `MESApi/Bom` | BOM 列表/详情/结构 |
| 工艺卡 | `MESApi/ProcessCard*` | 数据项/参数/分类/模板/记录族/记录 |
| 配方 | `MESApi/Recipe*` | 配方/配方族/产品配方 |
| 配料工艺单 | `MESApi/ProduceMix\|ProcessMix` | 配料单生成/审核/执行/报工 |
| E-SOP | `MESApi/ESOP` | 标准作业文件 |
| ERP 工单 | `MESApi/ErpOrder` | ERP 同步 |
| 线边仓 | `MESApi/LineStock/*` | 仓库/库位/库存/出入库/退料/补料/快速调整 |
| 看板 | `MESApi/Dashboard` | 效率/质量/设备/需求统计 |

### 7. 外部集成

| 系统 | 接口前缀 | 说明 |
|------|----------|------|
| 主数据代理 | `ThreeApi/*` | MDM/APS/EAM/HRIS 数据统一入口 |
| WMS | `MESApi/WMS/*` | 出入库任务、消耗回传、标签获取 |
| SAP | `ThreeApiSap/*` | 报工回传、入库回传 |
| HRIS | `MESApi/HRIS/*` | 员工、班组、岗位查询 |
| 安灯 Andon | `AndonWebApi/api/*` | 异常反馈/逐级上报 |
| SCADA | `MESApi/Scada/*` | 设备数采值查询 |
| SignalR | `chathub` | 工位执行页实时推送 |
| 文件服务 | `ExDoc/api/*` | 文件上传/下载 |

---

## 第二部分：本体 47 概念对照

### 对照方法

每个概念按三个维度评估：

- **A 类 — 已有适配器**: 已适配，需检查/修复
- **B 类 — 需新增适配器**: MES 有对应的 API，Agent 会用到
- **C 类 — 纯本体概念**: 无 MES API 对应，Agent 通过 Neo4j 查询本体元数据即可

---

### 树 1: 人员 (Personnel)

| 概念 | Actions | MES API | 评估 | 优先级 |
|------|---------|---------|------|--------|
| **Personnel** | 无 | — | C — 抽象根概念 | — |
| **Role** | 无 | — | C — 字典概念 | — |
| **Employee** | `query` | `MESApi/HRIS/*` (员工/班组查询), `MESApi/WorkOrderExecute/EmpList` (工位人员) | **B — 需新增** | 🔴 高 |

### 树 2: 数据字典 (Dictionary)

| 概念 | Actions | MES API | 评估 | 优先级 |
|------|---------|---------|------|--------|
| **Dictionary** | 无 | — | C — 抽象根概念 | — |
| **OrderStatus** | 无 | — | C — 枚举值 | — |
| **OrderType** | 无 | — | C — 枚举值 | — |
| **QualityDisposition** | 无 | — | C — 枚举值 | — |
| **DefectLevel** | 无 | — | C — 枚举值 | — |
| **DefectType** | 无 | `QCMApi/QualityDefect/Trees2` | C — 可查但不需 Agent 适配器 | 🟢 低 |
| **EquipmentStatus** | 无 | — | C — 枚举值 | — |
| **Priority** | 无 | — | C — 枚举值 | — |
| **InspectionType** | 无 | `QCMApi/CheckProject/getActiveQcTypes` | C — 可查但不需 Agent 适配器 | 🟢 低 |
| **InspectionMethod** | 无 | — | C — 枚举值 | — |

### 树 3: 物理资源 (PhysicalResource)

| 概念 | Actions | MES API | 评估 | 优先级 |
|------|---------|---------|------|--------|
| **PhysicalResource** | 无 | — | C — 抽象根概念 | — |
| **Factory** | 无 | — | C — 本体中无 action，纯元数据 | 🟢 低 |
| **ProductionLine** | 无 | `Basic/ProductLine/*` | C — 本体无 action | 🟢 低 |
| **WorkCenter** | `query` | `Basic/WorkCenter/*`, `MPS/LinePlan/workcenter`, `MPS/Routing/workcenters` | **B — 需新增** | 🔴 高 |
| **WorkStation** | `query` | `Basic/WorkStation/*`, `WorkOrderExecute/Login/Logout` | **B — 需新增(含 Login/Logout)** | 🔴 高 |
| **Equipment** | `query`, `changeStatus` | `Basic/Equipment/*` | **A — 已有适配器 ✅** | — |
| **Mould** | `query`, `assign`, `returnMould` | `Basic/Mould/*`, `WorkOrderExecute/CheckMouldCode`, `Preparation/getMouldStation` | **B — 需新增** | 🟡 中 |
| **Tooling** | `query`, `assign`, `returnTooling` | `WorkOrderExecute/CheckToolingCode`, `Preparation/getToolingStation` | **B — 需新增** | 🟡 中 |
| **Material** | `query` | `MaterialExtend/*`, `MPS/Material/*`, `ThreeApi/getMaterialDataView` | **B — 需新增** | 🔴 高 |

### 树 4: 产品定义 (ProductDefinition)

| 概念 | Actions | MES API | 评估 | 优先级 |
|------|---------|---------|------|--------|
| **ProductDefinition** | 无 | — | C — 抽象根概念 | — |
| **BOM** | `query` | `MESApi/Bom/*`, `ThreeApi/getBomInfo` | **B — 需新增** | 🟡 中 |
| **BOMItem** | `query` | `MESApi/Bom/getBomDetailList` | **B — 需新增** | 🟡 中 |
| **ProductionPreparation** | `query` | `Preparation/*` | **B — 需新增** | 🟡 中 |
| **ProductionPreparationStep** | 无 | — | C — 本体无 action | 🟢 低 |

### 树 5: 工艺定义 (ProcessDefinition)

| 概念 | Actions | MES API | 评估 | 优先级 |
|------|---------|---------|------|--------|
| **ProcessDefinition** | 无 | — | C — 抽象根概念 | — |
| **ProcessRouting** | `query` | `MPS/Routing/*` | **B — 需新增** | 🟡 中 |
| **ProcessOperation** | `query` | `MPS/Routing/*` (含工序信息) | **B — 需新增** | 🟡 中 |
| **ProcessCard** | `query` | `ProcessCard*/*`, `WorkOrderExecute/RecordProcessCard` | **B — 需新增** | 🟡 中 |

### 树 6: 生产指令 (ProductionOrder)

| 概念 | Actions | MES API | 评估 | 优先级 |
|------|---------|---------|------|--------|
| **ProductionOrder** | 无 | — | C — 抽象根概念 | — |
| **WorkOrder** | 12 个 action | `WorkOrder/*`, `WorkOrderExecute/*`, `ProcessFlowCard/*` | **A — 已有适配器 ⚠️ 需修复** | 🔴 高 |
| **WorkOrderTask** | 8 个 action | `MPS/LinePlan/*`, `WorkOrderExecute/*` | **A — 已有适配器 ⚠️ 需修复** | 🔴 高 |
| **WorkOrderBOM** | `query` | `MPS/MO/getWorkOrderBom` | **B — 需新增** | 🟡 中 |
| **WorkOrderBOMItem** | `query` | `MPS/MO/getWorkOrderBom` (含条目) | **B — 需新增** | 🟢 低 |

### 树 7: 质量管控 (QualityControl)

| 概念 | Actions | MES API | 评估 | 优先级 |
|------|---------|---------|------|--------|
| **QualityControl** | 无 | — | C — 抽象根概念 | — |
| **InspectionPoint** | `query` | `QCMApi/ToCheck/CheckPoints` | **B — 需新增** | 🟡 中 |
| **InspectionItem** | 无 | — | C — 本体无 action | 🟢 低 |
| **QualityCheck** | `query`, `record` | `QCMApi/PqcRecord/*`, `QCMApi/ToCheck/*` | **A — 已有适配器 ✅** | — |
| **QualityCheckItemResult** | 无 | — | C — 本体无 action | 🟢 低 |
| **QualityDefect** | `query` | `QCMApi/Unqualified/*`, `QCMApi/QualityDefect/*` | **B — 需新增** | 🟡 中 |

### 树 8: 线边仓 (LineStock)

| 概念 | Actions | MES API | 评估 | 优先级 |
|------|---------|---------|------|--------|
| **LineStock** | 无 | — | C — 抽象根概念 | — |
| **LineStockWarehouse** | `query` | `LineStock/Warehouse/*` | **B — 需新增** | 🟡 中 |
| **LineStockPosition** | 无 | `LineStock/Position/*` | C — 本体无 action | 🟢 低 |
| **LineStockInventory** | `query` | `LineStock/Stock/*` | **B — 需新增** | 🔴 高 |
| **LineStockTransaction** | `query`, `create` | `LineStock/Task/out\|in\|completed`, `LineStock/Down/*` | **B — 需新增** | 🔴 高 |

---

## 第三部分：适配器现状 & 问题

### 已适配概念 (4/23)

| 概念 | 文件 | 状态 | 问题 |
|------|------|------|------|
| WorkOrder | `mes_workorder.py` | ⚠️ 需修复 | **API 路径为占位符** (`/api/production/orders/search`)，未对接真实 MES `MESApi/WorkOrder/*` |
| WorkOrderTask | `mes_workordertask.py` | ⚠️ 需修复 | `startTask` 指向不存在的 `RecordStart` 端点；缺少流转卡关联 |
| Equipment | `mes_equipment.py` | ✅ 正常 | 已对接 `MESApi/Basic/Equipment/*` |
| QualityCheck | `mes_qualitycheck.py` | ✅ 正常 | 已对接 `QCMApi/PqcRecord/*` 和 `ToCheck/*` |

### 需要修复的严重问题

1. **`mes_workorder.py`** — 12 个 action 全部指向虚构的 `/api/production/orders/*` 路径，应改为真实 MES 端点:
   - `query` → `GET /MESApi/WorkOrder/getPages`
   - 生命周期操作(startProduction/markAsComplete/suspend 等) → `POST /MESApi/WorkOrderExecute/*`

2. **`mes_workordertask.py`** — `startTask` 指向不存在的 `RecordStart`。MES 真实开工 = `ProcessFlowCard/createProcessFlow` + `processFlowStart` + `ExecuteInfo` 返回 PrepareStatus=2

---

## 第四部分：优先级分层

### 🔴 P0 — 立即修复（现有适配器错误）

| 任务 | 说明 |
|------|------|
| 修复 `mes_workorder.py` API 路径 | 12 个 action 全部换为真实 MES 端点 |
| 修复 `mes_workordertask.py` 开工逻辑 | startTask → ProcessFlowCard 创建流程 |

### 🔴 P1 — 高频 Agent 交互（必须新增）✅ 已完成

| 概念 | 理由 | MES 端点 | 适配文件 | 状态 |
|------|------|----------|----------|------|
| **Employee** | Agent 查人/分配任务时必须 | `MESApi/HRIS/*`, `WorkOrderExecute/EmpList` | `mes_employee.py` | ✅ |
| **WorkStation** | 工位查询 + Login/Logout | `Basic/WorkStation/*`, `WorkOrderExecute/Login/Logout` | `mes_workstation.py` | ✅ |
| **Material** | 4 个关系引用，被 Agent 查询最频繁 | `MaterialExtend/getPages/getInfo` | `mes_material.py` | ✅ |
| **LineStockInventory** | 实时库存查询，线边物料状态 | `LineStock/Stock/getStockPages` | `mes_linestock_inventory.py` | ✅ |
| **LineStockTransaction** | **有写操作 `create`**，出入库 | `LineStock/Task/out\|in\|completed` | `mes_linestock_transaction.py` | ✅ |
| **WorkCenter** | 排产/任务分配基础数据 | `Basic/WorkCenter/getPages` | `mes_workcenter.py` | ✅ |

### 🟡 P2 — 查询为主（中等优先级）✅ 已完成 (11/13)

**11 个纯查询概念** → 共用 `GenericQueryAdapter`（`mes_generic_query.py`），配置驱动，无需每个概念一个文件：

| 概念 | MES 端点 | 注册方式 | 状态 |
|------|----------|----------|------|
| BOM | `MESApi/Bom/getPages` | GenericQueryAdapter | ✅ |
| BOMItem | `MESApi/Bom/getBomDetailList` | GenericQueryAdapter | ✅ |
| ProcessRouting | `MPS/Routing/getPages` | GenericQueryAdapter | ✅ |
| ProcessOperation | `MPS/Routing/getProcessPages` | GenericQueryAdapter | ✅ |
| ProcessCard | `ProcessCard/getPages` | GenericQueryAdapter | ✅ |
| WorkOrderBOM | `MPS/MO/getWorkOrderBom` | GenericQueryAdapter | ✅ |
| WorkOrderBOMItem | `MPS/MO/getWorkOrderBom` | GenericQueryAdapter | ✅ |
| ProductionPreparation | `Preparation/getPages` | GenericQueryAdapter | ✅ |
| InspectionPoint | `QCMApi/ToCheck/CheckPoints` | GenericQueryAdapter | ✅ |
| QualityDefect | `QCMApi/QualityDefect/Trees2` | GenericQueryAdapter | ✅ |
| LineStockWarehouse | `LineStock/Warehouse/getPages` | GenericQueryAdapter | ✅ |

**待定**: Mould (query/assign/returnMould)、Tooling (query/assign/returnTooling) — 含有写操作，需独立适配器，归入 P3。

### 🟢 P3 — 纯本体/字典 + 待定写操作（无需适配器或延后）

- 24 个概念无需适配器：7 个抽象根概念 + 17 个枚举/叶子概念（Agent 通过 Neo4j 查询）
- 2 个待定: Mould, Tooling（有 assign/return 写操作，MES 中对应 CheckMouldCode/StatusMouldConfirm 执行校验流程）

---

## 第五部分：本体扩展建议

### 已确认缺失的概念

| 概念 | 说明 | MES 对应 | 状态 |
|------|------|----------|------|
| **ProcessFlowCard** (流转卡) | 运行时工艺卡实例，随物料流转 | `ProcessFlowCard/createProcessFlow` + `processFlowStart/End` | ✅ 已新增 |
| **ProcessRecord** (加工记录) | 工位一次加工执行的记录 | `ExecuteInfo` 返回的核心实体 | ❌ 不新增 — WorkStation.getExecutionContext + WorkOrderTask.queryReports 已充分覆盖 |

### 已确认缺失的关系

| 关系 | 说明 | MES 对应 | 状态 |
|------|------|----------|------|
| Employee → WorkStation | "当前在岗" | `WorkOrderExecute/EmpList` | ✅ 已添加 |
| Employee → WorkOrderTask | "操作的任务" | `WorkOrderExecute/AddEmp` | ✅ 已添加 |
| ProcessFlowCard → WorkOrderTask | "流转到哪道工序" | `ProcessFlowCardController` | ✅ 已添加（ProcessFlowCard 本体关系） |

### 已确认缺失的 Action

| 概念 | 需新增 Action | MES 对应 | 状态 |
|------|--------------|----------|------|
| WorkStation | `login`, `logout` | `WorkOrderExecute/Login`, `Logout` | ✅ |
| WorkStation | `getExecutionContext` | `WorkOrderExecute/ExecuteInfo` | ✅ P0 优化 |
| WorkOrderTask | `consumMaterial` | `RecordConsumpMaterial` | ✅ |
| WorkOrderTask | `loadMaterial` (确认上料) | `RecordMaterialConfirm` | ✅ |
| WorkOrderTask | `verifyMaterial` (物料校验) | `CheckMaterialCode` | ✅ P1 优化 — 拆分为两步 |
| WorkOrderTask | `downMaterial` (下料) | `DownRecordMaterial` | ✅ |
| WorkOrderTask | `startTask` 自动创建流转卡 | `createProcessFlow` 降级路由 | ✅ P1 优化 |

### P0/P1 业务流程优化

| 优化 | 效果 | 实现 |
|------|------|------|
| ExecuteInfo 集成 | Agent 调用 4→1（一次获取全部上下文） | WorkStation.getExecutionContext |
| startTask 自动创建流转卡 | 无 flowCardId 时自动路由到 createProcessFlow | mes_workordertask.py |
| loadMaterial 拆分为验证+确认 | 与实际产线两步骤一致 | verifyMaterial + loadMaterial |
| ProcessRecord 不新增 | ExecuteInfo + queryReports 已覆盖 | — |

---

## 总结

| 类别 | 数量 |
|------|------|
| 总概念 | 47 + 1 (ProcessFlowCard) |
| 有 Action 的概念 | 24 |
| 已注册适配器 | **24** (P0:4 + P1:6 + P2:13 + 本体扩展:1) |
| P0 已修复 | 2 (WorkOrder + WorkOrderTask) ✅ |
| P1 已新增 | 6 (Employee/WorkStation/Material/LineStockInventory/LineStockTransaction/WorkCenter) ✅ |
| P2 已覆盖 | 13 (通用查询11 + Mould + Tooling) ✅ |
| 本体扩展 | ProcessFlowCard 概念 ✅ / WorkStation.login+logout ✅ / WorkOrderTask×3 物料 action ✅ / Employee↔WorkStation+WorkOrderTask 关系 ✅ |
| 待定/无需适配器 | ~26 (含无 action 的概念) |
| 本体缺失 (待定) | ProcessRecord 概念 |
