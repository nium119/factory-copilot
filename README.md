# Factory Copilot

面向制造业的 AI 多智能体协作框架，基于 FastAPI + React，支持关键词路由、多 Agent 协作、三层记忆系统和 SSE 流式响应。当前 Agent 工具返回模拟数据，为接入真实 MES API 预留接口。

## 快速开始

### 后端

```bash
cd backend
python -m venv venv
venv\Scripts\activate          # Windows
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8001
```

### 前端

```bash
cd frontend
npm install
npm run dev                    # http://localhost:3000，/api 代理到 :8001
npm run build                  # 生产构建 → dist/
```

### 测试

```bash
cd backend
pytest tests/ -v --tb=short
```

## 项目结构

```
├── backend/app/
│   ├── api/                  # FastAPI 路由（薄层，参数校验）
│   ├── services/             # 业务编排（记忆注入→路由→流式）
│   ├── agents/               # 智能体系统（核心）
│   │   ├── base.py           # 通用主循环（_standard_process）
│   │   ├── router.py         # 关键词路由（<10ms）
│   │   ├── collaborator.py   # 多 Agent 并发编排
│   │   ├── guardrails.py     # 安全护栏 + 审批
│   │   ├── planner.py        # 任务规划
│   │   ├── evaluator.py      # 响应质量评估
│   │   └── {domain}.py × 9   # 领域 Agent
│   ├── models/               # SQLAlchemy ORM + Pydantic
│   ├── repositories/         # CRUD 层
│   ├── core/                 # 配置 / 模型选择 / 中间件
│   ├── mcp/                  # MCP 协议集成骨架
│   └── migrations/           # DB 种子 + Alembic
├── frontend/src/
│   ├── components/
│   │   ├── ChatInterface.jsx       # SSE 流式渲染
│   │   ├── AgentSidebar/           # Agent 列表
│   │   └── ConversationDrawer/     # 历史会话
│   ├── services/                   # API 封装
│   └── stores/                     # Context 状态管理
├── .gitea/workflows/ci.yml         # Gitea Actions CI
└── CLAUDE.md                       # AI 开发指南
```

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端框架 | FastAPI + LangChain |
| 数据库 | SQLite + aiosqlite + 向量存储 |
| LLM | 通义千问 (DashScope) / DeepSeek |
| 前端 | React 18 + Ant Design 5 + Vite |
| 图表 | ECharts + Mermaid + Marked |
| CI/CD | Gitea Actions |

## Agent 系统

### 9 个领域 Agent

| Agent | 显示名 | 领域 |
|-------|--------|------|
| `general` | 智能助手 | 通用 AI（网络搜索、企业查询） |
| `scheduling` | 排产助手 | 排产查询、产能分析 |
| `quality` | 质检助手 | 质检报告、缺陷分析、SPC |
| `equipment` | 设备助手 | 设备状态、故障诊断、OEE |
| `inventory` | 线边仓助手 | 库存查询、缺料预警 |
| `process` | 工艺助手 | 工艺路线、参数、优化建议 |
| `production_prep` | 生产准备助手 | 产前准备检查 |
| `andon` | 安灯助手 | 异常上报、停线、升级处理 |
| `workstation` | 工位终端助手 | 工单开工/完工、生产报工 |

### 模板方法模式

Agent 主循环由框架提供，领域定制仅需覆盖 `call_tools()`：

```python
class BaseAgent(ABC):
    name: str
    display_name: str
    system_prompt: str

    async def _standard_process(message, ...):
        # 1. 自动深度思考判断
        # 2. 调用领域工具 call_tools()
        # 3. 推理框架注入 _get_reasoning_framework()
        # 4. LLM 流式输出
        async for chunk in llm_service.chat_stream(...):
            yield chunk

    async def call_tools(self, message) -> Optional[str]:
        """子类覆盖：调用领域工具"""
        return None
```

子类示例（~50 行）：

```python
class SchedulingAgent(BaseAgent):
    name = "scheduling"
    display_name = "排产助手"
    system_prompt = "你是制造业排产专家..."

    async def call_tools(self, message):
        return await scheduling_tool.query(message)
```

### 路由：关键词 + LLM 双模式

```
用户消息 → AgentRouter.route(message)
  → ROUTING_METHOD = "keyword"：匹配 agent_config.py 关键词，<10ms
  → ROUTING_METHOD = "llm"：LLM 语义理解，0.5-3s
  → 默认回退 general Agent
```

### 协作模式

触发词："整体情况"、"综合分析"、"全面"、"协作" — 并发查询排产 + 设备 + 质检 + 库存，LLM 聚合综合报告。

## 记忆系统（三层）

```
第一层 短期记忆：DB 最近 50 条消息
       ↓ 超过 50 条时
第二层 摘要压缩：LLM 将旧消息压缩为 ~500 字摘要
       ↓ 持久化
第三层 长期记忆：向量存储（DashScope text-embedding-v3）
       检索：余弦相似度 >0.7
       去重：相似度 >0.95
```

## SSE 流式协议

| event | data | 说明 |
|-------|------|------|
| `agent_info` | `{"agent_name", "confidence"}` | 路由结果 |
| `thinking` | 文本 | 思考过程 |
| `content` | 文本片段 | 流式响应 |
| `collab_start` | `{"agent_count"}` | 协作开始 |
| `collab_agent` | `{"agent", "status"}` | 协作状态 |
| `collab_done` | `{"agent", "result"}` | 协作完成 |
| `error` | 错误信息 | 异常 |
| `done` | （空） | 流结束 |

## 配置

`backend/.env` 主要配置项：

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `AGENT_MODEL` | LLM 模型 | `qwen3.6-plus` |
| `ROUTING_METHOD` | 路由策略 `keyword` / `llm` | `keyword` |
| `DASHSCOPE_API_KEY` | 通义千问 API 密钥 | — |
| `MEMORY_ENABLED` | 是否启用长期记忆 | `true` |
| `MAX_HISTORY_LENGTH` | 短期记忆条数 | `50` |

## 如何适配新领域

1. 复制通用核心模块（base/router/collaborator/guardrails/memory 等）到新项目
2. 写 Agent 子类 — 每个 ~50 行：`name` + `system_prompt` + `call_tools()`
3. 写 `agent_config.py` — 关键词表 + 领域定义
4. 写 `prompts.py` — 领域提示词
5. 写 `tools/*.py` — 对接真实 API（替换 mock）
6. 配置模型 — `model_config.py` 和 `.env`
7. 复制前端 — 替换品牌/Agent 列表/提示词

**核心约束**：不碰 `_standard_process`、不换关键词路由、保留 mock 回退。

## 许可证

MIT License
