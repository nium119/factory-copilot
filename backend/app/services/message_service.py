"""
消息处理服务
集成长期记忆检索和上下文注入，从数据库加载历史消息作为LLM上下文
采用混合记忆策略：保留最近 N 条完整消息 + 旧消息摘要压缩
"""
import asyncio
import json
import re
from typing import AsyncGenerator, List, Optional, Tuple

from langchain_core.messages import AIMessage as LCAIMessage
from langchain_core.messages import HumanMessage as LCHumanMessage
from langchain_core.messages import SystemMessage as LCSystemMessage
from loguru import logger

from app.core.chain_engine import chain_engine
from app.core.config import settings
from app.core.error_codes import ErrorCode, classify_exception, sse_error
from app.models.conversation import Conversation
from app.models.message import ConfirmStatus, Message, MessageRole, MessageType
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.message_repository import MessageRepository
from app.services.llm_service import llm_service
from app.services.vector_memory_service import vector_memory_service


# ── 执行链路事件捕获 ──

_EXEC_STEP_KEYS = {
    "route_start", "route_match", "route_l2", "route_l3",
    "param_extract", "confirm_required", "confirm_result",
    "confirm_delegated",
    "tool_start", "tool_result", "format_start", "execution_done",
    "parallel_start", "parallel_task", "parallel_done",
}

_STEP_LABEL_MAP = {
    "route_start": "路由分析",
    "route_l2": "意图识别",
    "route_match": "匹配工具",
    "route_l3": "无匹配兜底",
    "param_extract": "参数提取",
    "confirm_required": "等待确认",
    "confirm_result": "确认结果",
    "confirm_delegated": "委托审批",
    "tool_start": "工具执行",
    "tool_result": "查询结果",
    "format_start": "LLM 格式化",
    "execution_done": "执行完成",
    "parallel_start": "多域协作",
    "parallel_task": "Agent 查询",
    "parallel_done": "协作完成",
}


def _strip_markdown_code_wrapper(content: str) -> str:
    """剥离 LLM 输出的 ```markdown / ```md 代码块包裹，保留内部 Markdown 内容。"""
    if not content:
        return content
    # 匹配 ```markdown 或 ```md 开头的代码块，提取内部内容
    pattern = r'```(?:markdown|md)\s*\n(.*?)\n\s*```'
    cleaned = re.sub(pattern, r'\1', content, flags=re.DOTALL)
    return cleaned


def _maybe_capture_exec_step(chunk_type: str, content: str, steps: list) -> None:
    """从 SSE 事件中提取执行链路步骤，存入 steps 列表。"""
    if chunk_type not in _EXEC_STEP_KEYS:
        return

    step = {"key": chunk_type, "status": "done", "label": _STEP_LABEL_MAP.get(chunk_type, chunk_type)}
    try:
        import json as _json
        data = _json.loads(content) if isinstance(content, str) else (content or {})
    except Exception:
        data = {}

    if chunk_type == "route_start":
        step["detail"] = f"Agent: {data.get('agent', '')}"
    elif chunk_type in ("route_match", "route_l2"):
        tool = data.get("tool", "")
        label = data.get("action_label", "") or data.get("concept_label", "") or tool
        method = data.get("method", "")
        method_label = "关键词匹配" if method == "keyword" else f"置信度 {int(data.get('confidence', 0) * 100)}%"
        step["label"] = f"匹配工具: {label}" if label else step["label"]
        step["detail"] = method_label
    elif chunk_type == "param_extract":
        params = data.get("params", {})
        if params:
            step["detail"] = _json.dumps(params, ensure_ascii=False)
    elif chunk_type == "confirm_required":
        step["status"] = "running"
        step["label"] = f"人工确认: {data.get('action_label', '')}"
    elif chunk_type == "confirm_delegated":
        step["status"] = "done"
        assigned = data.get("assigned_to", [])
        step["label"] = f"委托审批 → {assigned[0] if assigned else '?'}"
    elif chunk_type == "confirm_result":
        if data.get("approved"):
            step["status"] = "done"
            step["label"] = "人工确认通过"
        else:
            step["status"] = "error"
            step["label"] = "操作已取消"
    elif chunk_type == "tool_start":
        step["status"] = "running"
        step["label"] = f"执行: {data.get('label', '') or data.get('tool', '')}"
        params = data.get("params", {})
        if params:
            step["detail"] = _json.dumps(params, ensure_ascii=False)
    elif chunk_type == "tool_result":
        source = data.get('source', '')
        source_label = data.get('sourceLabel', '') or {"api": "业务系统", "neo4j": "图数据库"}.get(source, "图数据库")
        step["label"] = f"查询结果: {data.get('rowCount', 0)} 条记录"
        step["detail"] = f"{source_label}"
    elif chunk_type == "execution_done":
        if data.get("cancelled"):
            step["status"] = "error" if not data.get("delegated") else "done"
            step["label"] = "已委托审批" if data.get("delegated") else "已取消"

    # ── 多域协作事件 ──
    elif chunk_type == "parallel_start":
        step["status"] = "running"
        tasks = data.get("tasks", [])
        names = [t.get("display_name", t.get("agent_name", "")) for t in tasks]
        step["detail"] = "、".join(names)
    elif chunk_type == "parallel_task":
        agent_name = data.get("display_name", data.get("agent_name", ""))
        status = data.get("status", "")
        elapsed = data.get("elapsed", 0)
        status_label = {"success": "完成", "timeout": "超时", "error": "失败", "empty": "无数据"}.get(status, status)
        step["label"] = f"Agent 查询: {agent_name}"
        step["detail"] = f"{status_label} ({elapsed:.0f}ms)"
        if status in ("timeout", "error"):
            step["status"] = "error"
    elif chunk_type == "parallel_done":
        success = data.get("success", 0)
        total = data.get("total", 0)
        step["label"] = f"协作完成: {success}/{total} 成功"
        # 标记 parallel_start 为完成
        for s in reversed(steps):
            if s["key"] == "parallel_start" and s["status"] == "running":
                s["status"] = "done"
                break

    # 当 tool_result 到达时标记前一个 tool_start 步骤为完成
    if chunk_type == "tool_result" and steps:
        for s in reversed(steps):
            if s["key"] == "tool_start" and s["status"] == "running":
                s["status"] = "done"
                break

    # 标记前一个 confirm_required 步骤
    if chunk_type == "confirm_result" and steps:
        for s in reversed(steps):
            if s["key"] == "confirm_required" and s["status"] == "running":
                s["status"] = "done" if data.get("approved") else "error"
                break

    steps.append(step)


class MessageService:
    """消息处理服务"""

    def __init__(
        self,
        message_repo: MessageRepository,
        conversation_repo: ConversationRepository
    ):
        self.message_repo = message_repo
        self.conversation_repo = conversation_repo
        self.llm_service = llm_service

    async def _load_history_messages(
        self,
        conversation_id: str,
        conversation: Optional[Conversation],
        exclude_last_user: bool = True
    ) -> Tuple[List, Optional[str]]:
        """
        从数据库加载会话历史消息，采用混合记忆策略

        Args:
            conversation_id: 会话ID
            conversation: 会话对象（用于获取缓存摘要）
            exclude_last_user: 是否排除最后一条用户消息（避免重复发送）

        Returns:
            (LangChain消息列表, 更新的摘要或None)
        """
        try:
            db_messages = await self.message_repo.get_by_conversation(conversation_id)

            if not db_messages:
                return [], None

            # 如果需要排除最后一条用户消息（已作为当前消息传入LLM）
            if exclude_last_user and db_messages:
                last_user_idx = None
                for i in range(len(db_messages) - 1, -1, -1):
                    if db_messages[i].role == MessageRole.USER:
                        last_user_idx = i
                        break
                if last_user_idx is not None:
                    db_messages = db_messages[:last_user_idx]

            # 检查是否需要摘要压缩
            summary = None
            if len(db_messages) > settings.MAX_HISTORY_LENGTH:
                history, summary = await self._build_hybrid_context(
                    db_messages, conversation
                )
            else:
                history = []
                for msg in db_messages:
                    if msg.role == MessageRole.USER:
                        history.append(LCHumanMessage(content=msg.content))
                    elif msg.role == MessageRole.ASSISTANT:
                        history.append(LCAIMessage(content=msg.content))

            logger.info(
                f"从数据库加载了 {len(history)} 条历史消息作为上下文 "
                f"(会话: {conversation_id}, 有摘要: {summary is not None})"
            )
            return history, summary

        except Exception as e:
            logger.error(f"加载历史消息失败: {e}")
            return [], None

    async def _build_hybrid_context(
        self,
        messages: List[Message],
        conversation: Optional[Conversation]
    ) -> Tuple[List, Optional[str]]:
        """
        构建混合上下文：旧消息摘要 + 最近 N 条完整消息

        Args:
            messages: 全部消息列表
            conversation: 会话对象

        Returns:
            (历史消息列表, 更新的摘要)
        """
        # 分割旧消息和最近消息
        old_messages = messages[:-settings.MAX_HISTORY_LENGTH]
        recent_messages = messages[-settings.MAX_HISTORY_LENGTH:]

        # 转换为 LangChain 格式（仅最近消息）
        history = []
        for msg in recent_messages:
            if msg.role == MessageRole.USER:
                history.append(LCHumanMessage(content=msg.content))
            elif msg.role == MessageRole.ASSISTANT:
                history.append(LCAIMessage(content=msg.content))

        # 获取已有摘要
        existing_summary = conversation.summary if conversation else None

        # 生成或更新摘要（同步，但复用已有摘要时跳过）
        new_summary = None
        if existing_summary:
            new_summary = existing_summary
            logger.info("复用已有摘要")
        else:
            # 首次生成：带超时保护，避免 LLM 调用过久
            try:
                new_summary = await asyncio.wait_for(
                    self._generate_summary(
                        old_messages=old_messages,
                        existing_summary=existing_summary,
                    ),
                    timeout=5.0,
                )
                logger.info(f"首次生成摘要成功，摘要长度: {len(new_summary)} 字")
            except asyncio.TimeoutError:
                logger.warning("摘要生成超时，跳过")
            except Exception as e:
                logger.error(f"摘要生成失败: {e}")

        if new_summary:
            # 将摘要作为系统消息插入到历史开头
            history.insert(0, LCSystemMessage(content=f"## 历史对话摘要\n\n{new_summary}\n\n请基于以上摘要和以下完整消息来理解上下文。"))
            logger.info(f"生成/更新摘要成功，摘要长度: {len(new_summary)} 字")
        else:
            logger.warning("摘要生成失败，使用空摘要")

        return history, new_summary

    async def _generate_summary(
        self,
        old_messages: List[Message],
        existing_summary: Optional[str] = None
    ) -> Optional[str]:
        """
        调用 LLM 生成摘要（非阻塞，使用独立 LLM 实例）

        Args:
            old_messages: 需要压缩的旧消息
            existing_summary: 已有摘要（首次压缩时为空）

        Returns:
            摘要文本或 None
        """
        try:
            from langchain_core.messages import HumanMessage, SystemMessage
            from langchain_openai import ChatOpenAI

            from app.core.model_config import get_api_key, get_model_config
            from app.core.prompts import format_summary_prompt

            # 格式化旧消息内容
            old_text = "\n".join(
                f"[{msg.role.value}] {msg.content[:300]}"
                for msg in old_messages
            )

            prompt = format_summary_prompt(
                old_messages=old_text,
                existing_summary=existing_summary or "",
                max_tokens=settings.SUMMARY_MAX_TOKENS
            )

            # 使用独立的 LLM 实例生成摘要，不影响主服务的模型状态
            target_model = settings.AGENT_MODEL
            model_config = get_model_config(target_model)
            api_key = get_api_key(model_config["provider"])

            summary_llm = ChatOpenAI(
                model=target_model,
                temperature=settings.AGENT_TEMPERATURE,
                max_tokens=model_config["max_tokens"],
                openai_api_base=model_config["api_base"],
                openai_api_key=api_key,
            )

            # 使用 asyncio.to_thread 将同步阻塞调用移到线程池
            messages = [
                SystemMessage(content="你是一个信息摘要专家，请对以下对话历史进行简洁摘要。"),
                HumanMessage(content=prompt)
            ]
            response = await asyncio.to_thread(summary_llm.invoke, messages)
            summary = response.content

            if len(summary) > settings.SUMMARY_MAX_TOKENS * 2:
                summary = summary[:settings.SUMMARY_MAX_TOKENS * 2]

            return summary

        except Exception as e:
            logger.error(f"摘要生成失败: {e}")
            return None

    async def _update_summary_if_needed(
        self,
        conversation_id: str,
        new_summary: Optional[str]
    ) -> None:
        """
        将摘要保存到数据库

        Args:
            conversation_id: 会话ID
            new_summary: 新摘要
        """
        if new_summary is None:
            return

        try:
            await self.conversation_repo.update_summary(conversation_id, new_summary)
            logger.info(f"摘要已更新到数据库 (会话: {conversation_id})")
        except Exception as e:
            logger.error(f"更新摘要到数据库失败: {e}")

    async def process_message_stream(
        self,
        user_id: str,
        conversation_id: str,
        message: str,
        model_name: Optional[str] = None,
        use_agent: bool = False,
        web_search: bool = False,
        enable_memory: bool = True,
        agent_name: Optional[str] = None,
        enable_thinking: Optional[bool] = None,
        matched_agents: Optional[list] = None,
    ) -> AsyncGenerator[tuple, None]:
        """
        处理消息并流式返回响应

        Args:
            user_id: 用户ID
            conversation_id: 会话ID
            message: 用户消息
            model_name: 模型名称
            use_agent: 是否启用协作模式（多 Agent 并发查询）
            web_search: 是否启用联网搜索
            enable_memory: 是否启用长期记忆
            agent_name: Agent名称（None=通用助手）
            enable_thinking: 是否启用深度思考（None=使用模型默认值）

        Yields:
            (type, content) 元组
        """
        # 设置 API 调用日志上下文（关联用户和会话）
        from app.services.multi_system_backend import _request_user_id, _request_conversation_id, _request_message, _request_token
        _request_user_id.set(user_id or "")
        _request_conversation_id.set(conversation_id or "")
        _request_message.set(message or "")
        # 透传请求的 Bearer token 给 API 系统调用
        try:
            token = http_request.headers.get("Authorization", "")
            if token.startswith("Bearer "):
                _request_token.set(token[7:])
        except Exception:
            pass

        ai_response_saved = False
        user_msg = None
        resolved_agent_name = "analysis_monitor"
        full_response = ""
        ai_metadata: dict = {}
        plan_steps: list = []
        plan_title = ""
        reflection_reason = None
        new_summary = None
        execution_steps: list = []
        chain_steps: list = []
        chain_id = ""
        chain_name = ""
        is_dynamic = False
        _has_report = False
        _has_alert = False

        try:
            logger.info(f"[消息处理] use_agent={use_agent}, agent_name={agent_name}, enable_memory={enable_memory}")

            # 0. Guardrails 输入安全检查
            from app.agents.guardrails import check_input, sanitize_input
            message = sanitize_input(message)
            is_valid, reject_reason = check_input(message)
            if not is_valid:
                logger.warning(f"[Guardrails] 输入被拒绝: {reject_reason}")
                code = ErrorCode.INPUT_EMPTY if "空" in (reject_reason or "") else ErrorCode.INPUT_SENSITIVE
                yield ('error', json.dumps(sse_error(code, reject_reason), ensure_ascii=False))
                yield ('done', '')
                return

            # 1-2. 检索长期记忆并构建上下文
            memory_context = await self._build_memory_context(
                user_id=user_id, message=message,
                conversation_id=conversation_id, enable_memory=enable_memory,
            )

            # 3. 保存用户消息到数据库
            user_msg = await self.message_repo.create(
                conversation_id=conversation_id,
                role=MessageRole.USER,
                content=message
            )

            # 4. 获取会话对象（用于读取缓存摘要）
            conversation = await self.conversation_repo.get_by_id(conversation_id)

            # 5. 从数据库加载历史消息（包含摘要压缩逻辑，带超时保护）
            history_messages = []
            try:
                history_messages, new_summary = await asyncio.wait_for(
                    self._load_history_messages(conversation_id, conversation, exclude_last_user=True),
                    timeout=5.0,
                )
                logger.info(f"加载了 {len(history_messages)} 条历史消息，将传给 Agent")
            except asyncio.TimeoutError:
                logger.warning("历史消息加载超时，跳过")

            # 6. 歧义优先：时间模糊且无具体数字 → 直接动态规划 (ASK追问)
            _ambiguity_handled = False
            import re as _re_amb2
            _is_ambiguous = (_re_amb2.search(r'最近|前段时间|近期|过去', message)
                and not _re_amb2.search(r'\d+\s*[个天月周年]', message))
            _is_time_answer = (len(message.strip()) < 15
                and _re_amb2.search(r'\d+\s*[个天月周年]', message))
            # 纯模糊查询：没具体业务对象 → ASK追问
            _is_vague = (_re_amb2.search(r'怎么样|如何|什么情况|帮我看看|整体', message)
                and not _re_amb2.search(r'工单|设备|质检|物料|质量|安灯|人员|报工|缺陷|库存|工艺|BOM|排产', message))
            if _is_ambiguous or _is_time_answer or _is_vague:
                try:
                    if chain_engine._get_compiled_runtime():
                        logger.info(f"[Ambiguity] {'时间模糊→ASK' if _is_ambiguous else '时间回答→综合分析'}")
                        is_dynamic = True; chain_id = "dynamic"; chain_name = "智能分析"; chain_steps = []
                        async for cht, chc in chain_engine._execute_dynamic(
                            message=message, model_name=model_name,
                            enable_thinking=enable_thinking, session_id=conversation_id,
                            history_messages=history_messages,
                        ):
                            if cht == 'content': full_response += chc
                            yield (cht, chc)
                            if cht == 'chain_start':
                                try: cs = json.loads(chc) if isinstance(chc,str) else chc; chain_name = cs.get("chain_name", chain_name); chain_steps = (cs.get("steps") or []).copy()
                                except: pass
                            elif cht == 'chain_step':
                                try: cs = json.loads(chc) if isinstance(chc,str) else chc; sid = cs.get("step_id",""); idx = next((i for i,s in enumerate(chain_steps) if s.get("step_id")==sid), -1); (chain_steps[idx].update(cs) if idx>=0 else chain_steps.append(cs))
                                except: pass
                            elif cht == 'error': break
                        if not _is_ambiguous:
                            _has_report = True
                        resolved_agent_name = "analysis_monitor"
                        ai_metadata = {"chain_id": chain_id, "chain_name": chain_name}
                        _ambiguity_handled = True
                except Exception as e:
                    logger.warning(f"[Ambiguity] 动态规划失败: {e}")

            if not _ambiguity_handled:
                # 统一模式判定 — 链引擎优先，其他走 Agent
                chain_id = chain_engine.detect(message)

            if not _ambiguity_handled and chain_id:
                # ── 模式 1: 预定义链引擎 ──
                from app.agents import get_agent
                chain_engine.set_agent_resolver(get_agent)
                logger.info(f"[ChainEngine] 触发链: {chain_id}")

                async for chunk_type, chunk_content in chain_engine.execute(
                    message=message,
                    model_name=model_name,
                    enable_thinking=enable_thinking,
                    session_id=conversation_id,
                    history_messages=history_messages,
                ):
                    if chunk_type == 'content':
                        full_response += chunk_content
                    yield (chunk_type, chunk_content)
                    _maybe_capture_exec_step(chunk_type, chunk_content, execution_steps)

                    # 收集链步骤用于持久化
                    if chunk_type == 'chain_start':
                        try:
                            cs = json.loads(chunk_content) if isinstance(chunk_content, str) else chunk_content
                            chain_id = cs.get("chain_id", "")
                            chain_name = cs.get("chain_name", "")
                            is_dynamic = cs.get("dynamic", False)
                            chain_steps = (cs.get("steps") or []).copy()
                        except Exception: pass
                    elif chunk_type == 'chain_step':
                        try:
                            cs = json.loads(chunk_content) if isinstance(chunk_content, str) else chunk_content
                            sid = cs.get("step_id", "")
                            idx = next((i for i, s in enumerate(chain_steps) if s.get("step_id") == sid), -1)
                            if idx >= 0:
                                chain_steps[idx].update(cs)
                            else:
                                chain_steps.append(cs)
                        except Exception: pass

                    if chunk_type == 'chain_done':
                        _has_report = True  # 链条完成即视为分析报告
                    elif chunk_type == 'tool_result':
                        try:
                            d = json.loads(chunk_content) if isinstance(chunk_content, str) else chunk_content
                            if d.get('rowCount', 0) > 0:
                                _has_report = True
                        except Exception: pass
                    elif chunk_type == 'alert':
                        _has_alert = True

                if chain_engine.last_plan:
                    resolved_agent_name = chain_engine.last_plan.final_agent or "analysis_monitor"
                    ai_metadata = {"chain_id": chain_engine.last_plan.chain_id, "chain_name": chain_engine.last_plan.name}
                else:
                    resolved_agent_name = "analysis_monitor"
            elif not _ambiguity_handled:
                # 6. 通过 Agent 处理（API endpoint 已做路由，直接使用传入的 agent_name）
                from app.agents import get_agent

                resolved_agent_name = agent_name or "analysis_monitor"
                try:
                    agent = get_agent(resolved_agent_name)
                except KeyError:
                    logger.warning(f"[消息] Agent '{resolved_agent_name}' 不可用（无域配置）")
                    yield ('error', '当前没有可用 Agent，请先配置业务域')
                    return
                agent._session_id = conversation_id

                system_prompt = await agent.build_system_prompt(memory_context)

                async for chunk_type, chunk_content in agent.process(
                    message=message,
                    session_id=conversation_id,
                    model_name=model_name,
                    use_agent=use_agent,
                    web_search=web_search,
                    enable_thinking=enable_thinking,
                    context={"system_prompt": system_prompt} if system_prompt else None,
                    history_messages=history_messages,
                    matched_agents=matched_agents or [],
                    user_id=user_id,
                ):
                    if chunk_type == 'content':
                        full_response += chunk_content
                    elif chunk_type == 'tool_call':
                        try:
                            tool_data = json.loads(chunk_content) if isinstance(chunk_content, str) else chunk_content
                            logger.info(f"[ToolCall] {tool_data.get('name', '')}")
                        except Exception:
                            pass
                        yield (chunk_type, chunk_content)
                    elif chunk_type == 'metadata':
                        try:
                            ai_metadata = json.loads(chunk_content) if isinstance(chunk_content, str) else chunk_content
                        except Exception:
                            pass
                    elif chunk_type == 'plan_start':
                        try:
                            p = json.loads(chunk_content) if isinstance(chunk_content, str) else chunk_content
                            plan_title = p.get("title", "")
                            plan_steps = []
                        except Exception as e:
                            logger.warning(f"[Planning] plan_start parse error: {e}")
                    elif chunk_type == 'plan_step':
                        try:
                            s = json.loads(chunk_content) if isinstance(chunk_content, str) else chunk_content
                            existing_idx = next((i for i, st in enumerate(plan_steps) if st.get("key") == s.get("key")), -1)
                            if existing_idx >= 0:
                                plan_steps[existing_idx].update(s)
                            else:
                                plan_steps.append(s)
                        except Exception as e:
                            logger.warning(f"[Planning] plan_step parse error: {e}")
                    logger.debug(f"[MessageService] chunk_type={chunk_type}")
                    yield (chunk_type, chunk_content)

                    # ── 收集执行链路事件 ──
                    _maybe_capture_exec_step(chunk_type, chunk_content, execution_steps)
                    if chunk_type in _EXEC_STEP_KEYS:
                        logger.info(f"[AGENT捕获] {chunk_type} → exec_steps now={len(execution_steps)}")

                    # ── 收集 Agent 内部链事件（Agent 可能自己触发链引擎） ──
                    if chunk_type == 'chain_start':
                        try:
                            cs = json.loads(chunk_content) if isinstance(chunk_content, str) else chunk_content
                            chain_id = cs.get("chain_id", "")
                            chain_name = cs.get("chain_name", "")
                            is_dynamic = cs.get("dynamic", False)
                            chain_steps = (cs.get("steps") or []).copy()
                            logger.info(f"[Agent链捕获] chain_start: id={chain_id} steps={len(chain_steps)}")
                        except Exception: pass
                    elif chunk_type == 'chain_step':
                        try:
                            cs = json.loads(chunk_content) if isinstance(chunk_content, str) else chunk_content
                            sid = cs.get("step_id", "")
                            idx = next((i for i, s in enumerate(chain_steps) if s.get("step_id") == sid), -1)
                            if idx >= 0:
                                chain_steps[idx].update(cs)
                            else:
                                chain_steps.append(cs)
                        except Exception: pass

                    # ── 直接检测报告消息类型 ──
                    if chunk_type == 'tool_result':
                        try:
                            d = json.loads(chunk_content) if isinstance(chunk_content, str) else chunk_content
                            if d.get('rowCount', 0) > 0:
                                _has_report = True
                        except Exception:
                            pass
                    elif chunk_type == 'alert':
                        _has_alert = True

                    # ── 委托审批：写入 DB 待办 ──
                    if chunk_type == 'confirm_delegated':
                        try:
                            data = json.loads(chunk_content) if isinstance(chunk_content, str) else chunk_content
                            assigned_to = (data.get("assigned_to") or [None])[0]
                            confirm_msg = await self.message_repo.create(
                                conversation_id=conversation_id,
                                role=MessageRole.SYSTEM,
                                content=json.dumps({
                                    "tool": data.get("tool", ""),
                                    "action_label": data.get("action_label", ""),
                                    "concept_label": data.get("concept_label", ""),
                                    "params": data.get("params", {}),
                                    "param_schema": data.get("param_schema", []),
                                    "risk": data.get("risk", "write"),
                                    "context": data.get("context", {}),
                                    "user_id": user_id,
                                    "message": message,
                                }, ensure_ascii=False),
                                message_type=MessageType.CONFIRM.value,
                                status=ConfirmStatus.PENDING.value,
                                assigned_to=assigned_to,
                            )
                            logger.info(f"[Confirm] 委托审批消息已写入 DB: id={confirm_msg.id}, assigned_to={assigned_to}")
                            # 广播事件：前端实时更新待审批列表
                            try:
                                from app.services.event_bus import event_bus
                                await event_bus.publish("pending_updated", {
                                    "conversation_id": conversation_id,
                                    "action": data.get("action_label", ""),
                                    "assigned_to": assigned_to,
                                })
                            except Exception:
                                pass
                        except Exception as e:
                            logger.error(f"[Confirm] 写入委托审批消息失败: {e}")

                logger.info(f"Agent 处理完成，响应长度: {len(full_response)} 字符, exec_steps={len(execution_steps)}, chain_steps={len(chain_steps)}")

                # ── 检测 Agent 路径中的分析报告 ──
                # analysis_monitor 的长响应视为报告；其他 Agent 含多级标题+表格的也视为报告
                if not _has_report and len(full_response) > 300:
                    if resolved_agent_name == "analysis_monitor":
                        _has_report = True
                        logger.info(f"[MessageType] analysis_monitor 长响应 → 标记为 report")
                    elif len(full_response) > 500:
                        # 启发式：至少 2 个标题 + 表格或列表
                        heading_count = len(re.findall(r'^#{1,3}\s', full_response, re.MULTILINE))
                        has_table = '|' in full_response and '---' in full_response
                        if heading_count >= 2 and has_table:
                            _has_report = True
                            logger.info(f"[MessageType] 启发式检测 → 标记为 report (headings={heading_count})")

                # 6.3 Reflection 自我修正
                if hasattr(agent, 'reflect'):
                    logger.info(f"[Reflection] 调用 {resolved_agent_name}.reflect() 自检...")
                    try:
                        reflection_result = await asyncio.wait_for(
                            agent.reflect(message, full_response),
                            timeout=10.0,
                        )
                    except (asyncio.TimeoutError, Exception) as e:
                        logger.warning(f"[Reflection] reflect() 失败: {e}")
                        reflection_result = None

                    if reflection_result:
                        reflection_reason = "检测到响应不足，已自动修正"
                        logger.info(f"[Reflection] {resolved_agent_name} 自我修正: {reflection_reason}")
                        yield ('reflection_start', json.dumps({"reason": reflection_reason}))
                        full_response = reflection_result
                        yield ('reflection_done', '')

            # ── 保存 AI 响应（在 yield 之前，确保持久化）──
            if full_response and not ai_response_saved:
                ai_metadata["agent_name"] = resolved_agent_name
                # 持久化业务域/Agent 信息（优先用业务域配置，否则用 Agent 类定义）
                try:
                    from app.agents.agent_config import AGENT_DEFINITIONS, reload as _reload_ad
                    _reload_ad()
                    info = AGENT_DEFINITIONS.get(resolved_agent_name, {})
                    if info:
                        ai_metadata["agent_info"] = {
                            "name": info.get("name", resolved_agent_name),
                            "display_name": info.get("display_name", resolved_agent_name),
                            "icon": info.get("icon", ""),
                            "color": info.get("color", ""),
                        }
                    else:
                        from app.agents import get_agent
                        _ag = get_agent(resolved_agent_name)
                        ai_metadata["agent_info"] = _ag.get_info()
                except Exception:
                    pass
                if plan_steps:
                    ai_metadata["plan_steps"] = plan_steps
                    ai_metadata["plan_title"] = plan_title
                if reflection_reason:
                    ai_metadata["reflection_reason"] = reflection_reason
                if execution_steps:
                    ai_metadata["execution_steps"] = execution_steps
                if chain_steps:
                    ai_metadata["chain_steps"] = chain_steps
                if chain_id:
                    ai_metadata["chain_id"] = chain_id
                    ai_metadata["chain_name"] = chain_name
                    ai_metadata["is_dynamic"] = is_dynamic

                from app.agents.guardrails import check_output
                is_valid, reject_reason, legacy_code = check_output(full_response)
                if not is_valid:
                    code = ErrorCode.OUTPUT_EMPTY if legacy_code == "empty" else ErrorCode.OUTPUT_TOO_LONG
                    logger.warning(f"[Guardrails] 输出被拒绝 [{code.value}]: {reject_reason}")
                    full_response = f"响应安全检查未通过 [{code.value}]: {reject_reason}"

                # 检测消息类型
                msg_type = MessageType.INFO.value
                if _has_alert:
                    msg_type = MessageType.ALERT.value
                elif _has_report:
                    msg_type = MessageType.REPORT.value
                logger.info(f"[SAVE] exec_steps={len(execution_steps)} chain_steps={len(chain_steps)} plan_steps={len(plan_steps)} ai_metadata_keys={list(ai_metadata.keys())} has_report={_has_report} msg_type={msg_type}")

                # 报告类消息剥离 ```markdown 包裹，避免前端渲染为代码块
                save_content = full_response
                if msg_type == MessageType.REPORT.value:
                    save_content = _strip_markdown_code_wrapper(full_response)

                ai_msg = await self.message_repo.create(
                    conversation_id=conversation_id,
                    role=MessageRole.ASSISTANT,
                    content=save_content,
                    metadata=ai_metadata,
                    message_type=msg_type,
                )
                ai_response_saved = True
                logger.info(f"AI响应已保存，消息ID: {ai_msg.id}")

                yield ('message_id', json.dumps({"id": str(ai_msg.id), "message_type": ai_msg.message_type or ""}))

            # ── 推送事件、清理收尾 ──
            await self._emit_post_response_events(
                conversation_id=conversation_id,
                user_id=user_id,
                user_msg_id=str(user_msg.id) if user_msg else "",
                message=message,
                full_response=full_response,
                new_summary=new_summary,
                enable_memory=enable_memory,
            )

            logger.info(f"Message processed successfully for conversation {conversation_id}")

        except Exception as e:
            import traceback
            logger.error(f"Failed to process message: {e}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            code = classify_exception(e)
            yield ('error', json.dumps(sse_error(code, str(e)), ensure_ascii=False))

        finally:
            # 兜底保存：即使 SSE 流被取消也要持久化 AI 响应
            if full_response and not ai_response_saved:
                try:
                    ai_metadata["agent_name"] = resolved_agent_name
                    try:
                        from app.agents import get_agent
                        _ag = get_agent(resolved_agent_name)
                        ai_metadata["agent_info"] = _ag.get_info()
                    except Exception:
                        pass
                    if plan_steps:
                        ai_metadata["plan_steps"] = plan_steps
                        ai_metadata["plan_title"] = plan_title
                    if reflection_reason:
                        ai_metadata["reflection_reason"] = reflection_reason
                    if execution_steps:
                        ai_metadata["execution_steps"] = execution_steps
                    if chain_steps:
                        ai_metadata["chain_steps"] = chain_steps
                    if chain_id:
                        ai_metadata["chain_id"] = chain_id
                        ai_metadata["chain_name"] = chain_name
                        ai_metadata["is_dynamic"] = is_dynamic

                    # 检测消息类型
                    _fallback_type = MessageType.INFO.value
                    if _has_alert:
                        _fallback_type = MessageType.ALERT.value
                    elif _has_report:
                        _fallback_type = MessageType.REPORT.value

                    # 报告类消息剥离 ```markdown 包裹
                    _fallback_content = full_response
                    if _fallback_type == MessageType.REPORT.value:
                        _fallback_content = _strip_markdown_code_wrapper(full_response)

                    await self.message_repo.create(
                        conversation_id=conversation_id,
                        role=MessageRole.ASSISTANT,
                        content=_fallback_content,
                        metadata=ai_metadata,
                        message_type=_fallback_type,
                    )
                    logger.info(f"[兜底] AI 响应已保存 (finally 块, conv={conversation_id})")
                except Exception as save_err:
                    logger.error(f"[兜底] 保存 AI 响应失败: {save_err}")


    # ── process_message_stream 辅助方法 ──────────────────────────

    async def _build_memory_context(
        self,
        user_id: str,
        message: str,
        conversation_id: str,
        enable_memory: bool,
    ) -> Optional[str]:
        """检索长期记忆并格式化为上下文字符串 (原步骤 1-2)"""
        if not (enable_memory and settings.MEMORY_ENABLED and settings.MEMORY_AUTO_INJECT):
            return None

        try:
            memories = await asyncio.wait_for(
                self._retrieve_memories(user_id, message, conversation_id),
                timeout=2.0,
            )
        except asyncio.TimeoutError:
            logger.warning("记忆检索超时，跳过")
            return None

        if not memories:
            return None

        logger.info(f"检索到 {len(memories)} 条记忆用于上下文")
        context = "\n\n## 相关历史记忆\n\n"
        for i, memory in enumerate(memories, 1):
            context += f"{i}. [{memory.role}] {memory.content}\n"
        context += "\n请参考以上相关历史记忆来回答用户的问题。\n"
        return context

    async def _emit_post_response_events(
        self,
        conversation_id: str,
        user_id: str,
        user_msg_id: str,
        message: str,
        full_response: str,
        new_summary: Optional[str],
        enable_memory: bool,
    ) -> None:
        """推送审批事件、更新计数/摘要/标题、存储向量。AI 消息已在此方法调用前保存。"""
        # 检查是否有待审批请求（Andon等高风险操作）
        from app.agents.approval import ApprovalManager
        pending = ApprovalManager.list_pending()
        if pending:
            approval = pending[-1]
            logger.info(f"[审批流] 发现待审批请求: {approval['approval_id']}")

        await self.conversation_repo.increment_message_count(conversation_id)
        await self._update_summary_if_needed(conversation_id, new_summary)

        # 自动生成标题（第一条消息）
        conversation = await self.conversation_repo.get_by_id(conversation_id)
        if conversation and conversation.message_count == 1:
            title = message[:20] + ("..." if len(message) > 20 else "")
            await self.conversation_repo.update(conversation_id, title=title)
            logger.info(f"自动生成标题: {title}")

        # 存储向量
        if enable_memory and settings.MEMORY_ENABLED:
            await self._store_vectors_with_delay(
                user_id=user_id,
                conversation_id=conversation_id,
                user_message_id=user_msg_id,
                user_content=message,
                ai_content=full_response,
            )

    async def _retrieve_memories(
        self,
        user_id: str,
        query: str,
        conversation_id: str
    ) -> List:
        """
        检索长期记忆

        Args:
            user_id: 用户ID
            query: 查询文本
            conversation_id: 会话ID

        Returns:
            记忆列表
        """
        try:
            memories = await vector_memory_service.retrieve_with_fallback(
                user_id=user_id,
                query=query,
                conversation_id=conversation_id,
                top_k=settings.MEMORY_TOP_K,
                similarity_threshold=settings.MEMORY_SIMILARITY_THRESHOLD
            )
            return memories
        except Exception as e:
            logger.error(f"记忆检索失败: {e}")
            return []

    async def _store_vectors_with_delay(
        self,
        user_id: str,
        conversation_id: str,
        user_message_id: str,
        user_content: str,
        ai_content: str,
    ) -> None:
        """等待 DB 保存完成后存储向量"""
        try:
            is_duplicate = await vector_memory_service.check_duplicate(
                user_id=user_id,
                content=user_content
            )
            if not is_duplicate:
                await vector_memory_service.store(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    message_id=user_message_id,
                    content=user_content,
                    role="user"
                )
            await vector_memory_service.store(
                user_id=user_id,
                conversation_id=conversation_id,
                message_id="pending",
                content=ai_content,
                role="assistant"
            )
            logger.debug(f"向量已存储，会话: {conversation_id}")
        except Exception as e:
            logger.error(f"向量存储失败: {e}")


