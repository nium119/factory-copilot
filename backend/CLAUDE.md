# CLAUDE.md

Factory Copilot — 制造业 AI 助手后端。

## 架构

```
app/
├── agents/          # 业务域系统（router → domain）
├── api/             # FastAPI 路由
├── core/            # 配置、中间件、prompts、启动
├── services/        # 核心服务
│   ├── action_executor.py   # 工具执行：本体驱动 Cypher 生成 + DataBackend
│   ├── data_backend.py      # 数据抽象层：Neo4j → Api → Sqlite 降级链
│   ├── intent_router.py     # L2 LLM 语义路由 + 中文人名提取
│   ├── ontology_service.py  # 本体缓存（从 Neo4j 加载）
│   ├── neo4j_service.py     # Neo4j 驱动
│   ├── rule_engine.py       # 规则引擎
│   └── llm_service.py       # LLM 调用 + 流式
├── models/          # Pydantic 数据模型
└── repositories/    # 数据访问
```

## 数据流

```
OntoStudio → push schema+data → Neo4j → Factory Copilot Agent 查询
```

- **数据来源**：Neo4j（由 OntoStudio 推送），不再依赖 SQLite 或本地 YAML
- **本体**：从 Neo4j 加载（`ontology_service`），15 个概念，9 个 Action
- **查询**：`DataBackend.query()` → `Neo4jBackend` 生成 Cypher → Neo4j
- **列头**：查询结果根据本体属性定义（label）生成中文列头，LLM 格式化表格
- **跨概念**：通过 FK 属性（如 `employeeId`）过滤，fallback 到图遍历 `-[*1..2]-`

## 端口

- Factory Copilot: `9001`
- OntoStudio 后端: `9003`
- OntoStudio 前端: `5003`

## 启动

```bash
cd backend
uvicorn app.main:app --port 9001 --host 127.0.0.1
```

## 关键设计决策

- **不再有 seed_data.py**：数据全由 OntoStudio push 到 Neo4j
- **不再有本地 YAML**：本体模板文件在 OntoStudio 项目
- **降级链**：`Neo4j → Api → Sqlite`（`DATA_BACKEND` 配置控制）
- **LLM 格式化**：查询结果带 `[列头]` 行，LLM 据此生成表格
