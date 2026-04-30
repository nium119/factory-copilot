# Factory Copilot — 项目架构文档

## 1. 概述

Factory Copilot 是一个**领域无关的多智能体协作框架**，当前以 MES（制造执行系统）为落地场景。后端 FastAPI + LangChain，前端 React 18 + Ant Design 5，数据库 SQLite + 向量存储。

**核心理念**：Agent 主循环由框架提供（`BaseAgent._standard_process`），领域定制仅需覆盖 `call_tools()`。

| 指标 | 数据 |
|------|------|
| 后端模块 | 85 个 Python 文件，~1.4 MB |
| 前端组件 | 27 个 JSX/JS 文件，~234 KB |
| 领域 Agent | 9 个（排产/质检/设备/库存/工艺/生产准备/安灯/工位终端/KPI监控） |
| 数据库表 | conversations / messages / agents / conversation_memory / feedback |
| 提交历史 | 46 个 commit |

---

## 2. 架构分层

```
┌──────────────────────────────────────────────┐
│  api/           FastAPI 路由（薄层，参数校验）  │
├──────────────────────────────────────────────┤
│  services/      业务编排（记忆注入→路由→流式）  │
├──────────────────────────────────────────────┤
│  agents/        智能体系统（核心）              │
│  ├─ base.py           通用主循环              │
│  ├─ router.py         关键词路由              │
│  ├─ collaborator.py   多Agent并发编排          │
│  ├─ guardrails.py     安全护栏 + 审批         │
│  ├─ planner.py        任务规划                │
│  ├─ evaluator.py      响应质量评估            │
│  ├─ reflection/       自我修正                │
│  └─ 9个领域Agent      领域定制（hot spot）     │
├──────────────────────────────────────────────┤
│  models/        SQLAlchemy ORM + Pydantic     │
│  repositories/  CRUD 层                       │
├──────────────────────────────────────────────┤
│  core/          配置 / 模型选择 / 中间件       │
│  mcp/           MCP 协议集成骨架              │
│  migrations/    DB 种子 + Alembic             │
└──────────────────────────────────────────────┘
```

### 请求路径

```
用户消息 → /api/messages/stream
  → MessageService.process_message_stream()
    → 加载历史 + 记忆检索（vector_memory_service）
    → AgentRouter 关键词匹配
    → Agent.process() → _standard_process()
      → should_deep_think()        自动判断深度思考
      → call_tools()               调用领域工具
      → build_system_prompt()      组装提示词
      → llm_service.chat_stream()  SSE 流式输出
```

---

## 3. 通用层 vs 领域层

### 3.1 通用核心（跨领域可复用）

| 模块 | 职责 | 领域耦合 |
|------|------|----------|
| `agents/base.py` | `_standard_process` 主循环、熔断重试、推理框架 | 零 |
| `agents/router.py` | 关键词意图路由（非 LLM，<10ms） | 关键词表来自 `agent_config.py` |
| `agents/collaborator.py` | 多 Agent 并发查询 + 结果聚合 | 零 |
| `agents/guardrails.py` | 工具安全分级 + `safe_tool_call()` 包装 | 零 |
| `agents/approval.py` | HITL 审批流（停线/变更/升级） | 审批场景配置来自 `settings/guardrails.py` |
| `agents/error_handler.py` | 错误分类 + 指数退避 + 熔断器 | 零 |
| `agents/evaluator.py` | 响应质量评估 + 优化建议 | 评估标准来自 `settings/evaluation.py` |
| `agents/planner.py` | 多任务规划 + 依赖排序 | 任务定义来自 `settings/evaluation.py` |
| `agents/prioritization.py` | 协作任务优先级排序 | 零 |
| `services/vector_memory_service.py` | 向量嵌入 + 余弦相似度 + 去重 + 摘要压缩 | 零 |
| `services/message_service.py` | 流式编排：记忆→路由→Agent→持久化 | 零 |
| `services/llm_service.py` | LangChain ChatOpenAI 封装 | 模型配置来自 `model_config.py` |
| `core/parallel_executor.py` | 并发执行 + 超时控制 | 零 |
| `core/resource_monitor.py` | 资源感知 + 并发限流 | 零 |
| `api/*.py` | FastAPI 路由 | 零 |
| `frontend: ChatInterface.jsx` | SSE 流式渲染 + 协作面板 + 审批弹窗 | 零 |
| `frontend: MarkdownRenderer.jsx` | GFM + ECharts + Mermaid 懒加载 | 零 |

### 3.2 领域层（换领域需重写）

| 模块 | 当前内容 | 替换成本 |
|------|----------|----------|
| `agents/{domain}.py` × 9 | `name` + `system_prompt` + `call_tools()` | 每个 ~50 行 |
| `agents/tools/{domain}_tools.py` × 8 | 模拟 MES API 工具函数 | 对接真实 API |
| `agents/agent_config.py` | 关键词表 + 元数据 | 替换关键词和领域定义 |
| `core/prompts.py` | 系统提示词（中文 MES） | 替换提示词 |
| `agents/settings/domain.py` | 安灯类型/工序/KPI/企业查询 | 替换领域配置 |
| `agents/settings/evaluation.py` | 排产评估标准 | 替换评估标准 |
| `agents/settings/kpi.py` | 制造 KPI 注册表 | 替换 KPI |

---

## 4. Agent 系统设计

### 4.1 模板方法模式

```python
class BaseAgent(ABC):
    name: str           # 路由标识
    display_name: str   # 展示名
    system_prompt: str  # 系统提示词

    async def process(message, ...) -> AsyncGenerator[tuple, None]:
        """子类可覆盖，默认走 _standard_process"""
        async for evt in self._standard_process(...):
            yield evt

    async def _standard_process(message, ...):
        """通用主循环 — 框架所有，子类不覆盖"""
        # 1. 自动深度思考判断
        if enable_thinking is None and self.should_deep_think(message):
            enable_thinking = True

        # 2. 调用领域工具
        tool_result = await self.call_tools(message)
        if tool_result:
            enhanced_message = f"{message}\n\n参考数据:\n{tool_result}"

        # 3. 推理框架注入（设备故障诊断 / 质检根因分析）
        reasoning = self._get_reasoning_framework(message)
        if reasoning:
            system_prompt = await self.build_system_prompt(reasoning_context=reasoning)

        # 4. LLM 流式输出
        async for chunk in llm_service.chat_stream(...):
            yield chunk

    async def call_tools(self, message) -> Optional[str]:
        """子类覆盖：调用领域工具，返回格式化文本"""
        return None

    def _get_reasoning_framework(self, message) -> str:
        """子类覆盖：注入结构化推理框架"""
        return ""

    async def reflect(self, message, response) -> Optional[str]:
        """子类覆盖：自我修正"""
        return None
```

**子类示例**（调度 Agent）：

```python
class SchedulingAgent(BaseAgent):
    name = "scheduling"
    display_name = "排产助手"
    system_prompt = "你是制造业排产专家..."

    async def call_tools(self, message):
        # 正则识别意图 → 调用排产工具 → 返回格式化文本
        return await scheduling_tool.query(message)
```

### 4.2 路由：关键词 + LLM 双模式

```
用户消息 → AgentRouter.route(message)
  → 检查 ROUTING_METHOD 配置（keyword | llm）
  → keyword 模式：匹配 agent_config.py 中的关键词，首次命中 confidence=0.85
  → llm 模式：LLM 语义理解选 Agent，支持同义词/口语化/隐含意图
  → 默认回退到 general Agent
```

关键词匹配 <10ms，LLM 路由 0.5-3s。通过 `.env` 中 `ROUTING_METHOD` 切换，方便对比准确性。

### 4.3 协作模式

触发词："整体情况"、"综合分析"、"全面"、"协作"

```
GeneralAgent._collaborate(message)
  → prioritize_agents()             优先级排序（andon > equipment > quality > ...）
  → parallel_executor.run()         并发查询 4 个 Agent
  → 聚合结果 → LLM 综合报告
```

### 4.4 注册表：DB 驱动懒加载

```python
_AGENT_REGISTRY = {
    "scheduling": "app.agents.scheduling:scheduling_agent",
    ...
}

def get_agent(name: str):
    if name in _loaded_agents:
        return _loaded_agents[name]
    module_path, attr_name = _AGENT_REGISTRY[name].split(":")
    module = importlib.import_module(module_path)
    agent = getattr(module, attr_name)
    # 从 DB 加载展示层配置覆盖
    config = _load_agent_config(name)
    _apply_db_config_to_agent(agent, config)
    _loaded_agents[name] = agent
    return agent
```

---

## 5. 记忆系统（三层）

```
第一层 短期记忆：DB 最近 50 条消息
       ↓ 超过 50 条时
第二层 摘要压缩：LLM 将旧消息压缩为 ~500 字摘要
       ↓ 持久化
第三层 长期记忆：向量存储（DashScope text-embedding-v3）
       检索：余弦相似度 >0.7
       去重：相似度 >0.95
```

---

## 6. SSE 流式协议

| event | data | 说明 |
|-------|------|------|
| `agent_info` | `{"agent_name", "confidence"}` | 路由结果 |
| `thinking` | 文本 | 思考过程 |
| `reasoning_step` | `{"key", "label", "icon"}` | 结构化推理步骤 |
| `content` | 文本片段 | 流式响应 |
| `collab_start` | `{"agent_count"}` | 协作开始 |
| `collab_agent` | `{"agent", "status"}` | 协作状态更新 |
| `collab_done` | `{"agent", "result"}` | 协作完成 |
| `eval_result` | `{"scores", "suggestions"}` | 评估结果 |
| `approval_required` | `{...}` | 审批请求 |
| `error` | 错误信息 | 异常 |
| `done` | （空） | 流结束 |

---

## 7. 前端架构

```
App.jsx
├── AgentSidebar          Agent 列表 + 历史按钮
├── ChatInterface
│   ├── ChatInputBar      @提及、模型选择、联网搜索
│   ├── MessageList
│   │   └── MessageItem   消息气泡
│   │       ├── CollabStepsPanel   协作卡片
│   │       ├── PlanStepsPanel     规划步骤
│   │       ├── ChainProgress      审批链
│   │       ├── FeedbackBar        反馈
│   │       └── MarkdownRenderer   GFM + 图表
│   ├── WelcomeScreen     空状态
│   ├── ApprovalModal     审批弹窗
│   └── EvalPanel         评估面板（ECharts）
├── ConversationDrawer    历史会话（右侧滑出）
└── ExplorerAlertDrawer   异常预警
```

**状态管理**：`ConversationContext.jsx` — React Context + useReducer，当前会话 ID 持久化到 `localStorage`。

---

## 8. 如何适配新领域

### 8.1 最小适配（保留全部通用模块）

1. **复制 8 个通用核心模块**到新项目
2. **写 Agent 子类** — 每个 ~50 行：`name` + `system_prompt` + `call_tools()`
3. **写 `agent_config.py`** — 关键词表 + 领域定义
4. **写 `prompts.py`** — 领域提示词
5. **写 `tools/*.py`** — 对接真实 API（替换 mock）
6. **配置模型** — `model_config.py` 和 `.env`
7. **复制前端** — 替换品牌/Agent 列表/提示词

### 8.2 核心约束

- **不碰 `_standard_process`** — 这是框架契约
- **关键词路由是故意设计** — 不换 LLM 路由
- **所有 Agent 工具可返回 mock** — `MES_API_ENABLED = False` 时回退模拟数据
- **数据库 SQLite** — 单文件部署，换 PostgreSQL 需改 Alembic 迁移

### 8.3 已知限制

- 无 TypeScript / mypy 类型检查
- 无 CI/CD 流水线
- 测试覆盖偏薄（39 个，集中在几个模块）
- LangChain 依赖较重（仅用于 ChatOpenAI 封装）
- 前端 ChatInterface.jsx ~800 行，`sendMessage()` 400+ 行未进一步拆分
