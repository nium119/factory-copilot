# FC Agent 能力开放框架设计方案（自主执行 + 动态 Skill + MCP + 多 Agent 协作）

> 状态：草案，待评审
> 背景：提出对标 Codex / Claude Code 型 agent 的能力方向，拆解为四点诉求：**自主执行、动态配置 skill、支持 MCP、多 agent 协作**。
> 本方案结论：**能力接入层开放（动态 skill / MCP / A2A），执行边界统一治理（RBAC + rule_engine 审批 + verify + 复核 + 审计）**。不做通用编程 agent，不放开文件/shell 自由操作。

---

## 1. 背景与目标

### 1.1 诉求拆解

| 诉求 | 本质 | 判断 |
|---|---|---|
| 对标字面："做成 Codex/Claude Code" | 想要 agent 更自主、能连续干活 | 诉求内核合理，字面方案（通用编程 agent）不合理 |
| 自主执行 | 未建模场景能自己规划、调整、完成 | ✅ 合理，当前最大缺口 |
| 动态配置 skill | 能力可运行时配置，不依赖本体推送链路 | ✅ 合理，但要声明式、分级 |
| 支持 MCP | 接入标准协议的外部工具能力 | ✅ 合理，FC 已有基础设施 |
| 多 agent 协作 | 多个 agent 分工完成复杂任务 | ✅ 合理，FC 已有 A2A 雏形 |

### 1.2 核心结论

**"能做什么"由能力层决定（开放、可配置），"能不能执行、怎么兜底"由治理层决定（强制、分级）**。二者必须分离——这是本框架与"通用编程 agent"的根本区别。

### 1.3 明确不做

- ❌ 通用编程能力（文件系统 / shell / git / 浏览器自由操作）
- ❌ 无限自主 agent（反思轮数有上限、工具白名单分级）
- ❌ 写操作绕过治理（任何写/高风险工具必须走 verify + 复核/自动回滚）

---

## 2. 现状核查（FC 已有地基）

| 能力 | 现状 | 代码位置 |
|---|---|---|
| 工具安全分级 | ✅ TOOL_SAFETY：READ / WRITE_AUDIT / WRITE_APPROVE / CRITICAL | `agents/settings/guardrails.py:10` |
| 统一工具安全包装 | ✅ `safe_tool_call`：分类→审批→执行→审计→脱敏 | `agents/guardrails.py:143` |
| MCP 接入 | ✅ 连接 + 工具自动注册到 TOOL_SAFETY | `mcp/client.py:170` |
| MCP 路由 | ✅ intent_router 加载 MCP 工具名、action_executor 可执行 | `intent_router.py:230` |
| 动态 skill | ❌ 编译产物（本体/链编译出 AtomicSkill/CompositeSkill），不可运行时配置 | `agents/compiler/compile.py:32` |
| 多 agent | ✅ AGENT_DEFINITIONS 从 DB 加载、可运行时 reload | `agents/agent_config.py:37-53` |
| A2A 外部 agent | ✅ CRUD 端点 + 运行时注册 | `api/a2a_agents.py` |
| 自主执行 | ◐ DynamicPlanner 受限 ReAct（计划定死、无反思循环） | `agents/compiler/dynamic.py:170,328` |
| action 写治理 | ✅ RBAC(authorized_roles) + `rule_engine.evaluate_all`（violations 拦截 / approvals 审批） | `services/action_executor.py:292,439` |
| 遗留产线 agent | ⚠️ `_safe_call` / TOOL_SAFETY 路径，**无调用方（死代码）**，真实主路径走 action | `agents/andon.py` `workstation.py` |

### 2.1 已识别的安全缺口 ⚠️

**缺口 1（真实，能力开放后暴露）——MCP 工具写操作绕过规则审批**：
`execute_structured_async` 的 MCP 分支（`sig.source == "mcp"`）在 RBAC 检查后直接调 mcp_registry，**不经过 `rule_engine.evaluate_all`**。而 MCP sig 的 `authorized_roles` 通常为空 → 写类 MCP 工具执行时无审批。**动态 skill 若挂到 action 路径但不建模 rule，同样绕开审批。**

**缺口 2（真实后门，但处于遗留路径）——`safe_tool_call` 未注册直接放行**：
`guardrails.py:161` 工具未在 TOOL_SAFETY 注册时 `直接通过`。逻辑上是后门，但该路径（产线 agent）无调用方，实践影响低。仍应修复为默认拒绝（通用正确性）。

**结论**：能力开放（MCP / 动态 skill）前，必须先把**统一写操作治理入口**建好，否则写操作会绕过审批。

---

## 3. 目标架构：四层

```
┌─────────────────────────────────────────────────────────┐
│  编排层   链引擎(L1, 确定性)   +   agent loop(L2, 自主+反思)  │
├─────────────────────────────────────────────────────────┤
│  能力层   动态 skill(声明式)  +  MCP 工具  +  本体 action    │
├─────────────────────────────────────────────────────────┤
│  协作层   A2A 多 agent（主从编排，子 agent 能力走主 agent 治理）│
├─────────────────────────────────────────────────────────┤
│  治理层   RBAC + rule_engine 审批 + verify_target + 复核    │
│           + 自动回滚 + 审计（统一写操作治理入口，强制不可绕过）│
└─────────────────────────────────────────────────────────┘
```

---

## 4. 统一工具注册层（核心设计）

动态 skill、MCP 工具、本体 action **统一进一个工具注册表**，代理在 loop 里看到的是同一份工具目录：

```
统一工具注册表（ToolRegistry）
├─ 动态 skill   声明式定义：name / display_name / description / 参数schema / 执行实现
├─ MCP 工具     协议连接自动注册（FC 已有）
└─ 本体 action  建模推送（现有）
        │ 统一登记能力元数据（含写操作治理要求）
        ▼
   agent loop 调度；写操作一律走统一治理入口
```

### 4.1 动态 Skill（Phase A 重点）

**声明式工具，不做任意代码**。定义存 DB（新表 `agent_skills`），运行时热更新：

```yaml
skill:
  name: workorder_summary          # 概念查询封装 / 统计 / 自定义只读
  display_name: 工单汇总
  description: 按状态汇总工单数量与耗时
  type: concept_query | aggregate | transform   # 只读类型
  concept: WorkOrder
  param_schema:                    # 复用现有 SkillParam
    - name: status
      label: 状态
      type: string
      required: false
  implementation:                  # 声明式，非代码
    kind: cypher_template | python_template | map_to_action
    template: |
      MATCH (n:WorkOrder) WHERE $status IS NULL OR n.status = $status
      RETURN n.status AS status, count(*) AS cnt
```

- **好处**：摆脱"必须本体建模 + 推送"才能有新能力的链路，运营/工程师在前端可视化配置即可
- **边界**：type 仅只读（concept_query/aggregate/transform）；写类能力必须映射到已建模的 Action，走治理
- **与 MCP 的关系**：skill 是内部声明式工具，MCP 是外部协议工具，两者进同一注册表、同一治理入口

### 4.2 MCP 分级接入

- 现状已支持连接 + 自动注册 TOOL_SAFETY（`mcp/client.py:170`）
- 增量：MCP 工具进 **agent loop 自主调度**（现在只走 intent_router 直接命中）
- **写操作治理缺口**：MCP 工具走 action_executor 时绕过 `rule_engine` 审批（见 §2.1 缺口1）——**必须先接入统一写操作治理入口，才能放开进 loop**
- 前端 MCP 配置页可视化工具风险等级（`api/mcp.py` 已有 tools/risk 列表接口）

### 4.3 本体 action

- 保持建模推送链路，作为写操作的**唯一合法来源**（写操作不能来自动态 skill 任意定义）

---

## 5. 自主执行：agent loop（Phase A）

从"计划一次定死"（`dynamic.py:328` 静态 for）升级为"计划 + 反思纠错循环"：

```
Phase 0  计划     LLM 输出初始步骤（复用 _plan_steps，保留 fast path）
   ↓
Phase 1  循环体
          ├─ 选工具：从 ToolRegistry 白名单（按 risk 分级可见）选
          ├─ 执行：确定性（编译 skill / 注册表工具 / action executor）
          ├─ 反思：LLM 观察结果，判定
          │      NEXT          → 继续下一步（携带上下文 + join key 过滤）
          │      REFINE        → 调整查询条件重试（joinOn 未命中、结果过大）
          │      REQUEST_INFO  → 执行中反问用户
          │      SUMMARY       → 汇总输出（若有写 → verify）
          └─ 收敛：步骤上限 / 连续 2 轮无进展 / 用户确认
   ↓
Phase 2  验证     写操作 → verify_target → 失败走复核/自动回滚（复用现链路）
```

**关键差异**：每步执行后 LLM **看结果再决定下一步**，而不是计划定死硬走。

---

## 6. 多 Agent 协作（A2A）

### 6.1 现状

- 多个 agent：`AGENT_DEFINITIONS` 从 DB 加载（`agent_agents` 表），运行时 reload
- 外部 agent：A2A CRUD + 运行时注册（`api/a2a_agents.py`）

### 6.2 协作模式（建议主从编排，非对等自由调用）

```
主 agent（统筹）
  ├─ 分解子任务 → 派发 A2A 子 agent / 内部 agent
  ├─ 收子任务结果 → 汇总
  └─ 写操作：子 agent 只上报执行请求，主 agent 统一走 verify + 复核
```

### 6.3 责任模型（治理重点）

| 角色 | 写操作责任 |
|---|---|
| 子 agent | 只执行/上报，不独立落盘写操作 |
| 主 agent | 统一调度写操作，走 verify_target + 复核 |
| 审计 | 记录"谁派发→谁执行→谁复核"全链（AuditLogger 扩展协作 trace） |

**原则**：协作不放松治理——子 agent 的写操作不能绕过主 agent 的治理链路，避免"外包给子 agent 就没人管"。

---

## 7. 治理底线（必须强制，不可选）

治理重心是**统一写操作治理入口**，而非依赖 TOOL_SAFETY（遗留路径）：

1. **统一写操作治理入口**：任何能力（本体 action / MCP / 动态 skill）的写/删操作，一律强制过 RBAC → `rule_engine` 审批（violations 拦截 / approvals 审批）→ 执行 → verify_target → 失败复核/自动回滚。**MCP 与动态 skill 必须先接入此入口，不允许绕过**（修复 §2.1 缺口1）
2. **默认拒绝未分级工具**：修复 `guardrails.py:161`——未在安全表注册的工具一律拒绝，不再"直接通过"（遗留路径的正确性修复，非主路径）
3. **写/高风险强制治理**：写工具在 loop 中必须：执行前确认 → verify_target → 失败复核/自动回滚。禁止"自主执行跳过确认"
4. **多 agent 责任归属**：子 agent 写操作归主 agent 治理（见 §6.3）
5. **全量审计**：含反思轨迹、REFINE 原因、协作派发链

---

## 8. 分阶段实施计划

| 阶段 | 内容 | 风险 | 工作量 |
|---|---|---|---|
| **P0 统一治理入口** | MCP/动态 skill 写操作接入 rule_engine 审批 + 未注册工具默认拒绝（遗留路径修复） | 低 | 小 |
| **P1 动态 Skill** | skill 声明式建模 + DB 存储 + 热更新 + 只读执行 | 中 | 中 |
| **P2 反思循环** | agent loop：NEXT/REFINE/REQUEST_INFO/SUMMARY + 收敛控制 | 低 | 中 |
| **P3 MCP 进 loop** | MCP 工具自主调度 + 接入统一写操作治理入口 | 中 | 中 |
| **P4 多 agent 协作** | A2A 主从编排 + 责任模型 + 协作审计 | 高 | 大 |

**P0 应立即做**（现有安全缺口）；P1/P2 可并行；P3/P4 需前面稳定后。

---

## 9. 决策点（需评审确认）

1. **动态 skill 的 type 边界**：只读（concept_query/aggregate/transform）？还是允许受限写模板（映射到已建模 action）？建议先只读。
2. **反思轮数上限**：建议 2 轮/步 + 无进展计数，是否可配置？
3. **fast path 保留**：简单问题是否走"计划定死"省 token？建议保留。
4. **MCP 工具治理模式**：写类 MCP 工具如何接入 rule_engine 审批（映射到规则 / 显式声明 risk）？建议写类工具必须有声明式风险才放行，否则默认拒绝。
5. **多 agent 协作范围**：内部 agent 协作（现有 agent_agents）+ 外部 A2A 都做？建议先内部后外部。
6. **Token 成本**：loop + 协作成本上升，是否对 loop 用 budget 模型降级？

---

## 10. 风险与应对

| 风险 | 应对 |
|---|---|
| 能力放开后被绕过治理 | 统一写操作治理入口（RBAC + rule_engine + verify/复核）强制 + 审计全记录 |
| 反思死循环 | 轮数上限 + 无进展计数 + 超时收敛 |
| 动态 skill 质量失控 | 声明式 schema 校验 + 前端可视化 + 变更审计 |
| 多 agent 写操作失责 | 子 agent 只上报、主 agent 统一治理 |
| 延迟/Token 成本上升 | budget 模型降级 + fast path + 并行 |
| 动态 skill 与编译 skill 冲突 | 编译 skill 优先，动态 skill 显式标记来源 |

---

## 11. 与对标诉求的对应

| 对标诉求 | 本框架对应 |
|---|---|
| 一句话自主干活 | agent loop 自主规划 + 反思调整（P2） |
| 能接入更多能力 | 动态 skill + MCP（P1/P3） |
| 多个 agent 一起干活 | A2A 主从协作（P4） |
| 连续执行、过程可见 | SSE think/refine 流式展示 |
| 不依赖人工配链 | 未建模场景 loop 兜底，能力可运行时配置 |

**核心差异（坚持的边界）**：不放开文件/shell/任意工具；写操作永远走治理；"能做什么"由能力层决定但"能不能执行"由治理层强制。这是护城河，也是安全边界。
