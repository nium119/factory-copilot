# FC Agent 能力开放框架 — 实施计划

> 对应方案：`docs/agent-loop-l2.md`（四层架构）
> 状态：待评审
> 原则：每个阶段独立可验证、可单独上线；P0 是后续所有阶段的前置（写操作治理必须先建好，能力才能放开）

---

## 0. 总览

### 0.1 阶段依赖

```
P0 统一写操作治理入口 ──┬──► P1 动态 Skill（并行）
   （前置，最先做）      ├──► P2 反思循环（并行）
                        └──► P3 MCP 进 loop（依赖 P0 + P2）
                                 └──► P4 多 agent 协作（最后）
```

### 0.2 里程碑

| 里程碑 | 内容 | 完成标志 |
|---|---|---|
| M1 | P0 治理入口就绪 | MCP 写操作无审批缺口，未注册工具默认拒绝 |
| M2 | P1 + P2 | 动态 skill 可配置 + agent 能边做边调整 |
| M3 | P3 | MCP 工具进 loop 且写操作受控 |
| M4 | P4 | 多 agent 主从协作 + 责任可追溯 |

### 0.3 工作量（人日估算）

| 阶段 | 估算 | 风险 |
|---|---|---|
| P0 | 2-3 | 低 |
| P1 | 5-8 | 中 |
| P2 | 5-8 | 中 |
| P3 | 3-5 | 中 |
| P4 | 8-12 | 高 |

---

## P0 统一写操作治理入口（前置，最先做）

**目标**：堵住 MCP / 动态 skill 写操作绕过审批的缺口，让"能力放开"具备前提。**不涉及 agent loop，独立上线。**

### T0.1 MCP 工具写操作声明

现状：`MCPTool`（`app/mcp/client.py:14`）只有 name/description/input_schema，无读写声明；连接时统一注册 risk=READ（`client.py:178`）。

改动：
- `MCPTool` 增加 `risk` 字段（READ/WRITE_AUDIT/WRITE_APPROVE/CRITICAL），默认 READ
- MCP server 配置支持声明工具风险：`backend/.env` 的 `MCP_SERVERS` 或 DB `mcp_servers` 表增加 `tool_risks: {tool_name: risk}` 字段
- `_register_mcp_tools_to_safety`（`client.py:170`）优先用声明值，无声明默认 READ
- `api/mcp.py` 工具列表接口透传 risk（前端可视化）

验证：配置某 MCP 工具 risk=WRITE_APPROVE → `GET /api/mcp/tools` 返回该 risk；`TOOL_SAFETY` 中该工具为 WRITE_APPROVE。

### T0.2 MCP 写操作审批拦截

现状：`execute_structured_async` MCP 分支（`action_executor.py:348` 附近）RBAC 后直接调 mcp_registry，无审批。

改动（`app/services/action_executor.py` MCP 分支）：
- 执行前查 `TOOL_SAFETY.get(tool_name).risk`
- `WRITE_APPROVE / CRITICAL` → `ApprovalManager.create_approval_request`（复用 `guardrails.py:178` 逻辑）→ 返回 `{"needs_approval": True, "approval_id": ...}`
- `WRITE_AUDIT` → 执行 + AuditLogger
- 审批通过的回执处理：复用现有 approval 回调链路（`api/approval.py`）

验证：调用 risk=WRITE_APPROVE 的 MCP 工具 → 返回 needs_approval 且生成审批记录；READ MCP 工具直接执行。

### T0.3 动态 skill 写操作治理占位

动态 skill 未实现前先定接口：写类 skill 必须映射到已建模 action（走 rule_engine）或声明 risk（走 ApprovalManager），**不允许裸写**。P1 实现时强制。

### T0.4 前端审批展示支持 MCP 写操作

现状：`api/approval.py` 按 action_key 处理审批。MCP 工具审批的 action_key 为 `mcp_{server}_{tool}`，前端 PendingApprovalView 需展示来源（MCP 服务器名 + 工具名 + 参数摘要）。

改动：`api/approval.py` 增加 mcp_ 前缀 action_key 的显示信息映射；前端审批卡展示工具来源。

### T0.5 未注册工具默认拒绝 ✅（已完成）

`safe_tool_call`（`guardrails.py:161`）已改为默认拒绝，纳入本阶段回归测试。

### P0 验证

1. 单元：MCP READ / WRITE_AUDIT / WRITE_APPROVE 三路径行为正确
2. 集成：配置一个测试 MCP 写工具 → 触发 → 审批弹窗 → 通过后执行、审计有记录
3. 回归：现有本体 action 写路径（RBAC + rule_engine）行为不变

---

## P1 动态 Skill（与 P2 并行）

**目标**：能力可运行时配置，不依赖本体推送链路。

### T1.1 数据模型

- 新表 `agent_skills`：name / display_name / description / type(concept_query|aggregate|transform) / concept / param_schema(JSON) / implementation(JSON) / risk / enabled
- `app/models/` + `app/core/startup.py` ensure_database 幂等建表

### T1.2 CRUD API

- `app/api/` 新增 skills 路由：`GET/POST/PUT/DELETE /skills`
- 生效机制：写库后 reload 内存注册表（仿 `agent_config.reload()`）

### T1.3 声明式执行器

- `app/services/skill_service.py`：`execute(skill, params)` 按 implementation.kind 分派
  - `cypher_template` → 只读 Neo4j 查询（参数化，模板校验禁止多语句）
  - `aggregate` → 聚合查询
  - `map_to_action` → 映射到本体 action（写操作唯一路径，走 P0 治理）
- 校验：param_schema 校验参数；模板白名单校验

### T1.4 skill 接入统一治理

- skill 注册到统一工具目录（ToolRegistry），写类（map_to_action）强制走 P0 治理入口
- 只读 skill 无需审批，但记录审计（调用轨迹）

### T1.5 前端可视化配置

- 新面板：skill 列表 + 编辑（name/desc/type/params/template）+ 启用开关
- 展示调用统计（复用行为数据埋点）

### P1 验证

1. 建一个 cypher_template skill → 前端配置 → 立即生效可查询
2. 非法模板（多语句/注入）被拒
3. 写类 skill 必须映射 action，否则不可创建

---

## P2 反思循环（与 P1 并行）

**目标**：DynamicPlanner 从"计划定死"升级为"计划 + 反思纠错"。

### T2.1 循环结构改造

现状：`dynamic.py:328` `for step in steps` 顺序执行。
改动：改为 `while` 循环，每步执行后进入反思判定，可重入/折返。

### T2.2 反思判定

- 新增 `_reflect(current_step_result, remaining_steps, context) -> 决策`：
  - `NEXT`：继续下一步
  - `REFINE`：调整查询条件重试（join key 未命中 / 结果过大 / 空结果），返回调整后的参数
  - `REQUEST_INFO`：执行中反问用户（补充条件）
  - `SUMMARY`：汇总输出
- LLM 调用复用 `_get_configured_model("decision_model")`，带超时（15s）

### T2.3 REFINE 实现

- 空结果 → 放宽条件或换过滤字段；结果过大 → 增加过滤/分页
- 重试参数写入 context，最多 2 轮（见 T2.5）

### T2.4 REQUEST_INFO 实现

- 中途反问：yield 内容 + 暂停循环；用户回复后（带对话历史）恢复（复用 `dynamic.py:145` 追问规则）

### T2.5 收敛控制

- 总步数上限（保留 MAX_STEPS=6 或放宽到 8）
- **无进展计数**：连续 2 轮 REFINE 无实质变化 → 强制 SUMMARY
- 每步反思最多 2 轮；单步超时（如 30s）→ 跳过该步继续
- 全部收敛原因记录审计（正常 / 步数上限 / 无进展）

### T2.6 SSE think/refine 事件

- `dynamic.py` 新增 chunk 类型：`think`（反思过程流式，灰字）/ `refine`（纠错标记 + 原因）
- `chain_engine._execute_dynamic`（`chain_engine.py:1046`）转发
- `messages.py` 事件分发（`messages.py:1489` chain_step 旁）新增处理

### T2.7 前端渲染

- `MessageItem.jsx`：think 灰字流式显示、refine 步骤标记（🔧 调整条件）、REQUEST_INFO 为输入框（复用现有反问）

### T2.8 埋点

- `tracking.py` `track_dynamic_steps` 扩展：记录反思轮数、REFINE 次数、收敛原因（供行为数据页分析 loop 质量）

### T2.9 fast path 保留

- 简单问题（单概念查询/明确对象）跳过反思，走原"计划定死"路径省 token
- 判定：LLM 在计划时输出 `fast: true/false` 或按步骤数（≤2 步不反思）

### P2 验证

1. 构造多跳查询 → 观察反思轨迹（think/refine 事件流）
2. 空结果场景 → REFINE 自动调条件
3. 无进展 → 强制收敛不卡死
4. 行为数据页显示 loop 反思统计

---

## P3 MCP 进 loop（依赖 P0 + P2）

**目标**：MCP 工具成为 agent loop 可自主调用的能力，写操作受控。

### T3.1 工具选择器含 MCP

- 统一工具目录（ToolRegistry）加载 MCP 工具（`mcp_registry.get_tool_names()`），loop 计划/反思时可选
- MCP 工具以 `mcp_{server}_{tool}` 形式进规划提示词

### T3.2 MCP 写操作治理（复用 P0）

- loop 选中 MCP 写工具 → 走 T0.2 审批拦截 → verify 阶段（若有 verify_target）
- 无 verify_target 的 MCP 写操作：审批通过即执行 + 审计（不强制 verify，但记录）

### T3.3 前端

- MCP 配置页工具风险可视化（T0.1 已做）+ loop 执行 MCP 工具的步骤展示

### P3 验证

1. loop 中自然语言触发 MCP 查询工具
2. MCP 写工具在 loop 中必须审批，拒绝"自主跳过"

---

## P4 多 agent 协作（最后）

**目标**：A2A 主从编排，子 agent 能力受主 agent 治理。

### T4.1 主从编排协议

- 主 agent 分解子任务 → 派发（现有 A2A 外部 agent + 内部 agent_agents）
- 子任务结果聚合 → 主 agent 汇总
- 定义子任务超时 / 失败重试 / 结果 schema

### T4.2 写操作责任模型

- 子 agent 只读查询 + 上报写请求；主 agent 统一走 P0 治理入口
- 子 agent 不允许独立触发写操作（执行端强制：子 agent 环境只暴露只读工具）

### T4.3 协作审计

- AuditLogger 扩展：`谁派发 → 谁执行 → 谁复核` 全链 trace（含子任务结果摘要）

### T4.4 前端协作可视化

- 协作过程树（主 agent 分支 → 子 agent 结果）SSE 展示

### P4 验证

1. 复杂任务拆分子 agent 并行 → 汇总
2. 子 agent 写操作被强制走主 agent 审批
3. 审计可回放完整协作链

---

## 执行顺序建议

1. **P0 立即开工**（2-3 人日，独立上线，安全前提）
2. P1 与 P2 并行（可分别由不同人负责）
3. P3 在 P0+P2 完成合入后开始
4. P4 评估后再定（价值最不确定，先出设计不实现）

## 关键风险提示

- **P0 的 MCP 写操作识别**依赖工具声明（T0.1），需在 MCP server 配置层面约定，纯启发式不可靠
- **P2 反思的 Token 成本**：每步多一次 LLM 调用，需 fast path + 收敛控制兜底
- **P4 不建议在 P1-P3 未稳定前动工**，避免治理模型频繁变更
