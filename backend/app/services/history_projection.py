"""
结构化历史投影

把数据库会话消息（Message）投影为「模型可见 + 路由可消费」的结构化历史记录，
取代此前把 agent 身份拼进 assistant 文本、下游靠字符串反向解析的脆弱做法。

对照 DSH session-projection：每轮历史是一份带身份的结构化记录
（哪轮由哪个 agent/工具处理、什么参数），身份是独立字段而非文本前缀。

实现约束（B1 纯增量）：
- 返回 LangChain 消息（HumanMessage/AIMessage），类型不变，现有路由层、
  动态规划、LLM 服务全部兼容（含 Qwen 内置联网搜索直接 astream 的路径）；
- 结构化字段承载于 LangChain 消息的 ``additional_kwargs[dsh_turn]``，
  OpenAI converter 只透传它认识的键，自定义键不会污染 API 请求；
- assistant content 仍带 ``[本轮由 XX 处理]`` 前缀（与投影前一致），
  B3 阶段删除该文本前缀，路由决策层改读结构化字段。
"""

import json
from typing import Any, Dict, List, Tuple

from langchain_core.messages import AIMessage as LCAIMessage
from langchain_core.messages import BaseMessage
from langchain_core.messages import HumanMessage as LCHumanMessage

from app.models.message import Message, MessageRole

# 结构化历史在 LangChain 消息 additional_kwargs 中的键名
TURN_META_KEY = "dsh_turn"


def _resolve_agent_label(meta: Dict[str, Any], agent_name: str) -> str:
    """解析处理本轮的 agent 展示名。

    优先级：外部 A2A 协作 display_name > agent_info.display_name >
    AGENT_DEFINITIONS.display_name > agent_name。
    """
    collab = meta.get("collab_agents") or []
    if collab:
        names = [c.get("display_name") or c.get("name", "") for c in collab if isinstance(c, dict)]
        label = "、".join([n for n in names if n])
        if label:
            return label
    info = meta.get("agent_info") or {}
    if isinstance(info, dict):
        label = info.get("display_name", "") or ""
        if label:
            return label
    if agent_name:
        try:
            from app.agents.agent_config import AGENT_DEFINITIONS
            label = (AGENT_DEFINITIONS.get(agent_name, {}) or {}).get("display_name", "") or agent_name
        except Exception:
            label = agent_name
    return label


def _resolve_tool(meta: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """尽力提取本轮工具名与参数（外部协作优先，其次 execution_steps）。

    execution_steps 是展示结构（label/detail），非干净的工具/参数存储，
    此处为尽力提取；B2 落库时会写入干净的 tool/params 字段替代反推。
    """
    collab = meta.get("collab_agents") or []
    if collab:
        first = collab[0] if isinstance(collab[0], dict) else {}
        return str(first.get("name", "")), {}

    steps = meta.get("execution_steps") or []
    for s in steps:
        if not isinstance(s, dict):
            continue
        if s.get("key") != "tool_start":
            continue
        label = str(s.get("label", ""))
        tool = ""
        if ":" in label:
            tool = label.split(":", 1)[1].strip()
        elif s.get("tool"):
            tool = str(s.get("tool", ""))
        params: Dict[str, Any] = {}
        detail = s.get("detail", "")
        if detail and detail not in ("无查询条件", "无过滤条件"):
            try:
                parsed = json.loads(detail)
                if isinstance(parsed, dict):
                    params = parsed
            except (json.JSONDecodeError, TypeError):
                pass
        return tool, params
    return "", {}


def _build_turn_meta(msg: Message) -> Dict[str, Any]:
    """从 assistant 消息的 metadata 投影结构化身份字段。"""
    meta = msg.metadata_dict or {}
    agent_name = str(meta.get("agent_name", "") or "")
    tool, params = _resolve_tool(meta)
    return {
        "role": "assistant",
        "agent_name": agent_name,
        "agent_label": _resolve_agent_label(meta, agent_name),
        "tool": tool,
        "params": params,
        "is_dynamic": bool(meta.get("is_dynamic", False)),
        "collab_agents": meta.get("collab_agents") or [],
    }


def project_turn(msg: Message) -> BaseMessage:
    """把一条 DB Message 投影为一条 LangChain 消息（assistant 附带结构化身份）。

    content 保持与投影前一致（assistant 带 ``[本轮由 XX 处理]`` 前缀）。
    """
    if msg.role == MessageRole.USER:
        return LCHumanMessage(content=msg.content or "")
    if msg.role == MessageRole.ASSISTANT:
        turn_meta = _build_turn_meta(msg)
        content = msg.content or ""
        label = turn_meta.get("agent_label", "")
        if label:
            content = f"[本轮由 {label} 处理] {content}"
        return LCAIMessage(content=content, additional_kwargs={TURN_META_KEY: turn_meta})
    # 其他角色（如 SYSTEM）不进入历史投影
    return LCHumanMessage(content=msg.content or "")


def project_history(messages: List[Message]) -> List[BaseMessage]:
    """把 DB Message 列表投影为 LangChain 消息列表（跳过非 user/assistant 消息）。"""
    result: List[BaseMessage] = []
    for msg in messages:
        if msg.role not in (MessageRole.USER, MessageRole.ASSISTANT):
            continue
        result.append(project_turn(msg))
    return result


def recent_turns(history_messages: Any, limit: int = 3) -> List[Dict[str, Any]]:
    """从 LangChain 历史消息中提取最近 N 轮的结构化身份投影。

    供路由决策层（L2 分类、参数提取）消费，取代文本标签反向解析。
    ``history_messages`` 为 ``project_history`` 产出的 LangChain 消息列表，
    结构化身份承载于 ``additional_kwargs[dsh_turn]``。
    """
    turns: List[Dict[str, Any]] = []
    for hm in reversed(history_messages or []):
        meta = getattr(hm, "additional_kwargs", {}).get(TURN_META_KEY)
        if isinstance(meta, dict):
            turns.append(meta)
            if len(turns) >= limit:
                break
    turns.reverse()
    return turns
