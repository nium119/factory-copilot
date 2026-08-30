# -*- coding: utf-8 -*-
"""四源工具统一注册表：ontology / api / mcp / a2a。

统一循环的「执行层」底座——LLM 规划器看到的工具清单、能力发现、
工具边界校验都从这里取，不再各自散落。

阶段 A 先统一 ontology + mcp 两源；api（multi_system_backend 的系统端点）
与 a2a（外部 Agent 能力）作为后续源补入 collect_api / collect_a2a。
"""
from app.core.logger import log
from app.agents.governance import RISK_BY_OUTPUT as _RISK_BY_OUTPUT  # 治理层风险分级（单一数据源）

# 动作动词 → 工具名子串（确定性过滤，不依赖 LLM）
_VERB_KEYWORDS = (
    ("创建", "create"), ("新增", "create"), ("新建", "create"),
    ("更新", "update"), ("修改", "update"), ("变更", "update"),
    ("删除", "delete"), ("移除", "delete"),
    ("排程", "schedule"), ("插单", "insertorder"),
)


def filter_writes_by_verb(write_ops: list[dict], message: str) -> list[dict]:
    """按用户消息中的动作动词过滤写操作（确定性，不做语义理解）。

    用户说「创建」就只留 create 类，避免上百项全列；无动词或过滤后为空则原样返回。
    """
    verb = next((v for kw, v in _VERB_KEYWORDS if kw in message), None)
    if not verb:
        return write_ops
    filtered = [c for c in write_ops if verb in (c.get("name") or "").lower()]
    return filtered or write_ops


def build_capability_fallback(message: str, write_ops: list[dict], limit: int = 8) -> str:
    """确定性能力清单回退文案（LLM 反问失败时的兜底）。

    只列本体里真实存在的写操作（本体是能力边界），按 label（概念）展示。
    """
    lines = "\n".join(
        f"• {c.get('label') or c.get('name')}（{c.get('concept_label') or c.get('concept_name')}）"
        for c in write_ops[:limit]
    )
    return (
        f"我理解你想「{message}」，但当前没有直接对应的操作。\n"
        f"我可以帮你做以下事情：\n{lines}\n"
        f"请告诉我具体要做哪一项，或用更明确的说法。"
    )


class ToolRegistry:
    """四源工具统一注册表。"""

    def __init__(self):
        self._tools: dict[str, dict] = {}

    # ── 注册 / 查询 ──

    def register(self, tool: dict) -> None:
        if tool.get("name"):
            self._tools[tool["name"]] = tool

    def get(self, name: str) -> dict | None:
        return self._tools.get(name)

    def get_all(self) -> list[dict]:
        return list(self._tools.values())

    def has(self, name: str) -> bool:
        return name in self._tools

    def get_writes(self) -> list[dict]:
        """写操作类工具（非 READ）。"""
        return [t for t in self._tools.values() if t.get("risk") != "READ"]

    def get_by_source(self, source: str) -> list[dict]:
        return [t for t in self._tools.values() if t.get("source") == source]

    # ── 收集：本体动作 ──

    def collect_ontology(self, action_signatures: list[dict]) -> int:
        """从本体动作签名收集工具。返回收集数量。"""
        count = 0
        for sig in action_signatures:
            fn = sig.get("functionName") or ""
            if not fn:
                continue
            output_type = sig.get("outputType", "") or "write"
            # 按函数名后缀推断操作类型（比 outputType 更可靠，对齐 action_executor）
            if fn.endswith("_query"):
                op = "query"
            elif fn.endswith("_delete"):
                op = "delete"
            elif fn.endswith("_findSimilar"):
                op = "similarity"
            else:
                op = output_type or "write"
            risk = _RISK_BY_OUTPUT.get(op, "WRITE_APPROVE")
            self.register({
                "name": fn,
                "label": sig.get("actionLabel") or fn,
                "description": sig.get("description", "") or "",
                "concept_name": sig.get("conceptName", ""),
                "concept_label": sig.get("conceptLabel", ""),
                "source": "ontology",
                "params": sig.get("params", []),
                "output_type": op,
                "action_name": sig.get("actionName", ""),
                "requires_confirmation": sig.get("requiresConfirmation", False),
                "authorized_roles": sig.get("authorized_roles", []),
                "risk": risk,
            })
            count += 1
        return count

    # ── 收集：MCP ──

    def collect_mcp(self) -> int:
        """从 MCP registry 收集工具。返回收集数量。"""
        try:
            from app.mcp import mcp_registry
        except Exception as e:
            log.warning(f"[ToolRegistry] MCP registry 不可用: {e}")
            return 0
        count = 0
        for tool_name in mcp_registry.get_tool_names():
            client, mcp_tool = mcp_registry._tool_map.get(tool_name, (None, None))
            name = mcp_tool.name if mcp_tool else tool_name
            desc = mcp_tool.description if mcp_tool else ""
            params = []
            input_schema = mcp_tool.input_schema if mcp_tool else {}
            props = input_schema.get("properties", {})
            required = input_schema.get("required", [])
            for pname, pinfo in props.items():
                params.append({
                    "name": pname,
                    "type": pinfo.get("type", "string"),
                    "label": pinfo.get("description", pname),
                    "required": pname in required,
                })
            # MCP 风险从 TOOL_SAFETY 读，默认 READ
            risk = "READ"
            try:
                from app.agents.settings import TOOL_SAFETY as _MCP_TS
                risk = (_MCP_TS.get(tool_name) or {}).get("risk", "READ")
            except Exception:
                pass
            self.register({
                "name": tool_name,
                "label": desc or name,
                "description": desc,
                "concept_name": name,
                "concept_label": f"{name}(MCP)",
                "source": "mcp",
                "params": params,
                "output_type": "mcp",
                "action_name": "",
                "requires_confirmation": risk != "READ",
                "authorized_roles": [],
                "risk": risk,
            })
            count += 1
        return count

    # ── 收集：API（阶段后续补）──
    # 从 multi_system_backend 的系统配置（endpoints）生成工具签名

    # ── 收集：A2A（阶段后续补）──
    # 从外部 Agent 的能力描述生成工具签名

    def rebuild(self, ontology_service) -> int:
        """从本体 + MCP 重建注册表。返回工具总数。"""
        self._tools.clear()
        self.collect_ontology(ontology_service.get_action_signatures() or [])
        self.collect_mcp()
        log.info(f"[ToolRegistry] 重建完成: {len(self._tools)} 个工具 "
                 f"(ontology={len(self.get_by_source('ontology'))}, mcp={len(self.get_by_source('mcp'))})")
        return len(self._tools)

    def ensure_loaded(self, ontology_service) -> int:
        """懒加载：空则重建。返回工具总数。"""
        if not self._tools:
            return self.rebuild(ontology_service)
        return len(self._tools)


# 全局单例
tool_registry = ToolRegistry()
