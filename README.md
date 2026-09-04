# Factory Copilot

本体驱动（Ontology-Driven）的通用引擎 AI 助手，基于 FastAPI + React。本体由 **OntoStudio**（独立仓库 `Ontology-Graph/`）建模并推送到 Neo4j，FC 启动时**动态编译本体**生成 Agent、Skill 与分析链：全量工具 react 循环自主决策查询/操作/反问/结束，多跳查询自动规划，缺参数主动澄清；配套三层记忆、身份数据权限（JWT claims 注入）与 SSE 流式响应。

**数据流**：OntoStudio（本体建模）→ Neo4j（图数据库）→ FC（编译 Agent）→ 用户对话。FC 只读 Neo4j，本体以 OntoStudio 为唯一数据源。

## 快速开始

### 后端（端口 9004）

```bash
cd backend
python -m venv venv
venv\Scripts\activate          # Windows
pip install -r requirements.txt
uvicorn app.main:app --port 9004
```

Windows 一键启动：`start.bat`（后端 9004 + 前端 5004）

### 前端（端口 5004）

```bash
cd frontend
npm install
npm run dev                    # http://localhost:5004，/api 代理到 :9004
npm run build                  # 生产构建（独立部署）→ dist/
npm run build:subapp           # 生产构建（子应用部署 /AI-OS/）→ dist/
```

### 测试

```bash
cd backend
pytest tests/ -v --tb=short
```

## 项目结构

```
├── backend/app/
│   ├── api/                  # FastAPI 路由（薄层，20+ 路由组）
│   ├── services/             # 业务编排（记忆→路由→执行→流式）
│   │   ├── message_service.py      # 核心编排（记忆注入→链检测→Agent→持久化）
│   │   ├── intent_router.py        # 意图入口（fast_route 确定性路由 + react 循环决策）
│   │   ├── action_executor.py      # 本体动作执行（Cypher 生成 + DataBackend）
│   │   ├── data_backend.py         # DataBackend 抽象（Neo4j/Api 降级链）
│   │   ├── ontology_service.py     # 从 Neo4j 加载本体（Concept/Action/Property/Relation）
│   │   ├── rule_engine.py          # 规则引擎（约束/推理/触发器/计算字段）
│   │   ├── multi_system_backend.py # 多系统后端（按概念路由到 MES API）
│   │   └── vector_memory_service.py # SQLite 向量存储（长期记忆）
│   ├── agents/               # 智能体系统（编译器驱动，核心）
│   │   ├── __init__.py       # compile_and_register()：编译本体生成 Agent
│   │   ├── compiler/         # OntologyCompiler：本体 → Skill → Agent → 链
│   │   ├── base.py           # BaseAgent + _standard_process 标准流程
│   │   └── router.py         # route_intent：LLM 语义路由到 Agent
│   ├── models/               # SQLAlchemy ORM + Pydantic
│   ├── repositories/         # CRUD 层
│   ├── core/                 # 配置 / 模型选择 / 链引擎 / 中间件 / prompts
│   ├── mcp/                  # MCP 协议集成
│   └── migrations/           # DB 种子 + Alembic
├── frontend/src/
│   ├── components/
│   │   ├── ChatInterface.jsx       # SSE 流式渲染 + 执行链路步骤
│   │   ├── ChainManager/           # 业务域配置 / 链条配置 / API / 向量化
│   │   ├── AgentSidebar/           # Agent 列表
│   │   └── ConversationDrawer/     # 历史会话
│   ├── services/                   # API 封装（messageService 主流式路径）
│   └── stores/                     # Context 状态管理
├── docs/                    # MES 业务分析与本体适配对照
├── skills/                  # AI 技能说明文档
└── .gitea/workflows/ci.yml  # Gitea Actions CI
```

## 技术栈


| 层级    | 技术                                |
| ----- | --------------------------------- |
| 后端框架  | FastAPI + LangChain + LangGraph   |
| 数据库   | Neo4j（业务数据/本体）+ SQLite（会话/元数据/向量） |
| LLM   | 通义千问 (DashScope) / DeepSeek       |
| 前端    | React 18 + Ant Design 5 + Vite 5  |
| 图表    | ECharts + Mermaid + Marked        |
| CI/CD | Gitea Actions                     |


## Agent 系统（编译器驱动）

Agent **不是固定角色化配置**，而是由 `compile_and_register()` 从 Neo4j 本体动态编译：

1. **OntologyCompiler**（`compiler/compile.py`）：读取本体概念 → 每个概念生成 `{Concept}_query` Skill + 关联 actions → 按业务域配置分组 → Agent
2. **业务域配置**（`/chains/compile/namespace/{name}`）：DB 存储 domains（`{agent: {display_name, concepts[]}}`），决定哪些 Skill 进哪个 Agent
3. **切换本体图谱**：前端「业务配置」页下拉切换 namespace → 只编译预览 → 点「全部应用」才刷新 `AGENT_DEFINITIONS` 和意图路由，对话切换到新业务域

### 执行架构（统一执行体，对齐 DSH 理念）

**理解归 LLM，执行归确定性代码**。消息进来先 fast_route 确定性分发（无前置路由 LLM 调用，省 ~8s），按触发条件走三条路径之一：

```
用户消息
  ├─ 命中固定链触发词 ──────▶ chain_engine 多步分析链（配置化步骤 + 汇总）
  ├─ 复杂多跳查询 ──────────▶ DynamicPlanner 规划-执行循环
  │                            ├ 首跳参数缺失 → clarify_required 反问澄清
  │                            └ 规划依赖链 → 逐步执行（joinOn 结果注入）→ 汇总
  └─ 默认 ──────────────────▶ 统一执行体（production_execution）
                               全量工具（跨业务域 query+write）react loop：
                               LLM 自主判断 查询 / 操作 / 反问 / 结束
                               ├ 写操作 → confirm_required 人机确认 + 规则门禁审批
                               ├ "当前用户"指代 → claims 身份注入守卫
                               └ 空结果 → 登录态兜底说明
  收尾：action_executor → DataBackend → LLM 格式化（表格/结论）
```

**为什么没有 LLM 语义路由**：决策层已含全量工具，路由到哪个 Agent 不影响工具选择——前置路由纯属浪费；工具选择由 react 循环的 LLM 决策完成，比预路由更准。

### 身份与数据权限链

```
登录 JWT（claims 含 user_id/工厂编码/员工号）
  → deps 验签 → _request_claims（ContextVar）+ session 缓存
  → get_user_property（别名归一化：plantcode/factorycode/工厂 → 同一属性）
  → 查询时 apply_data_filters 注入本体 DataFilter（按角色过滤行）
```

- **"当前用户"指代守卫**：消息含"当前用户/我的信息"等指代时，守卫自动用 claims 员工号覆盖查询参数，避免模糊搜索污染
- **写防御**：create 剥离内部参数（`_fuzzy`*/`_cross_*` 等），空业务数据直接拒绝（防止自动主键生成幽灵节点）

### 数据后端抽象（DataBackend）

统一业务数据访问接口（`resolve_entity()`/`query()`/`create()`/`delete()`），按概念特征自动路由：

- **Neo4jBackend** — Cypher 查询，支持图遍历/跨概念关系/规则
- **ApiBackend** — HTTP REST 调用 MES 系统
- **FallbackDataBackend** — 按需降级 Neo4j → Api

**硬边界**：所有业务数据访问必须通过 DataBackend 接口。

## 记忆系统（三层）

```
第一层 短期记忆：DB 最近 50 条消息
       ↓ 超过 50 条时
第二层 摘要压缩：LLM 将旧消息压缩为摘要
       ↓ 持久化
第三层 长期记忆：SQLite 向量存储（DashScope text-embedding-v3）
        检索：余弦相似度
        去重：相似度 > 0.95
```

## SSE 流式协议

双通道广播（`event_bus.py`）：每个事件同时发命名 event + 默认消息（`data: {"__type": ...}`），前端 `onmessage` 解析分发。核心事件分组：


| 分组  | event                                                                                                                   | 说明                                    |
| --- | ----------------------------------------------------------------------------------------------------------------------- | ------------------------------------- |
| 会话  | `agent_info` / `message_id` / `data_source`                                                                             | 路由结果（fast_route/manual）、消息落库 ID、数据源提示 |
| 思考  | `thinking` / `think` / `content`                                                                                        | 流式推理与正文片段                             |
| 规划  | `plan_start` / `plan_step` / `plan_done`                                                                                | DynamicPlanner 多跳规划步骤                 |
| 反思  | `reflection_start` / `reflection_done`                                                                                  | 计划自检                                  |
| 执行  | `tool_call` / `tool_start` / `tool_result`                                                                              | 工具调用与结果（含 rowCount/source）            |
| 并行  | `parallel_start` / `parallel_progress` / `parallel_task` / `parallel_done`                                              | 并行工具组进度                               |
| 链   | `chain_start` / `chain_step` / `chain_summary` / `chain_done`                                                           | 固定分析链执行                               |
| 交互  | `clarify_required` / `confirm_required` / `confirm_result` / `approval_request` / `approval_done` / `approval_executed` | 澄清反问、写确认、审批流                          |
| 呈现  | `exec_steps` / `blocks` / `metadata` / `action_items` / `change_plans` / `eval_result`                                  | 执行步骤时间线、内容块、行动项卡片                     |
| 结束  | `execution_done` / `query_done` / `optimization_done` / `error` / `done`                                                | 各阶段完成与异常                              |


## 配置

`backend/.env` 主要配置项：


| 变量                   | 说明                           | 默认值                     |
| -------------------- | ---------------------------- | ----------------------- |
| `API_PORT`           | 后端端口                         | `9004`                  |
| `AGENT_MODEL`        | LLM 模型                       | `qwen3.6-plus`          |
| `DASHSCOPE_API_KEY`  | 通义千问 API 密钥                  | —                       |
| `NEO4J_URI`          | Neo4j 连接                     | `bolt://localhost:7687` |
| `NEO4J_NAMESPACE`    | 本体命名空间（OntoStudio 推送侧一致）     | `manufacturing`         |
| `DATA_BACKEND`       | 数据后端（neo4j / api / fallback） | `fallback`              |
| `MES_API_BASE_URL`   | MES REST 基址（ApiBackend）      | —                       |
| `JWT_SECRET`         | 与 MES 共享的登录验签密钥              | 内置开发密钥                  |
| `MEMORY_ENABLED`     | 是否启用长期记忆                     | `true`                  |
| `MAX_HISTORY_LENGTH` | 短期记忆条数                       | `50`                    |


## 界面

对话区对齐 DSH 纯对话流风格：无角色文字标记、无头像（左右分栏 + 气泡底色区分双方），正文 900px 阅读宽度、输入区 1040px，复制按钮悬停浮现。侧栏保留 Agent 业务域列表（会话/导航入口）。

## 许可证

MIT License