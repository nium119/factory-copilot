# Factory Copilot

面向制造业的 AI 智能助手系统，支持多 Agent 协作、记忆管理和语义检索。

## 项目结构

```
FactoryCopilot/
├── backend/                 # 后端服务
│   ├── app/                # 应用主目录
│   │   ├── api/           # API路由
│   │   ├── core/          # 核心模块(配置、日志、异常、中间件)
│   │   ├── models/        # 数据模型
│   │   ├── repositories/  # 数据访问层
│   │   ├── services/      # 业务服务
│   │   │   ├── agents/    # Agent 实现(待扩展)
│   │   │   └── memory/    # 记忆系统
│   │   └── tools/         # 工具集
│   ├── data/              # SQLite 数据库文件
│   ├── logs/              # 日志目录
│   ├── requirements.txt   # Python依赖
│   └── .env              # 环境配置
└── frontend/              # 前端应用
    ├── src/
    │   ├── components/   # React组件
    │   ├── services/     # API服务
    │   └── App.jsx       # 主应用
    ├── package.json      # Node依赖
    └── vite.config.js    # Vite配置
```

## 技术栈

### 后端
- **FastAPI**: 现代高性能 Python Web 框架
- **SQLAlchemy**: ORM 数据库操作
- **LangChain / LangGraph**: LLM 调用和 Agent 编排
- **DashScope**: 通义千问模型服务
- **SQLite + aiosqlite**: 轻量级数据库（含向量存储）
- **Loguru**: 日志管理

### 前端
- **React 18**: UI 框架
- **Ant Design 5**: 企业级 UI 组件库
- **Vite**: 构建工具
- **Axios**: HTTP 客户端
- **Mermaid**: 图表渲染

## 核心特性

### 1. 三层记忆系统
- **短期记忆**: 滑动窗口保留最近 50 条完整消息
- **摘要压缩**: 旧消息由 LLM 压缩为 ~500 字摘要，存入数据库
- **长期记忆**: SQLite 向量存储，语义检索（余弦相似度）

### 2. Agent 架构
- 基于 LangGraph ReAct 模式
- 支持深度思考（thinking 模式）
- 支持联网搜索（Qwen 内置 / DuckDuckGo）
- 工具调用可扩展（MES API、数据查询等）

### 3. 多模型支持
- 阿里云百炼（Qwen 系列）
- DeepSeek
- OpenAI（GPT 系列）

### 4. 前端特性
- SSE 流式响应
- Markdown 渲染
- 深色/浅色主题
- Mermaid 图表

## 快速开始

### 后端启动

```bash
cd backend

# 创建虚拟环境
python -m venv venv

# 激活虚拟环境
venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt

# 启动服务
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

后端服务: http://localhost:8000
API 文档: http://localhost:8000/docs

### 前端启动

```bash
cd frontend

# 安装依赖
npm install

# 启动开发服务器
npm run dev
```

前端服务: http://localhost:3000

## 环境配置

后端环境变量配置文件: `backend/.env`

主要配置项:
- `AGENT_MODEL`: 使用的模型名称（qwen3.6-plus）
- `DASHSCOPE_API_KEY`: 通义千问 API 密钥
- `MEMORY_ENABLED`: 是否启用长期记忆
- `MAX_HISTORY_LENGTH`: 短期记忆保留条数
- `SUMMARY_MAX_TOKENS`: 摘要最大字数

## 记忆系统

```
用户发消息
  ↓
检索长期记忆(向量) → 注入系统提示词
加载短期记忆(最近50条 + 旧消息摘要)
调用 LLM → 流式返回
  ↓
保存消息到数据库
异步存储向量到 conversation_memory 表
```

## 许可证

MIT License
