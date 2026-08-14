# Factory Copilot

本体驱动（Ontology-Driven）的制造业 MES AI 助手（璟岩MES AI智能体），基于 FastAPI + React。本体由 **OntoStudio**（独立仓库 `Ontology-Graph/`）建模并推送到 Neo4j，FC 启动时**动态编译本体**生成 Agent、Skill 与分析链，支持 LLM 语义路由、多业务域协作、三层记忆系统和 SSE 流式响应。

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
│   │   ├── intent_router.py        # 意图路由（L2 LLM 语义分类）+ 参数提取
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

| 层级 | 技术 |
|------|------|
| 后端框架 | FastAPI + LangChain + LangGraph |
| 数据库 | Neo4j（业务数据/本体）+ SQLite（会话/元数据/向量） |
| LLM | 通义千问 (DashScope) / DeepSeek |
| 前端 | React 18 + Ant Design 5 + Vite 5 |
| 图表 | ECharts + Mermaid + Marked |
| CI/CD | Gitea Actions |

## Agent 系统（编译器驱动）

Agent **不是固定角色化配置**，而是由 `compile_and_register()` 从 Neo4j 本体动态编译：

1. **OntologyCompiler**（`compiler/compile.py`）：读取本体概念 → 每个概念生成 `{Concept}_query` Skill + 关联 actions → 按业务域配置分组 → Agent
2. **业务域配置**（`/chains/compile/namespace/{name}`）：DB 存储 domains（`{agent: {display_name, concepts[]}}`），决定哪些 Skill 进哪个 Agent
3. **切换本体图谱**：前端「业务配置」页下拉切换 namespace → 只编译预览 → 点「全部应用」才刷新 `AGENT_DEFINITIONS` 和意图路由，对话切换到新业务域

### 意图路由（L2 LLM 语义分类）

```
用户消息 → 触发词匹配 → RAG 混合召回（向量 + BM25）→ L2 LLM 语义分类
  → 约束输出防幻觉（UNSUPPORTED / NONE / 低置信不硬猜）
  → 参数提取（正则优先 → 实体解析 → LLM 填槽回退）
  → 写操作人机确认（内联确认 / 角色委托审批）
  → action_executor → DataBackend → LLM 格式化
```

L1 关键词匹配已废弃（中文口语误判率高），仅辅助保留。

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

| event | data | 说明 |
|-------|------|------|
| `agent_info` | `{"agent_name", "confidence"}` | 路由结果 |
| `thinking` | 文本 | 思考过程 |
| `content` | 文本片段 | 流式响应 |
| `route_l2` | `{"candidateCount", "concepts"}` | 意图识别候选 |
| `route_match` | `{"method", "tool", "confidence"}` | 匹配工具 |
| `param_extract` | `{"params", "tool"}` | 参数提取 |
| `confirm_required` | `{"tool", "params", "param_schema"}` | 等待人工确认 |
| `tool_start` / `tool_result` | 执行状态 / 查询结果 | 工具执行 |
| `format_start` | `{}` | LLM 格式化 |
| `execution_done` | `{"method", "tool"}` | 执行完成 |
| `error` | 错误信息 | 异常 |
| `done` | （空） | 流结束 |

## 配置

`backend/.env` 主要配置项：

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `API_PORT` | 后端端口 | `9004` |
| `AGENT_MODEL` | LLM 模型 | `qwen3.6-plus` |
| `DASHSCOPE_API_KEY` | 通义千问 API 密钥 | — |
| `NEO4J_URI` | Neo4j 连接 | `bolt://localhost:7687` |
| `NEO4J_NAMESPACE` | 项目命名空间 | `manufacturing` |
| `MEMORY_ENABLED` | 是否启用长期记忆 | `true` |
| `MAX_HISTORY_LENGTH` | 短期记忆条数 | `50` |

## 许可证

MIT License
