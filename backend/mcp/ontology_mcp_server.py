"""OntoStudio 本体 MCP Server — stdio JSON-RPC（MCP 协议 2024-11-05）。

把 OntoStudio 的本体只读 API（/api/v1）以 MCP 工具形式暴露给 AI 下游
（Factory Copilot 或任意 MCP 客户端），实现"本体项目 API 化"的 MCP 消费形态。

设计原则：
- 纯标准库实现（json/urllib），零第三方依赖，可复制到任意有 Python 的环境运行；
- 只读透传：所有工具都是 GET 调用，本体写入仍走 OntoStudio 建模界面；
- 单一实现：工具内部调用 REST API（/api/v1），不重复造元数据解析逻辑；
- 每个响应一行 JSON（与 Factory Copilot MCPClient 的按行读取协议对齐）。

用法：
    python ontology_mcp_server.py --api-base http://127.0.0.1:9003/api
    # 或环境变量 ONTOLOGY_API_BASE；默认 http://127.0.0.1:9003/api

在 Factory Copilot 注册（前端"MCP 管理"或 API）：
    name=ontology, command=python,
    args=[<本文件绝对路径>, --api-base, http://127.0.0.1:9003/api]
    → 工具自动注册为 mcp_ontology_list_concepts 等（全部 READ 风险）
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

# Windows 下强制 UTF-8，避免中文工具描述乱码
# （pytest 等环境会替换 std 流，reconfigure 需防御性调用）
if sys.platform == "win32":
    for _stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "ontostudio-ontology"
SERVER_VERSION = "1.2.0"

# ── API 客户端 ──


class ApiClient:
    """OntoStudio 只读 API 的极薄 HTTP 客户端（stdlib urllib）。

    显式禁用系统代理：本体 API 与 MCP server 通常同机/同内网部署，
    走代理既慢又可能被劫持（urllib 默认读 HTTP_PROXY 环境变量）。
    """

    def __init__(self, api_base: str, timeout: float = 15.0):
        self.api_base = api_base.rstrip("/")
        self.timeout = timeout
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        # FC 执行网关配置（execute_action 工具用，main() 注入）
        self.fc_base = ""
        self.fc_token = ""

    def post_json(self, url: str, payload: dict, headers: dict | None = None) -> dict:
        """POST JSON 到任意完整 URL，返回 JSON dict（FC 执行网关用）。"""
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json", **(headers or {})},
                method="POST",
            )
            with self._opener.open(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                body = e.read().decode("utf-8", errors="replace")[:300]
            except Exception:
                body = e.reason
            raise RuntimeError(f"HTTP {e.code}: {body}")
        except urllib.error.URLError as e:
            raise RuntimeError(f"无法连接 {url}: {e.reason}")

    def get(self, path: str, params: dict | None = None) -> dict:
        """GET {api_base}{path}?{params}，返回 JSON dict。

        抛出 RuntimeError（携带面向 LLM 的中文错误信息）。
        """
        url = f"{self.api_base}{path}"
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v})
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with self._opener.open(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            # API 层错误（404/503 等）— 提取 detail 给出可读信息
            try:
                body = json.loads(e.read().decode("utf-8"))
                detail = body.get("detail") or body.get("error") or body
            except Exception:
                detail = e.reason
            raise RuntimeError(f"OntoStudio API HTTP {e.code}: {detail}")
        except urllib.error.URLError as e:
            raise RuntimeError(
                f"无法连接 OntoStudio API（{self.api_base}）: {e.reason}。"
                f"请确认 OntoStudio 后端（默认端口 9003）已启动。"
            )
        except json.JSONDecodeError as e:
            raise RuntimeError(f"OntoStudio API 响应解析失败: {e}")


# ── 工具实现（全部只读） ──


def tool_list_namespaces(api: ApiClient, args: dict) -> dict:
    """列出所有已发布本体的 namespace。"""
    return api.get("/v1/namespaces")


def tool_list_concepts(api: ApiClient, args: dict) -> dict:
    """列出指定 namespace 的概念摘要清单（名称/标签/描述/属性数/动作数/规则数）。"""
    ns = _require(args, "namespace")
    return api.get(f"/v1/namespaces/{_enc(ns)}/concepts")


def tool_get_concept(api: ApiClient, args: dict) -> dict:
    """获取单个概念的完整定义：属性、关系、action 签名（含确认要求）、规则（含审批门禁）、数据权限。"""
    ns = _require(args, "namespace")
    concept = _require(args, "concept")
    return api.get(f"/v1/namespaces/{_enc(ns)}/concepts/{_enc(concept)}")


def tool_list_actions(api: ApiClient, args: dict) -> dict:
    """列出全量 action 签名（入参/出参/requiresConfirmation/关联审批规则），可按概念过滤。"""
    ns = _require(args, "namespace")
    return api.get(f"/v1/namespaces/{_enc(ns)}/actions", {"concept": args.get("concept", "")})


def tool_list_rules(api: ApiClient, args: dict) -> dict:
    """列出规则清单（constraint/inference/trigger/computed），可按概念与类型过滤。"""
    ns = _require(args, "namespace")
    return api.get(
        f"/v1/namespaces/{_enc(ns)}/rules",
        {"concept": args.get("concept", ""), "ruleType": args.get("ruleType", "")},
    )


def tool_get_snapshot(api: ApiClient, args: dict) -> dict:
    """获取整图快照：全概念完整定义 + 业务域描述 + 版本号（适合一次性拉取后本地缓存）。"""
    ns = _require(args, "namespace")
    return api.get(f"/v1/namespaces/{_enc(ns)}/snapshot")


def tool_search_concepts(api: ApiClient, args: dict) -> dict:
    """按关键词搜索概念（匹配名称/标签/描述/业务域，大小写不敏感），返回命中的概念摘要。"""
    ns = _require(args, "namespace")
    keyword = (_require(args, "keyword") or "").strip().lower()
    data = api.get(f"/v1/namespaces/{_enc(ns)}/concepts")
    hits = [
        c for c in data.get("concepts", [])
        if keyword in (c.get("name") or "").lower()
        or keyword in (c.get("label") or "").lower()
        or keyword in (c.get("description") or "").lower()
        or keyword in (c.get("domain") or "").lower()
    ]
    return {
        "namespace": ns,
        "version": data.get("version"),
        "keyword": keyword,
        "count": len(hits),
        "concepts": hits,
    }


def tool_execute_action(api: ApiClient, args: dict) -> dict:
    """执行本体 action — 转发 FC 执行网关 POST /api/actions/execute。

    ⚠️ 直通执行（演示阶段拍板）：跳过人机确认与审批门禁，直接读写业务数据。
    """
    if not api.fc_base or not api.fc_token:
        raise RuntimeError(
            "execute_action 未配置：启动时需传 --fc-base 与 --fc-token"
            "（或环境变量 ONTOLOGY_FC_BASE / ONTOLOGY_FC_TOKEN）"
        )
    tool = _require(args, "tool")
    payload = {
        "tool": tool,
        "params": args.get("params") or {},
        "user_id": args.get("user_id") or "mcp_service",
    }
    if args.get("namespace"):
        payload["namespace"] = args["namespace"]
    return api.post_json(
        f"{api.fc_base.rstrip('/')}/actions/execute",
        payload,
        headers={"Authorization": f"Bearer {api.fc_token}"},
    )


def _require(args: dict, name: str) -> str:
    """必填参数校验 — 缺参给出明确中文错误（LLM 可据此补参重试）。"""
    val = (args.get(name) or "").strip() if isinstance(args.get(name), str) else args.get(name)
    if not val:
        raise RuntimeError(f"缺少必填参数 '{name}'")
    return val


def _enc(s: str) -> str:
    """路径段 URL 编码（支持中文概念名/namespace）。"""
    return urllib.parse.quote(str(s), safe="")


# ── MCP 工具声明 ──

TOOLS = [
    {
        "name": "list_namespaces",
        "description": "列出所有已发布本体 namespace（OntoStudio 已推送 Schema 的本体版本集合）。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_concepts",
        "description": "列出指定 namespace 的概念摘要清单：名称、中文标签、描述、业务域、属性/动作/规则计数。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "namespace": {"type": "string", "description": "本体 namespace，如 manufacturing"},
            },
            "required": ["namespace"],
        },
    },
    {
        "name": "get_concept",
        "description": "获取单个概念的完整定义：属性（类型/主键/枚举）、关系（joinOn/遍历语义）、action 签名（含 requiresConfirmation）、规则（含审批门禁 approvalRoles）、数据权限。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "namespace": {"type": "string", "description": "本体 namespace"},
                "concept": {"type": "string", "description": "概念名（英文标识，如 WorkOrder）"},
            },
            "required": ["namespace", "concept"],
        },
    },
    {
        "name": "list_actions",
        "description": "列出全量 action 签名（入参 schema/出参类型/是否需确认/关联审批规则），可按概念过滤。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "namespace": {"type": "string", "description": "本体 namespace"},
                "concept": {"type": "string", "description": "可选：按概念名过滤"},
            },
            "required": ["namespace"],
        },
    },
    {
        "name": "list_rules",
        "description": "列出规则清单（constraint 约束 / inference 推理 / trigger 触发 / computed 计算字段），含审批门禁配置，可按概念与类型过滤。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "namespace": {"type": "string", "description": "本体 namespace"},
                "concept": {"type": "string", "description": "可选：按概念名过滤"},
                "ruleType": {"type": "string", "description": "可选：constraint | inference | trigger | computed", "enum": ["constraint", "inference", "trigger", "computed"]},
            },
            "required": ["namespace"],
        },
    },
    {
        "name": "get_snapshot",
        "description": "获取整图快照：全部概念完整定义 + 业务域描述 + 版本号。适合一次性拉取后本地缓存（响应较大，优先用 list_concepts/get_concept 按需查询）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "namespace": {"type": "string", "description": "本体 namespace"},
            },
            "required": ["namespace"],
        },
    },
    {
        "name": "search_concepts",
        "description": "按关键词搜索概念（匹配名称/中文标签/描述/业务域，大小写不敏感）。不确定概念英文标识时先用这个。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "namespace": {"type": "string", "description": "本体 namespace"},
                "keyword": {"type": "string", "description": "搜索关键词，如 '工单' / '物料'"},
            },
            "required": ["namespace", "keyword"],
        },
    },
    {
        "name": "execute_action",
        "description": "执行本体 action（如 WorkOrder_query 查询、WorkOrder_create 创建）。⚠️ 直通执行：跳过人机确认与审批门禁（演示阶段），会直接读写业务数据；先用 list_actions 查参数签名。生产启用前必须补门禁。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tool": {"type": "string", "description": "工具全名 {Concept}_{action}，如 WorkOrder_create / Material_query"},
                "params": {"type": "object", "description": "动作参数（按 list_actions 返回的签名填写，如 {\"code\": \"WO-001\"}）"},
                "namespace": {"type": "string", "description": "预期 namespace（防呆：与 FC 激活本体不符时拒绝）"},
                "user_id": {"type": "string", "description": "执行者标识（RBAC 角色检查用，默认 mcp_service）"},
            },
            "required": ["tool"],
        },
    },
]

TOOL_HANDLERS = {
    "list_namespaces": tool_list_namespaces,
    "list_concepts": tool_list_concepts,
    "get_concept": tool_get_concept,
    "list_actions": tool_list_actions,
    "list_rules": tool_list_rules,
    "get_snapshot": tool_get_snapshot,
    "search_concepts": tool_search_concepts,
    "execute_action": tool_execute_action,
}


# ── JSON-RPC 协议处理 ──


def handle_initialize(params: dict) -> dict:
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        "capabilities": {"tools": {}},
    }


def handle_tools_list(params: dict) -> dict:
    return {"tools": TOOLS}


def handle_tools_call(api: ApiClient, params: dict) -> dict:
    name = params.get("name", "")
    arguments = params.get("arguments") or {}
    handler = TOOL_HANDLERS.get(name)
    if not handler:
        return _error_result(f"未知工具: {name}（可用: {', '.join(TOOL_HANDLERS)}）")
    try:
        data = handler(api, arguments)
        return {
            "content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}],
        }
    except RuntimeError as e:
        return _error_result(str(e))
    except Exception as e:  # 兜底：任何异常都转为 isError 文本，不让 server 崩溃
        return _error_result(f"工具执行失败: {e}")


def _error_result(message: str) -> dict:
    return {"content": [{"type": "text", "text": f"错误: {message}"}], "isError": True}


HANDLERS = {
    "initialize": handle_initialize,
    "tools/list": handle_tools_list,
}


def main():
    parser = argparse.ArgumentParser(description="OntoStudio 本体只读 MCP Server（stdio）")
    parser.add_argument(
        "--api-base",
        default=os.environ.get("ONTOLOGY_API_BASE", "http://127.0.0.1:9003/api"),
        help="OntoStudio API 基地址（默认 http://127.0.0.1:9003/api，可用环境变量 ONTOLOGY_API_BASE）",
    )
    parser.add_argument("--timeout", type=float, default=15.0, help="HTTP 请求超时秒数（默认 15）")
    parser.add_argument(
        "--fc-base",
        default=os.environ.get("ONTOLOGY_FC_BASE", ""),
        help="FC 执行网关基地址（execute_action 用，如 http://127.0.0.1:9004/api；环境变量 ONTOLOGY_FC_BASE）",
    )
    parser.add_argument(
        "--fc-token",
        default=os.environ.get("ONTOLOGY_FC_TOKEN", ""),
        help="FC 服务身份 JWT（execute_action 用；环境变量 ONTOLOGY_FC_TOKEN）",
    )
    cli = parser.parse_args()
    api = ApiClient(cli.api_base, timeout=cli.timeout)
    api.fc_base = cli.fc_base
    api.fc_token = cli.fc_token

    # stdio JSON-RPC 主循环：一行一个请求，一行一个响应
    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break  # stdin 关闭（客户端退出）
            request = json.loads(line)
        except json.JSONDecodeError:
            _write({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
            continue
        except Exception:
            break

        req_id = request.get("id")
        method = request.get("method", "")
        params = request.get("params") or {}

        # 通知（无 id）只处理不发响应（如 notifications/initialized）
        if req_id is None:
            continue

        try:
            if method == "tools/call":
                result = handle_tools_call(api, params)
                _write({"jsonrpc": "2.0", "id": req_id, "result": result})
            elif method in HANDLERS:
                result = HANDLERS[method](params)
                _write({"jsonrpc": "2.0", "id": req_id, "result": result})
            elif method.startswith("notifications/"):
                continue
            else:
                _write({
                    "jsonrpc": "2.0", "id": req_id,
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                })
        except Exception as e:
            _write({
                "jsonrpc": "2.0", "id": req_id,
                "error": {"code": -32603, "message": f"Internal error: {e}"},
            })


def _write(data: dict) -> None:
    sys.stdout.write(json.dumps(data, ensure_ascii=False) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
