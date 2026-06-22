# MES 业务分析 & 本体适配对照

> 基于 AL.Extend.MESSolution2 三端（Admin/Api/Execute）源码分析，结合 manufacturing.onto.yaml 49 个概念逐一对照。
> 分析日期: 2026-06-05，修订日期: 2026-06-08

**修订说明（2026-06-08）**：
- 修正 `RecordStart` 端点分析：前端 service 有定义但 UI 已被注释，后端控制器中不存在
- 补充 `processFlowStart`/`createProcessFlow` 定位：Admin 侧 `ProcessFlowCardController.cs` 中确实存在
- 补充 Mould/Tooling 端点区分：Admin 侧 `saveMouldStation`/`saveToolingStation` vs Execute 侧 `StatusMouldConfirm`/`StatusToolingConfirm`
- 补充 POST 参数格式差异：部分端点使用 query string + 空 body 而非 JSON body
- 补充 `DownRecordMaterial`/`DownRecordMould` 后端控制器存在性确认
- **重写开工+报工流程**：区分工单工序开工 (`ExecuteInfo?workOrderMainId=`) vs 扫描流转卡开工 (`ExecuteInfo?cardNo=`) 两种路径，补充报工表单字段和请求体结构，补充 Execute 前端死代码清单

---

## 第一部分：MES 业务全景

### 1. 工位执行（Execute 前端 — 操作工）

操作工在工位终端上完成的核心业务流程：

```
Login → ExecuteBaseInfo → 操作工选择加工对象
                              │
              ┌───────────────┴───────────────┐
              │                               │
         方式一: 工单工序开工              方式二: 扫描流转卡开工
         (工序工单 Tab)                 (流转卡 Tab / 扫码枪)
              │                               │
    ExecuteInfo({                   ExecuteInfo({
      workOrderMainId,                cardNo,
      workStationId                   workStationId
    })                              })
              │                               │
              └───────────────┬───────────────┘
                              │
                         PrepareStatus?
                              │
              ≠2(未就绪)       =2(就绪)
             换型验证页        执行页
                 │               │
      逐一验证准备项:            ├─ 报工记录卡（流转卡列表 + 扫码输入框）
      Equipment/Mould/          ├─ 报工(RecordReport) — 良品数/报废数/缺陷/是否完工
      Material/Tooling/         ├─ 返工(RecordReReport)
      ProcessCard/Recipe        ├─ 暂停/恢复(RecordPause/RecordContinue)
            │                   ├─ 质检触发(CreatePqc / RecordReportPqc)
      全部就绪→自动跳转          ├─ 成品入库(RecordReportFinishProduct)
                                ├─ 生产记录(ProductionRecord/saveData)
                                └─ 人员/班组管理
```

#### 1.1 开工 — 两种方式

MES 工位执行中"开工"没有独立的 API 端点，而是通过调用 `ExecuteInfo` 隐式完成。`ExecuteInfo` 是执行上下文的核心 API——它不仅返回工单/流转卡/工序/物料/按钮可见性等信息，还会在首次调用时**自动创建或检索 ProcessRecord（加工记录）**，这本身就是"开工"动作。

前端 `RecordStart` (POST /MESApi/WorkOrderExecute/RecordStart) 在 service 层有定义（`src/service/index.js:910`），History 组件中也导入了它，但 Execute V3 的 UI 调用已被注释（`OrderBoxCardComponemt/index.jsx:91`），且后端控制器中不存在该端点，属于废弃的 UI 功能。`ShowRecordStart` 标志仍由后端 `ExecuteInfo` 返回，仅影响按钮可见性判断。

##### 方式一：工单工序开工

操作工从工单队列的"**工序工单**"Tab 中选择一条未完成的工单，点击"加工"按钮。

- **UI**: `OrderQueue.jsx` Tab "工序工单"，`getUnfinishedOrderListV3` → `GET /WorkOrderExecute/UnfinishedOrderList`
- **触发**: 点击行操作"加工"按钮 (`OrderQueue.jsx:295-311`)
- **API**: `dispatch(getExecuteInfo({workStationId, workOrderMainId, cardEntryId: ""}))`
- **实际请求**: `GET /MESApi/WorkOrderExecute/ExecuteInfo?workStationId=...&workOrderMainId=...`
- **效果**: 后端为该工单+工位创建/检索 ProcessRecord，返回包含 `PrepareStatus`、`ProcessRecordId`、`RecordCardDtos`、按钮可见性等完整执行上下文

##### 方式二：扫描流转卡开工

操作工通过扫码枪扫描已有流转卡的条码，或从工单队列的"**流转卡**"Tab 中选择一条已有流转卡。

- **扫码路径** (`OrderBoxCardComponemt/index.jsx:141-194`):
  - 在"报工记录"卡片的 Input 中扫描流转卡条码 → `_getExecInfoV3({qrCode})` → `ExecuteInfoV3({workStationId, cardNo})`
  - 如果扫描的流转卡属于不同工单 → 弹窗确认"是否切换工单" → 切换后重新调用 `getExecuteInfo`
- **选卡路径** (`OrderQueue.jsx` Tab "流转卡", `464-479`):
  - 点击"流转卡"Tab → `getUnfinishedProcessFlowCardListV3` → `GET /WorkOrderExecute/UnfinishedProcessFlowCardList`
  - 点击行操作"加工" → `dispatch(getExecuteInfo({workStationId, cardNo: record.QrCode}))`
- **实际请求**: `GET /MESApi/WorkOrderExecute/ExecuteInfo?workStationId=...&cardNo=...`
- **效果**: 后端为该流转卡创建/检索 ProcessRecord，返回执行上下文

##### 两种方式的本质统一

```
工单工序开工: ExecuteInfo(workOrderMainId)  → 无流转卡 → 首次报工时系统自动创建流转卡
扫描流转卡开工: ExecuteInfo(cardNo)          → 已有流转卡 → 直接绑定该流转卡执行
```

两者最终都调用 `ExecuteInfo`，区别在于**是否有已有流转卡**。无流转卡时，系统在首次报工 (`RecordReport`) 时根据参数 `IsSealBox` 决定是否自动创建新流转卡。

#### 1.2 报工 — 生产数据上报

报工是执行页的核心操作，操作工填写本次加工的数量和质量信息。

- **UI**: `Report.jsx` 全屏抽屉表单
- **触发**: `OPBlock` 中的"报工"按钮（`ShowRecordReport === true` 时可见）
- **API**: `POST /MESApi/WorkOrderExecute/RecordReport`（`RecordReportV3`, JSON body）
- **表单字段**:
  | 字段 | 说明 |
  |------|------|
  | `Id` | 流转卡号（`RecordCardDtos[0].Id`，预填充） |
  | `QualifiedQty` | 合格/报工数量（必填） |
  | `ScrapQty` | 报废数量 |
  | `QualityDefectId` | 报废原因（报废数>0 时必填，TreeSelect） |
  | `Remark` | 备注 |
  | `IsComplete` | 是否完工（合格数 ≥ 剩余数时弹窗确认"是否进行完工？"） |
  | `IsSealBox` | 创建新流转卡（勾选后本次报工创建新流转卡继续生产） |
- **请求体结构**:
  ```json
  {
    "ProcessRecordId": "...",
    "EmpNo": "...",
    "ClassName": "...",
    "RecordCardDtos": [{
      "Id": "...",
      "QualifiedQty": 100,
      "ScrapQty": 5,
      "QualityDefectId": "...",
      "Remark": "...",
      "IsComplete": false,
      "IsSealBox": false
    }]
  }
  ```

**其他报工类型**:
| 类型 | API | 组件 | 说明 |
|------|-----|------|------|
| 返工 | `POST /WorkOrderExecute/RecordReReport` | `Rework.jsx` | 返工数量和原因 |
| PQC 自检 | `POST /WorkOrderExecute/RecordReportPqc` | `OrderBoxCardPqcComponemt` | 过程质量检验报工 |
| 成品入库 | `POST /WorkOrderExecute/RecordReportFinishProduct` | `OrderBoxCardPrintLabelComponemt` | 成品入库+打印标签 |
| 批次通过 | `POST /WorkOrderExecute/RecordReportBatchPass` | OPBlock 按钮 | PQC 批次通过 |

#### 1.3 核心接口汇总

| 阶段 | 接口 | 方法 | 参数格式 | 说明 |
|------|------|------|----------|------|
| 登录 | `WorkOrderExecute/Login` | POST | JSON body | 扫工位码+员工码 |
| 基础信息 | `WorkOrderExecute/ExecuteBaseInfo` | GET | query params | 获取工位基础信息 |
| 工单队列 | `WorkOrderExecute/UnfinishedOrderList` | GET | query params | "工序工单"Tab 数据 |
| 流转卡队列 | `WorkOrderExecute/UnfinishedProcessFlowCardList` | GET | query params | "流转卡"Tab 数据 |
| **执行上下文** | **`WorkOrderExecute/ExecuteInfo`** | **GET** | **query params** | **核心：两种开工方式的统一入口** |
| 报工 | `WorkOrderExecute/RecordReport` | POST | JSON body | 合格数/报废数/缺陷/完工标记 |
| 返工 | `WorkOrderExecute/RecordReReport` | POST | JSON body | 返工报工 |
| 暂停 | `WorkOrderExecute/RecordPause` | POST | query string + 空 body | 暂停加工 |
| 恢复 | `WorkOrderExecute/RecordContinue` | POST | query string + 空 body | 恢复加工 |
| 换型 | `WorkOrderExecute/ChangeModel` | POST | query string + 空 body | 切换工位生产型号 |
| 按钮状态 | `WorkOrderExecute/ButtonInfos` | GET | query params | 当前可用操作按钮 |
| 报工日志 | `WorkOrderExecute/ReportLog` | GET | query params | 报工历史 |
| 流转卡查询 | `WorkOrderExecute/RecordCard` | GET | query params | 查询流转卡信息 |
| 流转卡确认 | `WorkOrderExecute/RecordCardConfirm` | POST | query string + 空 body | 确认流转卡 |
| 人员管理 | `WorkOrderExecute/EmpList/AddEmp/DelEmp` | GET/POST | mixed | 多人操作 |
| 班组交接 | `WorkOrderExecute/TeamGroupLog` | GET/POST/DELETE | mixed | 班组日志 |
| 登出 | `WorkOrderExecute/Logout` | POST | JSON body | 操作工登出 |
| 实时推送 | SignalR `chathub` | WS | — | refreshPage / refreshExecuteBtnListData |

#### 1.4 ExecuteInfo 返回结构（关键字段）

| 字段路径 | 说明 | 用途 |
|---------|------|------|
| `Data.PrepareStatus` | 0=空闲, 1=换型中, 2=就绪 | 决定跳转执行页还是换型页 |
| `Data.HeadInfoDto.ProcessRecordId` | 加工记录 ID | 报工/暂停/恢复的核心标识 |
| `Data.HeadInfoDto.ExecuteOperateBtnVisibleDto.ShowRecordStart` | 开工按钮可见性 | 按钮控制（当前 UI 已禁用） |
| `Data.HeadInfoDto.ExecuteOperateBtnVisibleDto.ShowRecordReport` | 报工按钮可见性 | OPBlock "报工"按钮 |
| `Data.HeadInfoDto.ExecuteOperateBtnVisibleDto.ShowRecordPause` | 挂起按钮可见性 | OPBlock "挂起/继续执行"按钮 |
| `Data.HeadInfoDto.ExecuteCardInfoVisibleDto` | 卡片可见性 | 控制 PQC/打印标签等卡片显隐 |
| `Data.HeadInfoDto.OrderInfoDto.RecordCardDtos` | 流转卡列表 | 报工记录卡数据源 |
| `Data.HeadInfoDto.OrderInfoDto.WorkOrderMainDto` | 工单信息 | 当前加工的工单 |
| `Data.StatusInfoDtos` | 准备项状态列表 | 换型验证项的状态

### 2. 换型验证

操作工选工单后 PrepareStatus ≠ 2 时，逐一扫码确认：

| 准备项 | 扫码验证 | 确认 | 参数格式 |
|--------|----------|------|----------|
| 设备 | `CheckEquipmentCode` | `StatusEquipmentConfirm` | query string + 空 body |
| 模具 | `CheckMouldCode` | `StatusMouldConfirm` | POST JSON body |
| 物料 | `CheckMaterialCode` | `RecordMaterialConfirm` | query string + 空 body |
| 工装 | `CheckToolingCode` | `StatusToolingConfirm` | query string + 空 body |
| 工艺卡 | `RecordProcessCard` | `StatusProcessCardConfirm` | — |
| 配方 | `RecordRecipe` | — | — |
| 物料标签 | `CheckMaterialTagCode` | `StatusMaterialTagConfirm` | query string + 空 body |
| 浆料 | `CheckSlurryNo` | `StatusSlurryConfirm` | — |
| 离型纸 | `CheckReleasePaperNo` | `StatusReleasePaperConfirm` | — |

**关键区分 — Admin 侧 vs Execute 侧端点：**

| 操作 | Admin 侧 (Preparation 模块) | Execute 侧 (工位换型) |
|------|--------------------------|---------------------|
| 模具绑定/确认 | `saveMouldStation` (POST) — 排产准备 | `StatusMouldConfirm` (POST) — 换型确认 |
| 模具查询 | `getMouldStation` (GET) — 已绑定模具 | `RecordMould` (GET) — 工位模具记录 |
| 工装绑定/确认 | `saveToolingStation` (POST) — 排产准备 | `StatusToolingConfirm` (POST) — 换型确认 |
| 工装查询 | `getToolingStation` (GET) — 已绑定工装 | `RecordTool` (GET) — 工位工装记录 |

**Agent 适配器使用 Admin 侧端点** (`saveMouldStation`/`saveToolingStation`)，因为 Agent 的操作语义是"领用/分配"（将模具/工装分配给设备/工位），属于排产准备范畴，而非换型时的状态确认。

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

> **注意**: Execute 前端未直接调用 `DownRecordMaterial`/`DownRecordMaterialStock`，这些端点在后端控制器 `WorkOrderExecuteItemStatusMaterialController.cs` 中存在，可能用于 Admin 或 WMS 集成场景。

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
| 流转卡 | `MESApi/ProcessFlowCard` | 流转卡创建/查询/开工/完工 |
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

## 第二部分：本体概念对照

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
| **Employee** | `query` | `MESApi/HRIS/*` (员工/班组查询), `MESApi/WorkOrderExecute/EmpList` (工位人员) | **A — 已适配 ✅** | — |

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
| **WorkCenter** | `query` | `Basic/WorkCenter/*`, `MPS/LinePlan/workcenter`, `MPS/Routing/workcenters` | **A — 已适配 ✅** | — |
| **WorkStation** | `query`, `login`, `logout`, `getExecutionContext` | `Basic/WorkStation/*`, `WorkOrderExecute/Login/Logout/ExecuteInfo` | **A — 已适配 ✅** | — |
| **Equipment** | `query`, `changeStatus` | `Basic/Equipment/*` | **A — 已适配 ✅** | — |
| **Mould** | `query`, `assign`, `returnMould` | `Basic/Mould/*`, `WorkOrderExecute/CheckMouldCode/StatusMouldConfirm`, `Preparation/saveMouldStation/getMouldStation` | **A — 已适配 ✅** (Admin侧) | — |
| **Tooling** | `query`, `assign`, `returnTooling` | `WorkOrderExecute/CheckToolingCode/StatusToolingConfirm/RecordTool`, `Preparation/saveToolingStation/getToolingStation` | **A — 已适配 ✅** (Admin侧) | — |
| **Material** | `query` | `MaterialExtend/*`, `MPS/Material/*`, `ThreeApi/getMaterialDataView` | **A — 已适配 ✅** | — |

### 树 4: 产品定义 (ProductDefinition)

| 概念 | Actions | MES API | 评估 | 优先级 |
|------|---------|---------|------|--------|
| **ProductDefinition** | 无 | — | C — 抽象根概念 | — |
| **BOM** | `query` | `MESApi/Bom/*`, `ThreeApi/getBomInfo` | **A — 已适配 ✅** | — |
| **BOMItem** | `query` | `MESApi/Bom/getBomDetailList` | **A — 已适配 ✅** | — |
| **ProductionPreparation** | `query` | `Preparation/*` | **A — 已适配 ✅** | — |
| **ProductionPreparationStep** | 无 | — | C — 本体无 action | 🟢 低 |

### 树 5: 工艺定义 (ProcessDefinition)

| 概念 | Actions | MES API | 评估 | 优先级 |
|------|---------|---------|------|--------|
| **ProcessDefinition** | 无 | — | C — 抽象根概念 | — |
| **ProcessRouting** | `query` | `MPS/Routing/*` | **A — 已适配 ✅** | — |
| **ProcessOperation** | `query` | `MPS/Routing/*` (含工序信息) | **A — 已适配 ✅** | — |
| **ProcessCard** | `query` | `ProcessCard*/*`, `WorkOrderExecute/RecordProcessCard` | **A — 已适配 ✅** | — |

### 树 6: 生产指令 (ProductionOrder)

| 概念 | Actions | MES API | 评估 | 优先级 |
|------|---------|---------|------|--------|
| **ProductionOrder** | 无 | — | C — 抽象根概念 | — |
| **WorkOrder** | 12 个 action | `WorkOrder/*`, `WorkOrderExecute/*`, `ProcessFlowCard/*` | **A — 已适配 ⚠️ 需验证** | 🔴 高 |
| **WorkOrderTask** | 10 个 action | `MPS/LinePlan/*`, `WorkOrderExecute/*`, `ProcessFlowCard/*` | **A — 已适配 ⚠️ 需验证** | 🔴 高 |
| **WorkOrderBOM** | `query` | `MPS/MO/getWorkOrderBom` | **A — 已适配 ✅** | — |
| **WorkOrderBOMItem** | `query` | `MPS/MO/getWorkOrderBom` (含条目) | **A — 已适配 ✅** | — |

### 树 7: 质量管控 (QualityControl)

| 概念 | Actions | MES API | 评估 | 优先级 |
|------|---------|---------|------|--------|
| **QualityControl** | 无 | — | C — 抽象根概念 | — |
| **InspectionPoint** | `query` | `QCMApi/ToCheck/CheckPoints` | **A — 已适配 ✅** | — |
| **InspectionItem** | 无 | — | C — 本体无 action | 🟢 低 |
| **QualityCheck** | `query`, `record` | `QCMApi/PqcRecord/*`, `QCMApi/ToCheck/*` | **A — 已适配 ✅** | — |
| **QualityCheckItemResult** | 无 | — | C — 本体无 action | 🟢 低 |
| **QualityDefect** | `query` | `QCMApi/Unqualified/*`, `QCMApi/QualityDefect/*` | **A — 已适配 ✅** | — |

### 树 8: 线边仓 (LineStock)

| 概念 | Actions | MES API | 评估 | 优先级 |
|------|---------|---------|------|--------|
| **LineStock** | 无 | — | C — 抽象根概念 | — |
| **LineStockWarehouse** | `query` | `LineStock/Warehouse/*` | **A — 已适配 ✅** | — |
| **LineStockPosition** | 无 | `LineStock/Position/*` | C — 本体无 action | 🟢 低 |
| **LineStockInventory** | `query` | `LineStock/Stock/*` | **A — 已适配 ✅** | — |
| **LineStockTransaction** | `query`, `create` | `LineStock/Task/out\|in\|completed`, `LineStock/Down/*` | **A — 已适配 ✅** | — |

---

## 第三部分：适配器现状 & 问题

### 已适配概念 (24/24，全部已注册)

| 概念 | 文件 | 状态 | 说明 |
|------|------|------|------|
| WorkOrder | `mes_workorder.py` | ✅ 已适配 | 对接 `MESApi/WorkOrder/*` |
| WorkOrderTask | `mes_workordertask.py` | ✅ 已适配 | 三层路由 (计划/流转卡/执行) |
| Equipment | `mes_equipment.py` | ✅ 已适配 | 对接 `MESApi/Basic/Equipment/*` |
| QualityCheck | `mes_qualitycheck.py` | ✅ 已适配 | 对接 `QCMApi/PqcRecord/*` |
| Employee | `mes_employee.py` | ✅ 已适配 | 对接 `MESApi/HRIS/*` |
| WorkStation | `mes_workstation.py` | ✅ 已适配 | 对接 `WorkOrderExecute/Login/Logout/ExecuteInfo` |
| Material | `mes_material.py` | ✅ 已适配 | 对接 `MaterialExtend/*` |
| WorkCenter | `mes_workcenter.py` | ✅ 已适配 | 对接 `Basic/WorkCenter/*` |
| LineStockInventory | `mes_linestock_inventory.py` | ✅ 已适配 | 对接 `LineStock/Stock/*` |
| LineStockTransaction | `mes_linestock_transaction.py` | ✅ 已适配 | 对接 `LineStock/Task/*` |
| Mould | `mes_mould.py` | ✅ 已适配 | Admin 侧 Preparation 端点 |
| Tooling | `mes_tooling.py` | ✅ 已适配 | Admin 侧 Preparation 端点 |
| ProcessFlowCard | `mes_processflowcard.py` | ✅ 已适配 | 对接 `ProcessFlowCard/*` |
| BOM | `mes_generic_query.py` | ✅ 已适配 | 通用查询适配器 |
| BOMItem | `mes_generic_query.py` | ✅ 已适配 | 通用查询适配器 |
| ProcessRouting | `mes_generic_query.py` | ✅ 已适配 | 通用查询适配器 |
| ProcessOperation | `mes_generic_query.py` | ✅ 已适配 | 通用查询适配器 |
| ProcessCard | `mes_generic_query.py` | ✅ 已适配 | 通用查询适配器 |
| WorkOrderBOM | `mes_generic_query.py` | ✅ 已适配 | 通用查询适配器 |
| WorkOrderBOMItem | `mes_generic_query.py` | ✅ 已适配 | 通用查询适配器 |
| ProductionPreparation | `mes_generic_query.py` | ✅ 已适配 | 通用查询适配器 |
| InspectionPoint | `mes_generic_query.py` | ✅ 已适配 | 通用查询适配器 |
| QualityDefect | `mes_generic_query.py` | ✅ 已适配 | 通用查询适配器 |
| LineStockWarehouse | `mes_generic_query.py` | ✅ 已适配 | 通用查询适配器 |

### 适配器端点对照验证

以下逐一验证关键适配器的端点选择是否正确。

#### WorkOrderTask (`mes_workordertask.py`)

| Action | 适配器端点 | MES 源码验证 | 结论 |
|--------|-----------|-------------|------|
| `query` | `GET /MPS/LinePlan/list` | ✅ 排产计划列表查询 | 正确 |
| `startTask` | `POST /ProcessFlowCard/processFlowStart` | ✅ Admin 流转卡开工（`ProcessFlowCardController.cs:99`） | 正确（Admin 语义，对应工单工序开工） |
| `startTask` (降级) | `POST /ProcessFlowCard/createProcessFlow` | ✅ Admin 创建流转卡（`ProcessFlowCardController.cs:79`） | 正确（无流转卡时自动创建） |
| `completeTask` | `POST /WorkOrderExecute/RecordReport` | ✅ 报工（`WorkOrderExecuteOperateController.cs:69`） | 正确 |
| `suspendTask` | `POST /WorkOrderExecute/RecordPause` | ✅ 暂停（`WorkOrderExecuteOperateController.cs:294`） | 正确 |
| `resumeTask` | `POST /WorkOrderExecute/RecordContinue` | ✅ 恢复（`WorkOrderExecuteOperateController.cs:315`） | 正确 |
| `changeover` | `POST /WorkOrderExecute/ChangeModel` | ✅ 换型（前端 query string + 空 body） | 正确 |
| `reportProgress` | `POST /WorkOrderExecute/RecordReport` | ✅ 阶段性报工 | 正确 |
| `queryReports` | `GET /WorkOrderExecute/ReportLog` | ✅ 报工历史 | 正确 |
| `verifyMaterial` | `GET /WorkOrderExecute/CheckMaterialCode` | ✅ 物料校验（前端 GET + params） | 正确 |
| `loadMaterial` | `POST /WorkOrderExecute/RecordMaterialConfirm` | ✅ 确认上料（前端 query string + 空 body） | 正确 |
| `consumMaterial` | `POST /WorkOrderExecute/RecordConsumpMaterialConfirm` | ✅ 消耗确认 | 正确 |
| `downMaterial` | `POST /WorkOrderExecute/DownRecordMaterial` | ✅ 后端存在（`WorkOrderExecuteItemStatusMaterialController.cs:251`） | 正确 |

> **Execute 侧 vs Admin 侧"开工"语义差异**:
> - **Execute 侧**（操作工）: `ExecuteInfo` 即是隐式开工——首次调用自动创建 ProcessRecord。MES 后端没有独立的 `POST /WorkOrderExecute/RecordStart` 端点，前端同名函数已被注释。实际生产中操作工通过两种方式进入执行状态: ① 工单工序开工 (`ExecuteInfo?workOrderMainId=`) ② 扫描流转卡开工 (`ExecuteInfo?cardNo=`)。
> - **Admin 侧**（管理后台）: `processFlowStart` 是流转卡状态转换端点（`ProcessFlowCardController.cs:99`），将流转卡从"已创建"转为"已开工"，属于 Admin 流转卡生命周期管理。
> - **适配器选择**: 当前 `startTask → processFlowStart` 采用 Admin 语义，对 Agent 通过 API 操作是合理的。如果需要更贴近 Execute 侧语义，可考虑 `startTask → ExecuteInfo` 然后直接进入报工状态。

#### Mould (`mes_mould.py`)

| Action | 适配器端点 | MES 源码验证 | 结论 |
|--------|-----------|-------------|------|
| `query` | `GET /Basic/Mould/GetActiveMoulds` | ✅ 活跃模具查询 | 正确 |
| `assign` | `POST /Preparation/saveMouldStation` | ✅ Admin 模具绑定工位（`MESPreparationMouldController.cs:33`） | 正确（Admin 语义） |
| `returnMould` | `POST /WorkOrderExecute/DownRecordMould` | ✅ 后端存在（`WorkOrderExecuteItemStatusMouldController.cs:88`） | 正确 |

> **`saveMouldStation` vs `StatusMouldConfirm`**: 前者是 Admin 侧 Preparation 模块的"排产准备-模具绑定"端点（Admin 前端使用），后者是 Execute 侧换型验证的"模具状态确认"端点。Agent 的 `assign` 语义是"领用/分配"，属于 Admin 范畴，使用 `saveMouldStation` 正确。

#### Tooling (`mes_tooling.py`)

| Action | 适配器端点 | MES 源码验证 | 结论 |
|--------|-----------|-------------|------|
| `query` | `GET /WorkOrderExecute/RecordTool` | ✅ 工位工装查询（`WorkOrderExecuteItemStatusEquipmentController.cs:23`） | 正确 |
| `assign` | `POST /Preparation/saveToolingStation` | ✅ Admin 工装绑定工位（`MESPreparationToolingController.cs:33`） | 正确（Admin 语义） |
| `returnTooling` | `POST /Preparation/saveToolingStation` | ✅ 复用同一端点（更新状态为封存） | 可接受 |

### 需要关注的潜在问题

1. **参数格式差异**: `RecordPause`/`RecordContinue`/`ChangeModel`/`RecordStart`(已废弃)/`RecordMaterialConfirm`/`StatusToolingConfirm` 在 MES 前端使用 **query string 参数 + 空 body** (`data: {}`)，而我们的适配器发送 JSON body。这可能在对接真实 MES 时导致参数无法正确解析。待 E2E 测试验证。

2. **`ProcessFlowCard` 权限边界**: `processFlowStart`/`createProcessFlow` 在 `ProcessFlowCardController.cs`（Admin 侧），调用需要 Admin 权限。如果 Agent 以操作工身份运行，这些端点可能返回 403。

3. **Mould/Tooling 端点选择固化**: 当前适配器选择 Admin 侧 Preparation 端点。如果未来 Agent 需要在工位执行上下文中操作（如换型验证），需新增 Execute 侧端点映射或增加上下文感知路由。

---

## 第四部分：端点存在性交叉验证

### 前端 service 有定义但后端控制器不存在的端点

| 端点 | 前端位置 | 后端控制器 | 状态 |
|------|---------|-----------|------|
| `POST /WorkOrderExecute/RecordStart` | Execute `service/index.js:910` + Admin `qms.js:752` | **不存在** | 🔴 废弃/未实现 |

该端点在前端两处 service 中定义但：
- Execute UI 中调用已被注释 (`OrderBoxCardComponemt/index.jsx:91`)
- Admin `History` 组件中有导入但未作为独立 API 使用
- 后端无对应控制器 Action
- 实际"开工"通过 `ExecuteInfo` 隐式完成（见第一部分 1.1 两种开工方式）
- `ShowRecordStart` 标志仅用于按钮可见性判断，不影响实际流程

### 后端控制器存在但 Execute 前端未直接调用的端点

| 端点 | 后端位置 | 说明 |
|------|---------|------|
| `POST /WorkOrderExecute/DownRecordMaterial` | `WorkOrderExecuteItemStatusMaterialController.cs:251` | 下料操作，可能用于 Admin/WMS |
| `POST /WorkOrderExecute/DownRecordMaterialStock` | `WorkOrderExecuteItemStatusMaterialController.cs:271` | 工位库存下料 |
| `POST /WorkOrderExecute/DownRecordMould` | `WorkOrderExecuteItemStatusMouldController.cs:88` | 模具下模 |
| `POST /ProcessFlowCard/processFlowStart` | `ProcessFlowCardController.cs:99` | Admin 流转卡开工 |
| `POST /ProcessFlowCard/processFlowEnd` | `ProcessFlowCardController.cs:119` | Admin 流转卡完工 |
| `POST /ProcessFlowCard/createProcessFlow` | `ProcessFlowCardController.cs:79` | Admin 创建流转卡 |

### Execute 前端已定义但未使用/已废弃的 API

| 函数 | 端点 | 状态 |
|------|------|------|
| `RecordStart` | `POST /WorkOrderExecute/RecordStart` | UI 调用已注释，后端控制器不存在 |
| `RecordReportV2` | `POST /WorkOrderExecute/RecordReportV2` | 已定义但无组件导入使用（V3 使用 `RecordReportV3`） |
| `ExecInfoV3` | `GET /Mes_ProcessWebApi/api/Card/ExecInfoV3` | 导入但从未调用（实际使用 `ExecuteInfoV3`） |
| `CardEntries` | `GET /Mes_ProcessWebApi/api/Card/CardEntries` | 已定义但无组件导入使用 |
| `InfoV2` | `GET /MES_ProcessWebApi/api/Card/InfoV2` | 已定义但无组件导入使用 |
| `UpdateReportType` | `POST /WorkOrderExecute/UpdateReportType` | 已定义但无组件导入使用 |
| `RecordMaterialConfirmV3` | `POST /WorkOrderExecute/RecordMaterialConfirm` | 整个函数已注释（`src/service/index.js:415-430`） |

---

## 第五部分：优先级分层

### ✅ 已完成 — 全部 24 个概念适配

| 类别 | 数量 | 概念 |
|------|------|------|
| P0 核心操作 | 4 | WorkOrder, WorkOrderTask, Equipment, QualityCheck |
| P1 高频查询+写 | 6 | Employee, WorkStation, Material, LineStockInventory, LineStockTransaction, WorkCenter |
| P2 通用查询 | 11 | BOM, BOMItem, ProcessRouting, ProcessOperation, ProcessCard, WorkOrderBOM, WorkOrderBOMItem, ProductionPreparation, InspectionPoint, QualityDefect, LineStockWarehouse |
| P3 含写操作 | 3 | Mould, Tooling, ProcessFlowCard |

### ⚠️ 待验证 — 需 E2E 测试

| 验证项 | 说明 |
|--------|------|
| POST query string 参数格式 | `RecordPause`/`RecordContinue`/`ChangeModel` 当前发送 JSON body，MES 实际接收 query string |
| Admin 端点权限 | `processFlowStart`/`createProcessFlow` 需要 Admin 权限，Agent 运行身份待确认 |
| `DownRecordMaterial` 参数格式 | 后端使用 `[FromBody]` 接收，适配器当前发送 JSON body，需确认 |

---

## 第六部分：本体扩展建议

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
| WorkOrderTask | `consumMaterial` | `RecordConsumpMaterialConfirm` | ✅ |
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
| 总概念 | 49 (48 project + 1 template extra) |
| 有 Action 的概念 | 24 |
| 已注册适配器 | **24** (全部覆盖) |
| 需验证 | POST query string 格式、Admin 权限边界 |
| 纯本体/无需适配器 | ~25 (含无 action 的概念) |
| 本体扩展已落地 | ProcessFlowCard 概念、WorkStation login/logout/getExecutionContext、WorkOrderTask 物料 action ×4、Employee 关系 ×2 |

### 修订记录

| 日期 | 修订内容 |
|------|---------|
| 2026-06-05 | 初版，基于 Admin + Execute 前端分析 |
| 2026-06-08 | 修订：交叉验证后端控制器源码，修正 RecordStart 端点分析，补充 Mould/Tooling 端点区分，补充 POST 参数格式差异，补充端点存在性交叉验证表 |
