# FC（Factory Copilot）项目状态

> 更新时间：2025-06-29　分支：`feature/flat-menu-sidebar`　最新提交：`d31e933`（已 push 并同步部署）
> 服务器：`root@172.21.10.22:/home/websites/factory-copilot`，容器 `factory-copilot`，健康检查 `/health` → `{"status":"healthy","neo4j":"connected"}`

## 一、总目标

对齐 DSH（DeepSeek Harness）的核心理念：**理解归 LLM，执行归确定性**。

- LLM 负责：意图理解、参数抽取、澄清提问的生成、结果解读。
- 确定性代码负责：规则校验、权限、审批/确认门禁、工具执行、快照回滚。

## 二、当前进展（截至 d31e933）

### 1. 统一 Agent Loop 重构（backend/app/agents/）
- 拆分出独立模块：`planner.py`（规划）、`loop.py`（主循环）、`reflector.py`（反思）、`router.py`（路由）、`collab.py`（协作）、`graph_engine.py`（图引擎）、`governance.py`（治理）。
- `base.py` 仍为总入口，`_execute_and_reflect` 重写：**先 preflight，后 tool_start，再执行**。

### 2. 确定性治理管线（governance.py，新增 305 行）
`GovernancePipeline` 六道门禁，`pre_execute()` 在任何工具执行前返回：
`{blocked, reason, violations, inferences, approvals, risk, report}`。
门禁顺序：tool_boundary → rbac → data_permission → rule_constraint → risk → approval。

### 3. 审批/确认前移（对齐 DSH pre-execute 语义）— 本轮核心
- **确认（requiresConfirmation）与审批（requiresApproval）是两个独立门禁**，互不替代，均由本体配置驱动：
  - `WorkOrder_create/delete/update.requiresConfirmation = true`；`startProduction/suspend/close = false`。
  - `Rule.requiresApproval + approvalRoles=["系统管理员"]`（quantity > 200 规则）。
- 流程修正为：**preflight（规则+实体解析）→ 需确认则 confirm_required → 需审批则 rule_engine 事件（无 tool_start）→ 通过后 tool_start → 执行写入**。审批绝不再发生在工具执行之后。
- 端到端验证过的场景：
  - 创建工单 quantity=10：1 次确认 → 直接写入（参数缺失不再误触发审批）。
  - 删除工单：preflight 结果被 execute 复用（不重复求值）。
  - 创建工单 quantity=400：确认 → rule_engine:0（无 tool_start）→ 审批通过 → tool_start → 写入成功，结果回传不再丢失。

### 4. 规则引擎修复（rule_engine.py）
- 参数缺失（`left_val is None`）置 `_unresolved=True` 并返回 None——**缺失 ≠ 条件成立**，根除了 quantity=10 误报审批。
- 已验证：10 / "10" / 缺失 均不触发；201 / 300 正常触发审批。

### 5. 执行器输出契约（action_executor.py）
- 新增 `preflight(tool_name, arguments, user_id)`：实体富化（resolve_entity）+ 规则 evaluate_all，不产生任何写操作。
- `execute_structured_async(..., preflight=...)` 复用预检结果。
- 统一返回：`content`（给人看的文本）+ `value`（`{rowCount, records, actionType, created_entity_id, before_snapshot}`，给 LLM/程序消费）。

### 6. 审批链路与角色
- "管理员"角色按超级管理员处理：`needs_delegate=False`，可 inline 直接审批（解决 approvalRoles="系统管理员" 本地无成员的问题）。
- 审批通过后重新执行带 `_skip_approval=True`（防无限循环），且先 yield tool_start 再执行，`out["tool_result"]` 回传结果。
- 测试角色集：`_TEST_USER_ROLES = {"admin": "管理员", "EMP-001": "操作工", "EMP-010": "车间主任"}`。

### 7. 澄清交互升级（对齐 DSH ask_user_question）
- `resolve_clarify(session_id, reply, cancelled, selected, custom)`：优先确定性选项（selected），其次 custom，最后 reply 自由文本。
- 前端 `ClarifyTakeoverBar`：点选项提交 `{selected:[label]}`，文本提交 `{custom}`；`/api/messages/clarify` 支持。

### 8. 测试与文档
- 新增单测：governance / loop / planner / reflector / graph_engine / tool_registry / scheduling / collab / compound / dialog_regression（约 1300 行）。
- 新文档：`docs/unified-agent-loop-architecture.md`（统一循环架构）。
- 前端：`MessageItem` 拆分瘦身、`ExecutionOrbit` 执行可视化增强。

## 三、关键决策记录

| # | 决策 | 理由 |
|---|------|------|
| 1 | 确认与审批是两个独立门禁，都由本体配置（requiresConfirmation / requiresApproval）驱动 | "审批归审批，如果没配审批但配了确认，确认也需要"；配置即事实，代码不硬编码 |
| 2 | 所有门禁（确认/审批/规则拦截）发生在 tool_start **之前** | DSH pre-execute 语义：宁可没开始，不可做一半 |
| 3 | 参数缺失 ≠ 条件成立，不触发审批 | 宁可漏报不可误拦；缺失信息走澄清，不走审批 |
| 4 | preflight 与 execute 分离，execute 可复用 preflight 结果 | 一次求值两处使用，避免"预检时通过、执行时又变"的不一致 |
| 5 | 输出契约 value/content 分离 | value 给 LLM 与程序消费，content 给人渲染，互不纠缠 |
| 6 | "管理员"视为超级管理员，inline 审批不走委托 | 本地 Role 节点为空，approvalRoles 无成员会导致审批死锁 |
| 7 | 审批通过重执行带 `_skip_approval=True` 且先 yield tool_start | 防审批无限循环；保证前端执行轨迹完整 |
| 8 | LLM 不接触执行细节，只产出意图+参数，执行全部走确定性管线 | 理解归 LLM，执行归确定性 |

## 四、核心文件清单

**后端（本轮改动核心）**
- `backend/app/agents/governance.py` — 新增，治理管线六道门禁
- `backend/app/agents/base.py` — 主入口：preflight→门禁→tool_start→执行 的完整链路
- `backend/app/agents/planner.py` / `loop.py` / `reflector.py` / `router.py` / `collab.py` / `graph_engine.py` — 统一循环拆分
- `backend/app/services/action_executor.py` — preflight + 执行 + value/content 契约
- `backend/app/services/rule_engine.py` — 参数缺失语义修复
- `backend/app/services/tool_registry.py` / `scheduling_service.py` / `attribution_service.py` / `impact_judger.py` — 新增服务
- `backend/app/api/messages.py` — clarify 支持 selected/custom
- `backend/tests/test_governance.py` 等 10 个新测试文件

**前端**
- `frontend/src/components/ChatInterface.jsx` — ClarifyTakeoverBar、审批/确认交互
- `frontend/src/components/ChatInterface/MessageItem.jsx` + `.css` — 消息渲染拆分
- `frontend/src/components/ChatInterface/ExecutionOrbit.jsx` + `.css` — 执行可视化

**文档**
- `docs/unified-agent-loop-architecture.md` — 统一 Agent Loop 架构说明

## 五、下一步计划

1. **（一+二期已落地 2026-08-31，OntoStudio 侧；execute_action 于 09-01 落地）本体项目 API 化 + MCP**：
   - **REST**：只读 Schema API `/api/v1/namespaces/{ns}/...`（concepts/actions/rules/snapshot，ETag+304，推送后自动失效缓存，输出脱敏）；
   - **MCP 双传输**（工具同名同参同语义，8 工具 = 7 只读 + execute_action）：**HTTP/SSE `GET /api/mcp`（远程集成推荐，注册只填一个 URL）** + stdio `backend/mcp/ontology_mcp_server.py`（同机备选，FC 仓库带副本）；
   - **execute_action（用户拍板「能力优先，治理后补」）**：MCP 直通执行本体 action——FC 新增执行网关 `app/api/actions.py`（`GET /api/actions` 签名清单 + `POST /api/actions/execute` 直通执行 `_skip_approval`），OntoStudio 侧转发（`FC_API_BASE`/`FC_SERVICE_TOKEN`）；跳过人机确认/审批门禁，保留 JWT 鉴权/RBAC/constraint 校验 + namespace 409 防呆 + MCP 回环 400 拒绝；**上生产前必须补门禁**（审计落盘、审批接入、白名单）；
   - **FC 侧已支持**：`MCPHttpClient`（HTTP/SSE 传输）+ `mcp_servers` 的 `url` 字段（模型加列+幂等迁移）+ 前端 MCPDrawer URL 输入框 + **试调端点 `POST /api/mcp/servers/{name}/call`**（经 FC 注册表直调工具，全链路验证用）；本地已按 url 模式注册 `os` 并验证连接/8 工具/全部应用；
   - **验证**：OntoStudio 单测 38 项、FC 全量 233 passed/1 skipped；execute_action 端到端（HTTP/SSE→FC 网关→Neo4j：Material_query 真实查询、WorkOrder_create 直通创建→回读→delete→回读 0 清理、缺必填被 constraint 挡且 ok=false）均通过；**经 FC 全链路**（FC API `/mcp/servers/os/call`→mcp_registry→MCPHttpClient→OntoStudio→回转 FC 网关→Neo4j：search_concepts 命中 11 概念、WorkOrder_query rowCount=6、create→回读 1→delete 清理）通过；
   - 文档：`Ontology-Graph/docs/ontology-api.md`（接口含 execute_action 节）、`factory-copilot/docs/ontology-mcp-guide.md`（FC 操作手册）、`DEPLOY-LINUX.md` §4.9（部署含 FC_SERVICE_TOKEN 配置）。
   **剩余**：② 变更事件推送（`ontology.published`，现为 etag/304 轮询）；② `ontology_service` 改读 `/api/v1`（吃狗粮，可选）；② 本地 9003 需再重启一次才有 `/api/mcp`（SSE 端点代码在用户上次重启之后写入）；② execute_action 生产门禁（审计/审批接入/白名单）；② 同步服务器（需用户确认，含 FC 生成服务 JWT + OntoStudio .env 配置）。
2. **审批委托链路补全**：非管理员触发审批时走"待审批列表→委托审批人处理"的完整闭环（当前管理员 inline 审批已通）。
3. **（已修 2026-09-01）CHAT 闲聊与业务域脱节**：「你好/你有什么功能」走 `_execute_chat`（base.py）原只用 `DEFAULT_SYSTEM_PROMPT` 通用话术（echarts/翻译/网络搜索，其中网络搜索/企业信息查询还是虚构能力）——现注入 `_chat_capability_section()` 动态能力清单（本体 action 签名 + 概念中文标签 + 需确认标注 + 禁止声称虚构能力），活体验证「你有什么功能」返回 41 类业务对象真实操作清单；`ANALYSIS_MONITOR` 提示词同步删除虚构能力行。
3. **门禁覆盖率**：GovernancePipeline 的 data_permission / risk 两道门当前为占位实现，需接真实数据权限模型与风险评分。
4. **回归测试扩充**：把端到端验证过的三个场景（quantity=10 直写 / delete 复用 preflight / quantity=400 审批链）固化为自动化测试。
5. **同步与部署**：后续改动沿用 `bash ./sync.sh`（前端构建→tar→scp→docker restart），改服务器数据前必须先问。

## 六、环境速查

| 项 | 值 |
|---|---|
| FC 后端 | http://127.0.0.1:9004（uvicorn，无 --reload，改码需杀进程重启） |
| FC 前端 | React18 + AntD5 + Vite5，由后端托管 `frontend/dist` |
| MES 登录 | admin / `123!@#` / CN10，token 存 localStorage `__SRMC_Config_token` |
| 本体规模 | 59 concepts / 82 actions / 244 mappings，namespace=manufacturing |
| SSE 协议 | `data: {"type": X, "content": "<json字符串>"}`，真实载荷在 content 内需二次 json.loads |
| 测试 token | `jwt.encode({"EmpCode":"admin","LoginUserName":"admin","exp":...}, JWT_SECRET, "HS256")` |

## 附：本体 API 化建议（OntoStudio 对非 FC 下游开放）

**定位**：本体项目只做"事实源"，不做"执行器"。API 分两层，均不暴露 FC 的编译产物（Skill/Agent 编译格式是 FC 私有实现，禁止耦合）。

**第一层：Schema API（只读，最主要）**
- `GET /api/v1/namespaces/{ns}/concepts` — 概念清单（含 label/描述/属性/关系）
- `GET /api/v1/namespaces/{ns}/concepts/{concept}` — 单概念完整定义（属性、关系、action 签名、requiresConfirmation/requiresApproval、规则）
- `GET /api/v1/namespaces/{ns}/actions` — 全量 action 签名（输入/输出 schema）
- `GET /api/v1/namespaces/{ns}/rules` — 约束/推理/审批规则
- `GET /api/v1/namespaces/{ns}/snapshot?format=json|json-schema` — 整图快照，供下游一次性拉取 + 本地缓存
- 响应带 `etag`/`version`，支持 `If-None-Match` 304，下游增量轮询成本低

**第二层：变更事件（Webhook / 变更流）**
- 本体发布（push 到 Neo4j）时发 `ontology.published` 事件：`{namespace, version, changed_concepts[], timestamp}`
- 下游（FC 或其他系统）据此失效缓存、重新编译——FC 现在是被动轮询，改事件驱动后多消费者天然解耦

**不建议做的**：
- ❌ 通过 API 直接写本体数据（写仍走 OntoStudio 建模界面，保证单一编辑入口与审计）
- ❌ 暴露 Neo4j Cypher 透传（下游会耦合存储实现）
- ❌ 暴露 FC 编译后的 Agent/Skill 格式（那是 FC 的内部产物，换了下游就没意义）

**实施顺序**：先做 Schema API 的 concepts/actions/snapshot 三个只读端点（一天内可完成，FC 自己也能用——把 `ontology_service` 从直读 Neo4j 改为读 API，吃自己的狗粮），变更事件二期再做。
