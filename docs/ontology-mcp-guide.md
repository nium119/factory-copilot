# FC 使用本体 MCP 工具 — 操作手册

> 通过 OntoStudio 的 MCP Server，FC 对话可直接查询**已发布本体**（概念/action 签名/规则/审批配置）。
> 接口细节见 `Ontology-Graph/docs/ontology-api.md`；本文只讲 FC 侧怎么用。

## 架构一图流（HTTP 传输，推荐）

```
FC 对话（自然语言）
  → 意图路由选中 mcp_ontology_* 工具
  → FC 后端 MCPHttpClient（HTTP+SSE，无子进程、无本地脚本）
  → OntoStudio /api/mcp（SSE 传输端点）
  → ontology_api_service（只读，Neo4j 已发布元数据，ETag 缓存）
  → 结果 JSON 回传 → LLM 格式化 → 对话渲染
```

前置条件：OntoStudio 后端（9003）在线且版本含 `/api/mcp` 端点（2026-08-31 后的代码）。
HTTP 模式 FC 侧**不需要任何本地文件**——无脚本副本、无绝对路径、本地/服务器注册内容完全一致。

## 管理员：注册（一次性）

**方式 A · API（推荐，`url` 字段非空即走 HTTP 传输）**：

```bash
curl -X POST http://127.0.0.1:9004/api/mcp/servers \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "ontology",
    "url": "http://172.21.10.22:9003/api/mcp",
    "command": "",
    "description": "OntoStudio 本体只读工具",
    "enabled": true
  }'
# 然后连接并应用
curl -X POST http://127.0.0.1:9004/api/mcp/servers/ontology/connect
curl -X POST http://127.0.0.1:9004/api/mcp/servers/apply
```

**方式 B · 前端表单**：业务配置页 → MCP 管理 Tab → 「添加 MCP 服务器」。
前端表单暂无独立 URL 输入框（待补），先用方式 A 注册；列表中的连接/断开/应用按钮
对两种传输通用，连接成功后展开可见工具清单。

**备选 · stdio 传输**（OntoStudio 对 FC 无 HTTP 可达性、或 Claude Desktop 类同机客户端时）：
`command=python`，args 第一个参数是脚本**本地绝对路径**——服务器容器内
`/app/mcp/ontology_mcp_server.py`；本地 Windows 用 FC 仓库副本
`D:\code\long-running-agent-harness\projects\factory-copilot\backend\mcp\ontology_mcp_server.py`；
再加 `--api-base http://<host>:9003/api`。
⚠️ stdio 第一个参数必须是本地文件路径，**不能填 URL**；该模式需要 FC 侧维护脚本副本
（源文件在 `Ontology-Graph/backend/mcp/`，改任何一份必须同步另一份，文件头有标注）。

**注册后可用工具**（FC 内全名带 `mcp_ontology_` 前缀，两种传输完全一致）：

| 工具 | 用途 |
|---|---|
| `list_namespaces` | 列出已发布本体 |
| `list_concepts` | 概念摘要清单 |
| `search_concepts` | **中文关键词搜概念**（"工单"、"物料"） |
| `get_concept` | 单概念完整定义（属性/关系/action 签名/规则/数据权限） |
| `list_actions` | action 签名（含 requiresConfirmation、审批规则关联） |
| `list_rules` | 规则清单（含审批门禁 approvalRoles） |
| `get_snapshot` | 整图快照（大，LLM 按需查询优先前几个工具） |

## 使用者：对话里怎么问

不需要任何特殊指令，自然语言即可。示例：

- 「本体里有哪些和**工单**相关的概念？」→ search_concepts
- 「WorkOrder 有哪些属性和操作？哪些操作需要确认？」→ get_concept
- 「哪些操作配置了审批？审批角色是谁？」→ list_rules / list_actions
- 「数量超过 200 的审批规则是怎么配的？」→ list_rules

执行轨迹（tool_start/tool_result）会显示 `mcp_ontology_xxx`，结果由 LLM 整理成表格。

## 管理员：试调工具（验证链路是否可用）

`POST /api/mcp/servers/{name}/call` 经 FC 的 MCP 注册表直调一个工具，全链路验证（不需要对话）：

```bash
# 元数据查询（只读）
curl -X POST http://127.0.0.1:9004/api/mcp/servers/os/call \
  -H "Authorization: Bearer <JWT>" -H "Content-Type: application/json" \
  -d '{"tool": "search_concepts", "arguments": {"namespace": "manufacturing", "keyword": "工单"}}'

# action 执行（写操作也走这条，OntoStudio 侧需配 FC_SERVICE_TOKEN）
curl -X POST http://127.0.0.1:9004/api/mcp/servers/os/call \
  -H "Authorization: Bearer <JWT>" -H "Content-Type: application/json" \
  -d '{"tool": "execute_action", "arguments": {"tool": "WorkOrder_query", "params": {}, "namespace": "manufacturing"}}'
```

tool 传短名（`search_concepts`）或完整名（`mcp_os_search_concepts`）均可；返回 `{"ok", "tool", "result"}`。
前端「MCP 管理」页同款能力（工具展开试调）后续可接此端点。

## 数据新鲜度

- 事实源是 **Neo4j 已发布本体**：OntoStudio 推送 Schema 后缓存自动失效，FC 下次查询即最新；
- 未推送的编辑中内容**不会**出现（设计如此：发布才对外）。

## 故障排查

| 现象 | 排查 |
|---|---|
| HTTP 模式「连接」失败 | 先 `curl http://<host>:9003/api/v1/namespaces` 验证 OntoStudio 在线；再验证版本含 `/api/mcp`（`curl -H "Accept: text/event-stream" http://<host>:9003/api/mcp` 应收到 `event: endpoint`）——旧版本需重启 OntoStudio 加载 |
| stdio 模式「连接」失败 | FC 侧手动跑 `python <脚本> --api-base <地址>`，喂一行 `{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}` 看是否响应；检查 python 与脚本路径 |
| 工具调用报「无法连接 / 会话已失效」 | HTTP：SSE 长连接被代理掐断（心跳 15s 一般够）或 OntoStudio 重启过 → 点「连接」重连即可；stdio：检查 api-base 地址与 OntoStudio 启动状态 |
| 工具存在但对话不选中 | 意图路由未命中：给工具配触发词（MCP Tab → 工具展开 → 触发词，如"本体/概念/schema"），或确认点过「全部应用」 |
| 查询结果缺刚改的本体 | OntoStudio 是否已**推送 Schema**（保存 ≠ 发布）；API 缓存最长 30s |

## 治理说明（7 个只读 + 1 个执行）

7 个元数据工具只读：本体写入只走 OntoStudio 建模界面（单一编辑入口 + 审计），FC 侧永远不直接改本体——与「理解归 LLM，执行归确定性」的边界一致。

`execute_action` 是**业务数据执行工具**（用户拍板「能力优先，治理后补」）：直通执行、跳过人机确认/审批门禁，但保留 JWT 鉴权、RBAC、constraint 校验；上生产前必须补门禁（详见 `Ontology-Graph/docs/ontology-api.md` execute_action 节）。
