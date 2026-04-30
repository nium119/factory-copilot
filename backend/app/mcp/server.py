"""Demo MCP Server — 基于 stdio JSON-RPC 的最小化实现，提供示例工具"""
import json
import sys
import traceback
from datetime import datetime

# Windows 下强制 UTF-8 编码
if sys.platform == "win32":
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


def _read_request() -> dict:
    """从 stdin 读取一行 JSON 请求"""
    line = sys.stdin.readline()
    if not line:
        sys.exit(0)
    return json.loads(line)


def _write_response(data: dict) -> None:
    """向 stdout 写入一行 JSON 响应"""
    sys.stdout.write(json.dumps(data, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def handle_initialize(params: dict) -> dict:
    return {
        "protocolVersion": "2024-11-05",
        "serverInfo": {
            "name": "factory-copilot-demo-server",
            "version": "1.0.0",
        },
        "capabilities": {
            "tools": {},
        },
    }


def handle_tools_list(params: dict) -> dict:
    return {
        "tools": [
            {
                "name": "calculator",
                "description": "安全计算数学表达式，支持 + - * / ** 和括号。",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "expression": {
                            "type": "string",
                            "description": "数学表达式，如 '2 + 3 * 4'",
                        },
                    },
                    "required": ["expression"],
                },
            },
            {
                "name": "get_current_time",
                "description": "获取当前日期时间。",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "timezone": {
                            "type": "string",
                            "description": "时区，如 'Asia/Shanghai'，默认本地时区",
                        },
                    },
                },
            },
        ],
    }


def handle_tools_call(params: dict) -> dict:
    name = params.get("name", "")
    arguments = params.get("arguments", {})

    if name == "calculator":
        expression = arguments.get("expression", "")
        try:
            # 安全计算：只允许数字、运算符、括号、空格
            allowed = set("0123456789+-*/.() **")
            if not all(c in allowed for c in expression):
                return {
                    "content": [{"type": "text", "text": "错误: 表达式包含不允许的字符。仅支持数字和 + - * / ** 运算符。"}],
                    "isError": True,
                }
            result = eval(expression, {"__builtins__": {}}, {})
            return {"content": [{"type": "text", "text": f"计算结果: {expression} = {result}"}]}
        except Exception as e:
            return {"content": [{"type": "text", "text": f"计算错误: {str(e)}"}], "isError": True}

    elif name == "get_current_time":
        tz = arguments.get("timezone", "")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        info = f"当前时间: {now}"
        if tz:
            info += f" (请求时区: {tz})"
        return {"content": [{"type": "text", "text": info}]}

    else:
        return {"content": [{"type": "text", "text": f"未知工具: {name}"}], "isError": True}


HANDLERS = {
    "initialize": handle_initialize,
    "tools/list": handle_tools_list,
    "tools/call": handle_tools_call,
}


def main():
    """Demo MCP Server 主循环 — stdio JSON-RPC"""
    while True:
        try:
            request = _read_request()
            req_id = request.get("id")
            method = request.get("method", "")
            params = request.get("params", {})

            handler = HANDLERS.get(method)
            if handler:
                result = handler(params)
                _write_response({"jsonrpc": "2.0", "id": req_id, "result": result})
            else:
                _write_response({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                })

        except json.JSONDecodeError:
            _write_response({
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Parse error"},
            })
        except Exception:
            traceback.print_exc(file=sys.stderr)
            _write_response({
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32603, "message": "Internal error"},
            })


if __name__ == "__main__":
    main()
