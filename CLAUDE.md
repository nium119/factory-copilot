# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

**Factory Copilot**（璟岩MES AI智能体）是基于 FastAPI + React 构建的制造业 AI 助手，支持多智能体协作、长期记忆向量检索和语义搜索。当前所有 Agent 工具返回**模拟数据**，为后续接入真实 MES API 预留了接口。

## 快速开始

### 后端
```bash
cd backend
# 创建虚拟环境（首次）
python -m venv venv
venv\Scripts\activate          # Windows
pip install -r requirements.txt

# 启动开发服务（默认 8000 端口，.env 配置为 8001）
uvicorn app.main:app --reload --port 8001
```

Windows 一键启动：`start.bat`

### 前端
```bash
cd frontend
npm install
npm run dev                    # 开发服务 3000 端口，/api 代理到 :8001
npm run build                  # 生产构建 → dist/
```

### 测试
```bash
cd backend
pytest tests/test_conversation_integration.py -v
```

### 关键文件
| 文件 | 说明 |
|------|------|
| `backend/.env` | 当前配置（API Key、模型选择、端口） |
| `backend/.env.example` | 配置模板 |
| `start.bat` | Windows 一键启动脚本 |
| `stop.bat` | 终止所有 python.exe 进程 |
| `nginx.conf` | 生产环境反向代理配置（含 SSE 支持） |
| `DEPLOYMENT.md` | Windows 生产部署指南 |

## 架构

### 后端 (`backend/app/`)

**入口**：`main.py` → `create_app()` 注册 5 个路由组：`health`、`chat`（`/api`）、`conversations`（`/api/conversations`）、`messages`（`/api`）、`memory`（`/api`）。当 `frontend/dist` 存在时挂载为静态 SPA。

**两条并行的流式路径**：
- `/api/chat/stream` — 旧路径，使用 `agent_service` + 内存历史
- `/api/messages/stream` — 生产路径，使用 `MessageService`，含 DB 持久化、记忆注入和向量存储

**核心模块**：
```
backend/app/
├── api/                  # FastAPI 路由处理
├── core/
│   ├── config.py         # Pydantic 配置（从 .env 读取）
│   ├── model_config.py   # LLM 供应商配置（通义千问/DashScope、DeepSeek）
│   └── prompts.py        # 各 Agent 域的系统提示词
├── models/
│   ├── agent.py          # Agent 数据模型（agents 表）
│   ├── schemas.py        # Pydantic 请求/响应模型
│   └── conversation.py, message.py  # SQLAlchemy ORM 模型
├── repositories/         # CRUD 层（会话、消息、Agent）
├── services/
│   ├── llm_service.py           # LangChain ChatOpenAI 流式调用
│   ├── message_service.py       # 核心编排：记忆检索、历史加载、Agent 路由、持久化
│   ├── conversation_service.py  # 会话 CRUD + 自动生成标题 + 向量清理
│   ├── agent_service.py         # 旧版工具检测（正则匹配）
│   └── vector_memory_service.py # SQLite 向量存储（余弦相似度、DashScope 嵌入）
├── agents/               # 多智能体框架（见下方 Agent 系统）
└── tools/                # 通用工具（SearchTool、EnterpriseTool 通过网页抓取）
```

**数据库**：SQLite + `aiosqlite`（`data/agent.db`）。表：`conversations`、`messages`、`agents`、`conversation_memory`。迁移：Alembic 用于会话表，`init_agents.py` 独立脚本用于 Agent 种子数据。

### Agent 系统 (`backend/app/agents/`)

**9 个领域 Agent**，在 `__init__.py` 中通过 `importlib` 懒加载注册：

| Agent | 显示名 | 领域 |
|-------|--------|------|
| `general` | 智能助手 | 通用 AI（网络搜索、企业查询） |
| `scheduling` | 排产助手 | 排产查询、产能分析 |
| `quality` | 质检助手 | 质检报告、缺陷分析、SPC |
| `equipment` | 设备助手 | 设备状态、故障诊断、OEE |
| `inventory` | 线边仓助手 | 库存查询、缺料预警 |
| `process` | 工艺助手 | 工艺路线、参数、优化建议 |
| `production_prep` | 生产准备助手 | 产前准备检查（物料/设备/工装/质量标准/SOP） |
| `andon` | 安灯助手 | 异常上报、停线、升级处理 |
| `workstation` | 工位终端助手 | 工单开工/完工、生产报工、签到、自检 |

**核心组件**：
- `base.py` — `BaseAgent` 抽象类，`process()` 产出 SSE 元组，`build_system_prompt()` 合并基础提示词 + 记忆上下文
- `router.py` — **仅关键词路由**（不用 LLM，避免 10s+ 延迟）。匹配消息中的 Agent 关键词，首次匹配成功，置信度 0.85。默认回退到 `general`
- `collaborator.py` — 多 Agent 协作模式，触发词："整体情况"、"综合分析"、"全面"、"协作"。并发查询排产 + 设备 + 质检 + 库存四个 Agent
- `entity_extractor.py` — 正则提取产线名（SMT-01）、工单号（WO-2026-001）、产品名、紧急程度等

**Agent 统一流程**：`call_tools(message)`（正则意图识别）→ 将工具结果附加到消息 → 通过 `llm_service.chat_stream()` 流式输出

**工具层**（`agents/tools/`）：所有工具返回**模拟数据**，`MES_API_ENABLED = False`，`MES_API_BASE = "http://localhost:9090"` 为后续真实 MES 接入预留。存在跨 Agent 工具调用（如 `production_prep_tools` 调用库存/设备/质检的查询函数）。

### 记忆系统（三层）

1. **短期记忆**：从 DB 加载最近 `MAX_HISTORY_LENGTH`（50）条消息
2. **摘要压缩**：超过 50 条时，LLM 将旧消息压缩为约 500 字摘要，缓存在 `conversations.summary` 字段
3. **长期记忆**：SQLite 向量存储，DashScope `text-embedding-v3` 嵌入模型，Python 内计算余弦相似度，0.95 去重阈值

### 前端 (`frontend/`)

**技术栈**：React 18 + Ant Design 5 + Vite 5 + Axios + ECharts + Mermaid + Marked

**布局**（`App.jsx`）：双栏可拖拽分割 — `AgentSidebar`（左）+ `ChatInterface`（中）。`ConversationDrawer` 从右侧滑出管理历史会话。

**核心组件**：
- `ChatInterface.jsx` — SSE 流式渲染（100ms 节流）。事件类型：`agent_info`、`thinking`、`content`、`collab_start`、`collab_agent`、`collab_done`、`error`、`done`。功能：@ 提及选 Agent、模型选择器、协作模式开关、流式 Markdown、可折叠思考过程、中止生成
- `AgentSidebar/` — Agent 列表（图标/颜色/描述），从 API 加载
- `ConversationDrawer/` — 历史面板（搜索、批量删除、编辑标题、分页）
- `MarkdownRenderer.jsx` — 流式模式：纯文本。完成模式：完整 GFM Markdown，含 ECharts（懒加载）和 Mermaid（懒加载，解析失败回退原始代码）

**状态管理**：`stores/ConversationContext.jsx` — React Context + useReducer，当前会话 ID 持久化到 `localStorage`（`fc_current_conversation_id`）。

**Services**：
- `services/request.js` — Axios 实例，带鉴权拦截器，30s 超时
- `services/messageService.js` — 主流式路径（fetch + ReadableStream，支持 `agent_name`、`conversation_id`）
- `services/chatService.js` — 旧流式路径
- `services/conversationService.js` — 会话 CRUD 封装

**Vite 代理**：`/api` → `http://127.0.0.1:8001`。SSE 代理头：`X-Accel-Buffering: no`、`Cache-Control: no-cache`。

## SSE 协议

流式响应使用 Server-Sent Events，带 `event` 字段区分类型：

```
event: agent_info    data: {"agent_name": "scheduling", "confidence": 0.85}
event: thinking      data: 推理过程文本
event: content       data: 响应文本片段
event: collab_start  data: {"agent_count": 4}
event: collab_agent  data: {"agent": "scheduling", "status": "done"}
event: collab_done   data: {"agent": "scheduling", "result": "..."}
event: error         data: 错误信息
event: done          data: （空）
```

## 注意事项

- **模拟优先设计**：Agent 工具当前返回模拟数据。接入真实 MES 需设置 `MES_API_ENABLED = True` 并配置 `MES_API_BASE`
- **关键词路由是有意设计**：LLM 路由已故意禁用以保障性能，模糊消息回退到 `general`
- **数据库迁移不匹配**：Alembic 迁移 `001_add_conversation_tables.py` 使用 PostgreSQL UUID 类型，但实际运行在 SQLite 上（`String(36)` UUID）。表创建实际通过 `scripts/init_db.py` 的 `create_all` 完成
- **`chatService.js` 是旧代码**：主流式路径是 `messageService.sendMessageStream()`，支持 `agent_name` 和 `conversation_id` 参数
- **`workstation_tools.py` 导入缺失**：在 `tools/__init__.py` 的 `__all__` 中列出，但缺少 `from . import workstation_tools` 语句
