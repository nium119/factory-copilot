"""
消息API
提供消息发送接口，支持 Agent 路由
"""
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agents import get_agents_from_db
from app.core.config import settings
from app.core.logger import log
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.message_repository import MessageRepository
from app.services.conversation_service import ConversationService
from app.services.message_service import MessageService

router = APIRouter(prefix="/messages", tags=["消息"])


@router.get("/events/stream")
async def event_stream():
    """全局 SSE 事件流 — 审批状态变更实时推送。"""
    from app.services.event_bus import event_bus
    return StreamingResponse(
        event_bus.subscribe(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


class SendMessageRequest(BaseModel):
    """发送消息请求"""
    conversation_id: str
    content: str
    model_name: Optional[str] = None
    agent_name: Optional[str] = None
    use_agent: bool = False
    web_search: bool = False
    enable_memory: bool = True
    enable_thinking: Optional[bool] = None


# 模块级引擎和会话工厂，应用启动时创建一次

_engine = create_async_engine(settings.DATABASE_URL, echo=False)
_async_session = async_sessionmaker(_engine, expire_on_commit=False)


async def get_db() -> AsyncSession:
    """获取数据库会话（复用全局引擎）"""
    async with _async_session() as session:
        yield session


def get_message_service(db: AsyncSession = Depends(get_db)) -> MessageService:
    """获取消息服务实例"""
    message_repo = MessageRepository(db)
    conversation_repo = ConversationRepository(db)
    return MessageService(message_repo, conversation_repo)


def get_conversation_service(db: AsyncSession = Depends(get_db)) -> ConversationService:
    """获取会话服务实例"""
    conversation_repo = ConversationRepository(db)
    message_repo = MessageRepository(db)
    return ConversationService(conversation_repo, message_repo)


def get_current_user_id(request: Request) -> str:
    """从请求 Header 解析当前用户 ID，同时设置 JWT claims ContextVar。

    优先级: X-User-Id > Bearer token (JWT) 会话映射 > default_user。
    """
    from app.services.multi_system_backend import _request_claims, _parse_jwt_claims
    # 前端登录后通过 X-User-Id 直接传递用户标识
    user_id = request.headers.get("X-User-Id", "").strip()
    if user_id:
        return user_id
    # 回退: 从 Bearer token 解析 JWT claims
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
        # 尝试解析 JWT claims
        claims = _parse_jwt_claims(token)
        if claims:
            _request_claims.set(claims)
            user_id = claims.get("sub") or claims.get("userId") or claims.get("nameid", "")
            if user_id:
                return user_id
        # 兼容旧 session 映射
        from app.services.auth_service import auth_service as _auth_svc
        user_id = _auth_svc.resolve_user(token)
        if user_id:
            return user_id
    return "default_user"


class ConfirmRequest(BaseModel):
    """确认请求"""
    approved: bool = True
    params: Optional[dict] = None


class PendingQuery(BaseModel):
    """待办查询参数"""
    user_id: str = ""
    user_roles: str = ""  # 逗号分隔的角色列表


class ApprovalRequest(BaseModel):
    """审批请求"""
    user_id: str = ""
    comment: str = ""


@router.post("/confirm/{session_id}", summary="确认或取消写操作")
async def confirm_action(session_id: str, request: ConfirmRequest):
    """前端确认/取消高危操作。

    后端 _standard_process 在执行 requiresConfirmation 的 Action 前
    会挂起等待此端点。超时 60s 自动取消。
    """
    from app.agents.base import BaseAgent
    # 先读消息和操作名（resolve 会 pop 掉数据）
    entry = BaseAgent._pending_confirmations.get(session_id, {})
    action_label = entry.get("action_label", "") if isinstance(entry, dict) else ""
    user_message = entry.get("message", "") if isinstance(entry, dict) else ""
    resolved = BaseAgent.resolve_confirmation(session_id, request.approved, request.params)
    # 取消时记录负反馈
    if not request.approved and action_label:
        from app.db import get_db
        from app.models.intent_feedback import IntentFeedback
        async for db_session in get_db():
            db_session.add(IntentFeedback(
                message=user_message,
                matched_action=action_label,
                was_correct=0,
            ))
            await db_session.commit()
            break
    return {"resolved": resolved, "session_id": session_id, "approved": request.approved}


@router.get("/agents", summary="获取可用 Agent 列表")
async def list_agents():
    """从数据库获取所有已注册的 Agent 元信息"""
    from app.db import get_db
    from app.repositories.agent_repository import AgentRepository
    async for session in get_db():
        repo = AgentRepository(session)
        agents = await repo.get_enabled_agents()
        result = []
        for a in agents:
            try:
                from app.agents import get_agent
                agent = get_agent(a.name)
                info = agent.get_info()
                info["enabled"] = a.enabled
                result.append(info)
            except (KeyError, Exception):
                result.append({
                    "name": a.name, "display_name": a.display_name,
                    "icon": a.icon, "color": a.color,
                    "description": a.description, "enabled": a.enabled,
                })
        return result


@router.post("/stream", summary="流式发送消息")
async def send_message_stream(
    request: SendMessageRequest,
    http_request: Request,
    message_service: MessageService = Depends(get_message_service),
    user_id: str = Depends(get_current_user_id)
):
    """
    通过 SSE 流式发送消息并逐步接收 AI 回复。

    返回的 SSE 事件类型：
    - **agent_info**: Agent 元信息（首次发送）
    - **content**: AI 回复的文本片段
    - **thinking**: 模型思考过程
    - **error**: 错误信息
    - **[DONE]**: 传输完成标记
    """

    # 从请求头提取 MES token，传递给 CLI 子进程
    mes_token = None
    auth_header = http_request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        mes_token = auth_header[7:]

    async def event_generator():
        """SSE事件生成器"""
        # 设置 API 调用日志的请求上下文
        from app.services.multi_system_backend import _request_user_id, _request_conversation_id, _request_message
        _request_user_id.set(user_id or "")
        _request_conversation_id.set(request.conversation_id)
        _request_message.set(request.content[:200])
        try:
            from app.agents import get_agent
            from app.agents.router import route_intent
            from app.agents.tools.mes_cli_runner import set_token

            set_token(mes_token)

            route = await route_intent(request.content, request.agent_name)
            agent_name = route["agent_name"]
            use_agent = route["use_agent"]
            matched_agents = route.get("matched_agents", [])

            # 用户偏好适应
            if route["confidence"] < 0.9:
                try:
                    async with _async_session() as adapt_db:
                        from app.services.adaptation_service import get_adapted_confidence
                        adapted_confidence = await get_adapted_confidence(
                            adapt_db, user_id, agent_name, route["confidence"]
                        )
                        if adapted_confidence < 0.3:
                            log.info(
                                f"[Adaptation] 用户偏好负向，{agent_name} 置信度过低 ({adapted_confidence})，"
                                f"回退到 analysis_monitor"
                            )
                            agent_name = "analysis_monitor"
                            use_agent = False
                except Exception as e:
                    log.debug(f"[Adaptation] 偏好调整跳过: {e}")

            # 资源感知：根据查询复杂度自动选择模型
            from app.agents.router import select_model_for_complexity
            model_name = select_model_for_complexity(request.content, request.model_name)
            if model_name:
                log.info(f"[资源优化] 自动选择模型: {model_name} (基于查询复杂度)")
            else:
                model_name = request.model_name

            try:
                agent = get_agent(agent_name)
            except KeyError:
                log.warning(f"[SSE] Agent '{agent_name}' 不可用（无域配置）")
                yield f"data: {json.dumps({'type': 'error', 'content': '当前没有可用 Agent，请先配置业务域'})}\n\n"
                yield "data: [DONE]\n\n"
                return
            agent_info = agent.get_info()

            # 发送 Agent 信息
            log.info(f"[SSE] 发送 agent_info: {agent_info['display_name']}")
            yield f"data: {json.dumps({'type': 'agent_info', 'agent_name': agent_info['name'], 'display_name': agent_info['display_name'], 'icon': agent_info['icon'], 'color': agent_info['color']})}\n\n"

            # 通过 MessageService 处理消息（包含记忆、数据库持久化）
            # 传递已路由的 agent_name，避免 service 层重复路由（LLM 调用导致延迟）
            async for chunk_type, chunk_content in message_service.process_message_stream(
                user_id=user_id,
                conversation_id=request.conversation_id,
                message=request.content,
                model_name=model_name,
                use_agent=use_agent,
                web_search=request.web_search,
                enable_memory=request.enable_memory,
                agent_name=agent_name,
                enable_thinking=request.enable_thinking,
                matched_agents=matched_agents,
            ):
                log.info(f"[SSE] 发送 chunk_type={chunk_type}, content_len={len(str(chunk_content))}")
                yield f"data: {json.dumps({'type': chunk_type, 'content': chunk_content})}\n\n"

            # 发送数据来源信息 + 恢复建议
            from app.agents.tools.mes_cli_runner import get_data_source, get_error_recovery_hint
            ds = get_data_source()
            hint = get_error_recovery_hint() if ds == "mock_fallback" else None
            yield f"data: {json.dumps({'type': 'data_source', 'source': ds, 'hint': hint})}\n\n"

            # 发送结束标记
            log.info("[SSE] 发送 [DONE]")
            yield "data: [DONE]\n\n"

        except Exception as e:
            error_data = json.dumps({
                "type": "error",
                "content": str(e)
            })
            yield f"data: {error_data}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


@router.get("/pending")
async def get_pending_confirmations(
    user_id: str = "",
    user_roles: str = "",
    db: AsyncSession = Depends(get_db),
):
    """获取当前用户可审批的待办消息列表。"""
    from app.repositories.message_repository import MessageRepository
    repo = MessageRepository(db)

    # 解析用户角色
    roles = [r.strip() for r in user_roles.split(",") if r.strip()] if user_roles else []

    # 查询待审批消息
    all_pending = await repo.get_pending_confirmations(assigned_to=None, limit=100)

    # 过滤：无角色时显示所有待审批；有角色时只显示匹配的
    result = []
    for msg in all_pending:
        assigned = msg.assigned_to or ""
        if not roles or not assigned or assigned in roles:
            content_data = {}
            try:
                content_data = json.loads(msg.content) if msg.content else {}
            except Exception:
                content_data = {"raw": msg.content}
            result.append({
                "id": msg.id,
                "conversation_id": msg.conversation_id,
                "action_label": content_data.get("action_label", ""),
                "concept_label": content_data.get("concept_label", ""),
                "tool": content_data.get("tool", ""),
                "params": content_data.get("params", {}),
                "param_schema": content_data.get("param_schema", []),
                "risk": content_data.get("risk", "write"),
                "context": content_data.get("context", {}),
                "user_id": content_data.get("user_id", ""),
                "message": content_data.get("message", ""),
                "assigned_to": assigned,
                "created_at": str(msg.created_at) if msg.created_at else "",
            })

    return {"pending": result, "total": len(result), "have_param_schema": True}


@router.get("/reports")
async def get_reports(page: int = 1, page_size: int = 20, db: AsyncSession = Depends(get_db)):
    """获取历史分析报告（message_type=report 的消息），按时间倒序，分页。"""
    from app.repositories.message_repository import MessageRepository
    from app.models.message import Message
    from sqlalchemy import select, func
    import json
    repo = MessageRepository(db)
    q = select(Message).where(
        Message.message_type == "report"
    ).order_by(Message.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    r = await db.execute(q)
    items = list(r.scalars().all())
    result = []
    for msg in items:
        # 查会话标题
        conv_title = ""
        user_question = ""
        try:
            from app.repositories.conversation_repository import ConversationRepository
            conv_repo = ConversationRepository(db)
            conv = await conv_repo.get_by_id(msg.conversation_id)
            if conv:
                conv_title = conv.title or ""
        except Exception:
            pass
        result.append({
            "id": msg.id,
            "conversation_id": msg.conversation_id,
            "content": msg.content,
            "title": conv_title,
            "created_at": str(msg.created_at) if msg.created_at else "",
        })
    # 总数
    cq = select(func.count()).where(Message.message_type == "report")
    cr = await db.execute(cq)
    total = cr.scalar() or 0
    return {"reports": result, "total": total, "page": page, "page_size": page_size}


@router.get("/reports/{report_id}/export")
async def export_report(
    report_id: str,
    format: str = "pdf",
    db: AsyncSession = Depends(get_db),
):
    """导出报告为 PDF 或 Word 格式。

    GET /api/messages/reports/{id}/export?format=pdf   → application/pdf
    GET /api/messages/reports/{id}/export?format=docx  → Word 文档
    """
    from fastapi.responses import Response
    from app.repositories.message_repository import MessageRepository

    repo = MessageRepository(db)
    msg = await repo.get_by_id(report_id)
    if not msg:
        raise HTTPException(status_code=404, detail="报告不存在")

    md_content = msg.content or ""
    title = ""
    try:
        from app.repositories.conversation_repository import ConversationRepository
        conv_repo = ConversationRepository(db)
        conv = await conv_repo.get_by_id(msg.conversation_id)
        if conv:
            title = conv.title or "分析报告"
    except Exception:
        title = "分析报告"

    if format == "pdf":
        # PDF：生成自包含 HTML 页面，CDN 加载 ECharts/Mermaid 在浏览器端渲染真实图表后打印
        html = _build_print_html(md_content, title)
        return Response(content=html, media_type="text/html; charset=utf-8")

    elif format == "docx":
        # Word：ECharts → 原生 Office 图表，Mermaid → mermaid.ink SVG
        try:
            docx_bytes = _build_docx_with_charts(md_content, title)
            filename = f"{title}.docx"
            return Response(content=docx_bytes,
                            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{_url_quote(filename)}"})
        except Exception as e:
            log.warning(f"Word 生成失败: {e}")
            raise HTTPException(status_code=500, detail=f"Word 生成失败: {e}")

    else:
        # HTML 预览
        html_body = _md_to_docx_html(md_content)
        html = _build_print_html(md_content, title).replace(
            '<script>window.addEventListener', '<!--')
        return Response(content=html, media_type="text/html; charset=utf-8")


def _build_print_html(md_text: str, title: str) -> str:
    """生成自包含 HTML 页面，CDN 加载 ECharts/Mermaid 在浏览器端渲染真实图表后自动打印。"""
    import re as _re
    import json as _json
    import base64

    # 提取所有 echarts/mermaid 块的索引和内容，替换为容器 div
    echarts_blocks = []
    mermaid_blocks = []

    def _collect_echarts(match):
        code = match.group(1).strip()
        idx = len(echarts_blocks)
        echarts_blocks.append(code)
        return f'<div id="echarts-{idx}" class="echarts-chart" style="width:100%;min-height:350px;margin:16px 0;"></div>'

    def _collect_mermaid(match):
        code = match.group(1).strip()
        idx = len(mermaid_blocks)
        mermaid_blocks.append(code)
        return f'<div class="mermaid" style="margin:16px 0;">{code}</div>'

    processed = _re.sub(r'```echarts\s*\n(.*?)\n```', _collect_echarts, md_text, flags=_re.DOTALL)
    processed = _re.sub(r'```mermaid\s*\n(.*?)\n```', _collect_mermaid, processed, flags=_re.DOTALL)

    # Markdown → HTML
    import markdown as _md
    html_body = _md.markdown(processed, extensions=['tables', 'fenced_code', 'codehilite', 'toc'])

    # 构建 echarts 初始化 JS
    echarts_js = ""
    if echarts_blocks:
        opts_json = []
        for i, code in enumerate(echarts_blocks):
            opt = _parse_echarts_option(code)
            opts_json.append(opt)
        echarts_js = f"""
<script>
let chartOpts = {_json.dumps(opts_json, ensure_ascii=False)};
let chartsReady = 0;
let totalCharts = chartOpts.length;
function initEcharts() {{
    chartOpts.forEach(function(opt, i) {{
        let el = document.getElementById('echarts-' + i);
        if (el && opt && Object.keys(opt).length > 0) {{
            let chart = echarts.init(el);
            chart.setOption(opt);
            chartsReady++;
        }}
    }});
    tryAutoPrint();
}}
</script>"""

    # 构建 mermaid JS
    mermaid_js = ""
    if mermaid_blocks:
        mermaid_js = """
<script>
let mermaidReady = false;
mermaid.initialize({{ startOnLoad: true, theme: 'default', securityLevel: 'loose' }});
</script>"""

    # 自动打印逻辑：先调 initEcharts 渲染图表，等 echarts + mermaid 完成后触发打印
    auto_print_js = f"""
<script>
let echartsDone = {str(not echarts_blocks).lower()};
let mermaidDone = {str(not mermaid_blocks).lower()};
function tryAutoPrint() {{
    if (echartsDone && mermaidDone) {{
        setTimeout(function() {{ window.print(); }}, 600);
    }}
}}
// 初始化图表并设置完成回调
document.addEventListener('DOMContentLoaded', function() {{
    if (typeof initEcharts === 'function') {{
        initEcharts();
        echartsDone = true;
    }}
    tryAutoPrint();
}});
// Mermaid polls async completion
let mermaidCheck = setInterval(function() {{
    let svgs = document.querySelectorAll('.mermaid svg');
    if (svgs.length >= {len(mermaid_blocks)}) {{
        mermaidDone = true;
        clearInterval(mermaidCheck);
        tryAutoPrint();
    }}
}}, 200);
// Fallback: auto-print after 10s regardless
setTimeout(function() {{
    echartsDone = true; mermaidDone = true;
    tryAutoPrint();
}}, 10000);
</script>"""

    # CSS 样式表
    css = """
body { font-family: "Microsoft YaHei", "SimSun", sans-serif; font-size: 14px; line-height: 1.8;
       max-width: 960px; margin: 30px auto; padding: 0 24px; color: #333; }
h1 { font-size: 1.8em; border-bottom: 2px solid #2563eb; padding-bottom: 8px; color: #1e3a5f; }
h2 { font-size: 1.4em; border-bottom: 1px solid #e0e0e0; padding-bottom: 6px; color: #333; margin-top: 28px; }
h3 { font-size: 1.15em; color: #444; }
table { border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 13px; }
th, td { border: 1px solid #d0d0d0; padding: 6px 10px; text-align: left; }
th { background: #e8ecf1; font-weight: 600; }
tr:nth-child(even) { background: #f8f9fb; }
pre { background: #f4f5f7; padding: 12px; border-radius: 4px; overflow-x: auto; font-size: 13px; }
code { background: #f0f0f0; padding: 1px 4px; border-radius: 2px; font-size: 0.9em; }
blockquote { border-left: 3px solid #6c5ce7; margin: 12px 0; padding: 4px 16px; color: #555; background: #f8f7ff; }
img { max-width: 100%; }
.echarts-chart { border: 1px solid #e8e8e8; border-radius: 8px; background: #fafafa; }
@media print {
    body { max-width: 100%; margin: 0; padding: 0 12px; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
    .echarts-chart { break-inside: avoid; }
    h1, h2, h3 { break-after: avoid; }
    table { break-inside: avoid; }
}"""

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
<style>{css}</style>
</head>
<body>
<h1>{title}</h1>
{html_body}
{echarts_js}
{mermaid_js}
{auto_print_js}
</body>
</html>"""


def _md_to_docx_html(md_text: str) -> str:
    """Markdown → HTML（DOCX 导出用：echarts → 表格，mermaid → 占位图片）。"""
    return _md_to_html(md_text)


def _parse_echarts_option(js_text: str) -> dict:
    """将 ECharts JS option 对象转为 Python dict（简易解析器）。"""
    import json as _json
    text = js_text.strip()
    # 先尝试直接 JSON 解析
    try:
        return _json.loads(text)
    except Exception:
        pass
    # 尝试 JS 对象字面量转 JSON
    text = _re.sub(r'^\s*option\s*=\s*', '', text)
    text = _re.sub(r';\s*$', '', text)
    text = _re.sub(r'(\s)(\w+)(\s*:)', r'\1"\2"\3', text)
    text = _re.sub(r'^(\w+)(\s*:)', r'"\1"\2', text)
    text = text.replace("'", '"')
    text = _re.sub(r',(\s*[}\]])', r'\1', text)
    try:
        return _json.loads(text)
    except Exception:
        return {}


def _render_echarts_blocks(md_text: str) -> str:
    """将 ```echarts 代码块渲染为 HTML 表格。"""
    import re as _re

    def _render_echarts(match):
        code = match.group(1)
        opt = _parse_echarts_option(code)
        if not opt:
            return f'<pre><code>{code}</code></pre>'

        title = ""
        if "title" in opt and isinstance(opt["title"], dict):
            title = opt["title"].get("text", "")
        elif "title" in opt and isinstance(opt["title"], str):
            title = opt["title"]

        # 提取 xAxis 类别
        categories = []
        xaxis = opt.get("xAxis", {})
        if isinstance(xaxis, dict):
            categories = xaxis.get("data", [])
        elif isinstance(xaxis, list) and len(xaxis) > 0:
            categories = xaxis[0].get("data", []) if isinstance(xaxis[0], dict) else []

        # 提取 series
        series_list = opt.get("series", [])
        if isinstance(series_list, dict):
            series_list = [series_list]

        # 构建 HTML 表格
        rows = []
        if title:
            rows.append(f'<tr><th colspan="{max(2, len(series_list) + 1)}" style="text-align:center;background:#f0f0f0;">{title}</th></tr>')

        if categories:
            header = "<tr><th></th>"
            for s in series_list:
                header += f'<th>{s.get("name", "")}</th>'
            header += "</tr>"
            rows.append(header)

            max_len = len(categories)
            for i in range(max_len):
                row = f"<td>{categories[i] if i < len(categories) else ''}</td>"
                for s in series_list:
                    data = s.get("data", [])
                    val = data[i] if i < len(data) else ""
                    row += f"<td>{val}</td>"
                rows.append(f"<tr>{row}</tr>")
        else:
            # 无类别的简单序列
            header = "<tr>"
            for s in series_list:
                header += f'<th>{s.get("name", "")}</th>'
            header += "</tr>"
            rows.append(header)
            max_len = max((len(s.get("data", [])) for s in series_list), default=0)
            for i in range(max_len):
                row = ""
                for s in series_list:
                    data = s.get("data", [])
                    row += f"<td>{data[i] if i < len(data) else ''}</td>"
                rows.append(f"<tr>{row}</tr>")

        chart_type = series_list[0].get("type", "chart") if series_list else "chart"
        return (
            f'<div style="margin:16px 0;padding:12px;border:1px solid #e0e0e0;border-radius:6px;background:#fafafa;">'
            f'<div style="font-size:12px;color:#888;margin-bottom:8px;">📊 {chart_type} 图表</div>'
            f'<table style="width:100%;font-size:13px;">{"".join(rows)}</table></div>'
        )

    return _re.sub(r'```echarts\s*\n(.*?)\n```', _render_echarts, md_text, flags=_re.DOTALL)


def _render_mermaid_blocks(md_text: str) -> str:
    """将 ```mermaid 代码块渲染为 mermaid.ink 图片。"""
    import base64, re as _re, zlib

    def _render_mermaid(match):
        code = match.group(1).strip()
        # 使用 mermaid.ink API: pako.deflate → base64url
        compressed = zlib.compress(code.encode("utf-8"))[2:-4]  # 去掉 zlib header/trailer，等同 raw deflate
        encoded = base64.urlsafe_b64encode(compressed).decode("ascii").rstrip("=")
        url = f"https://mermaid.ink/svg/{encoded}"
        return (
            f'<div style="margin:16px 0;text-align:center;">'
            f'<img src="{url}" alt="流程图" style="max-width:100%;border:1px solid #e0e0e0;border-radius:6px;" />'
            f'<div style="font-size:11px;color:#999;margin-top:4px;">流程图</div></div>'
        )

    return _re.sub(r'```mermaid\s*\n(.*?)\n```', _render_mermaid, md_text, flags=_re.DOTALL)


def _md_to_html(md_text: str) -> str:
    """Markdown → HTML（含 echarts/mermaid 占位处理）。"""
    import re as _re
    # 替换 echarts → HTML 表格，mermaid → 图片
    cleaned = _render_echarts_blocks(md_text)
    cleaned = _render_mermaid_blocks(cleaned)
    try:
        import markdown as _md
        return _md.markdown(cleaned, extensions=['tables', 'fenced_code', 'codehilite', 'toc'])
    except ImportError:
        # 无 markdown 库时简单处理
        return f"<pre>{cleaned}</pre>"


def _build_docx_with_charts(md_text: str, title: str) -> bytes:
    """将 Markdown 报告转为 .docx，ECharts → 格式化表格，Mermaid → mermaid.ink SVG 图片。"""
    import re as _re
    import io, base64, zlib
    from docx import Document
    from docx.shared import Pt, Cm, Inches, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    doc = Document()

    # 页面设置
    section = doc.sections[0]
    section.page_width = Cm(21)
    section.page_height = Cm(29.7)
    section.left_margin = Cm(2.5)
    section.right_margin = Cm(2.5)

    # 标题
    title_para = doc.add_heading(title, level=0)
    title_para.alignment = WD_ALIGN_PARAGRAPH.CENTER

    # ── 内联 Markdown → Word 格式化段落 ──
    def _add_rich_paragraph(text: str, style=None, indent=None, italic_all=False, color=None):
        """解析 **粗体** *斜体* `代码` [链接](url) <br> 并生成 Word 段落。"""
        # <br> → 换行
        text = _re.sub(r'<br\s*/?>', '\n', text, flags=_re.IGNORECASE)
        p = doc.add_paragraph(style=style)
        if indent:
            p.paragraph_format.left_indent = Cm(indent)
        if not text:
            return p

        # 正则匹配 inline 元素
        pattern = r'(\*\*(.+?)\*\*|'
        pattern += r'\*(.+?)\*|'
        pattern += r'`(.+?)`|'
        pattern += r'\[([^\]]+)\]\(([^)]+)\))'

        last = 0
        for m in _re.finditer(pattern, text):
            # 前面的普通文本
            if m.start() > last:
                plain = text[last:m.start()]
                if plain:
                    run = p.add_run(plain)
                    if italic_all:
                        run.italic = True
                    if color:
                        run.font.color.rgb = color

            # 粗体 **...**
            if m.group(2):
                run = p.add_run(m.group(2))
                run.bold = True
                if italic_all:
                    run.italic = True
                if color:
                    run.font.color.rgb = color
            # 斜体 *...*
            elif m.group(3):
                run = p.add_run(m.group(3))
                run.italic = True
                if color:
                    run.font.color.rgb = color
            # 代码 `...`
            elif m.group(4):
                run = p.add_run(m.group(4))
                run.font.name = 'Consolas'
                run.font.size = Pt(9)
                if color:
                    run.font.color.rgb = color
            # 链接 [...](url)
            elif m.group(5):
                run = p.add_run(m.group(5))
                run.underline = True
                run.font.color.rgb = RGBColor(0x25, 0x63, 0xEB)
                if italic_all:
                    run.italic = True

            last = m.end()

        # 尾部普通文本
        if last < len(text):
            plain = text[last:]
            if plain:
                run = p.add_run(plain)
                if italic_all:
                    run.italic = True
                if color:
                    run.font.color.rgb = color

        p.paragraph_format.space_after = Pt(6)
        return p

    # 按行解析 markdown，追踪状态
    lines = md_text.split('\n')
    in_code_block = False
    code_block_type = None
    code_block_lines = []
    in_table = False
    table_rows = []

    def _fill_cell_rich(cell, text: str, bold_all=False, font_size=Pt(10)):
        """填充单元格，解析内联 Markdown 格式和 <br> 换行。"""
        # 清除默认空段落
        for p in cell.paragraphs:
            p.clear()
        # <br> → 换行符
        text = _re.sub(r'<br\s*/?>', '\n', text, flags=_re.IGNORECASE)
        p = cell.paragraphs[0]
        # 匹配 **bold** *italic* `code`
        pattern = r'(\*\*(.+?)\*\*|\*(.+?)\*|`(.+?)`)'
        last = 0
        for m in _re.finditer(pattern, text):
            if m.start() > last:
                run = p.add_run(text[last:m.start()])
                run.font.size = font_size
                if bold_all: run.bold = True
            if m.group(2):  # **bold**
                run = p.add_run(m.group(2))
                run.bold = True
                run.font.size = font_size
            elif m.group(3):  # *italic*
                run = p.add_run(m.group(3))
                run.italic = True
                run.font.size = font_size
            elif m.group(4):  # `code`
                run = p.add_run(m.group(4))
                run.font.name = 'Consolas'
                run.font.size = Pt(9)
            last = m.end()
        if last < len(text):
            run = p.add_run(text[last:])
            run.font.size = font_size
            if bold_all: run.bold = True

    def _set_cell_margins(cell, top=60, bottom=60, left=80, right=80):
        """设置单元格内边距 + 垂直居中。"""
        from docx.oxml.ns import qn
        tc = cell._element.get_or_add_tcPr()
        # 垂直居中
        vAlign = tc.makeelement(qn('w:vAlign'), {qn('w:val'): 'center'})
        tc.append(vAlign)
        # 内边距
        tcMar = tc.makeelement(qn('w:tcMar'), {})
        for side, val in [('top', top), ('bottom', bottom), ('left', left), ('right', right)]:
            el = tcMar.makeelement(qn(f'w:{side}'), {qn('w:w'): str(val), qn('w:type'): 'dxa'})
            tcMar.append(el)
        tc.append(tcMar)

    def _flush_table():
        nonlocal in_table, table_rows
        if not table_rows or len(table_rows) < 2:
            table_rows = []
            in_table = False
            return
        header = [c.strip() for c in table_rows[0].split('|') if c.strip()]
        data_rows = []
        for tr in table_rows[1:]:
            cells = [c.strip() for c in tr.split('|') if c.strip()]
            if cells:
                data_rows.append(cells)
        if not header or not data_rows:
            table_rows = []
            in_table = False
            return
        tbl = doc.add_table(rows=1 + len(data_rows), cols=len(header), style='Table Grid')
        tbl.autofit = True
        from docx.oxml.ns import qn
        for j, h in enumerate(header):
            cell = tbl.rows[0].cells[j]
            _fill_cell_rich(cell, h, bold_all=True)
            _set_cell_margins(cell)
            shading = cell._element.get_or_add_tcPr()
            shd = shading.makeelement(qn('w:shd'), {qn('w:fill'): 'E8ECF1', qn('w:val'): 'clear'})
            shading.append(shd)
        for i, row in enumerate(data_rows):
            for j, val in enumerate(row):
                if j < len(header):
                    cell = tbl.rows[i + 1].cells[j]
                    _fill_cell_rich(cell, val)
                    _set_cell_margins(cell)
        doc.add_paragraph()
        table_rows = []
        in_table = False

    def _flush_code_block():
        nonlocal in_code_block, code_block_type, code_block_lines
        code = '\n'.join(code_block_lines)
        if code_block_type == 'echarts':
            _add_matplotlib_chart(doc, code)
        elif code_block_type == 'mermaid':
            _add_mermaid_image(doc, code)
        else:
            # 普通代码块
            p = doc.add_paragraph()
            run = p.add_run(code)
            run.font.size = Pt(9)
            run.font.name = 'Consolas'
            p.paragraph_format.space_before = Pt(4)
            p.paragraph_format.space_after = Pt(4)
        code_block_lines = []
        code_block_type = None
        in_code_block = False

    for line in lines:
        # 代码块
        if line.strip().startswith('```'):
            if in_code_block:
                _flush_code_block()
            else:
                in_code_block = True
                code_block_type = line.strip()[3:].strip() or None
            continue

        if in_code_block:
            code_block_lines.append(line)
            continue

        # 表格
        if line.strip().startswith('|') and line.strip().endswith('|'):
            if not in_table:
                _flush_table()  # flush any pending
                in_table = True
            # 跳过分隔行
            if not _re.match(r'^[\|\s\-:]+$', line.strip()):
                table_rows.append(line)
            continue
        elif in_table:
            _flush_table()

        # 空行
        if not line.strip():
            continue

        # 标题
        h_match = _re.match(r'^(#{1,6})\s+(.*)', line)
        if h_match:
            level = len(h_match.group(1))
            doc.add_heading(h_match.group(2), level=min(level, 3) + (0 if level <= 3 else 1))
            continue

        # 水平线
        if _re.match(r'^[-*_]{3,}$', line.strip()):
            doc.add_paragraph('─' * 50)
            continue

        # 列表（无序 + 有序）
        li_match = _re.match(r'^(\s*)[-*+]\s+(.*)', line)
        ol_match = _re.match(r'^(\s*)\d+[\.\)]\s+(.*)', line)
        if li_match:
            _add_rich_paragraph(li_match.group(2), style='List Bullet')
            continue
        if ol_match:
            _add_rich_paragraph(ol_match.group(2), style='List Number')
            continue

        # 引用
        bq_match = _re.match(r'^>\s?(.*)', line)
        if bq_match:
            _add_rich_paragraph(bq_match.group(1), indent=1, italic_all=True,
                               color=RGBColor(0x66, 0x66, 0x66))
            continue

        # 普通段落
        if line.strip():
            _add_rich_paragraph(line.strip())

    # Flush remaining
    if in_code_block:
        _flush_code_block()
    if in_table:
        _flush_table()

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf.read()


def _add_matplotlib_chart(doc, echarts_code: str):
    """将 ECharts option 用 matplotlib 渲染为图表图片，嵌入 docx。"""
    import io
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.font_manager import FontProperties
    from docx.shared import Inches

    opt = _parse_echarts_option(echarts_code)
    if not opt:
        return

    # 中文字体（优先用项目内置 simhei.ttf）
    import os as _os
    zh_font = zh_font_title = None
    _proj_dir = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    font_paths = [
        _os.path.join(_proj_dir, 'app', 'static', 'fonts', 'simhei.ttf'),  # 项目内置
        r'C:\Windows\Fonts\simhei.ttf',              # Windows
        '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc',  # Linux fallback
    ]
    for fp in font_paths:
        if _os.path.isfile(fp):
            try:
                zh_font = FontProperties(fname=fp, size=10)
                zh_font_title = FontProperties(fname=fp, size=12)
                break
            except Exception:
                continue

    # 图表类型和标题
    series_list = opt.get("series", [])
    if isinstance(series_list, dict):
        series_list = [series_list]
    chart_type = series_list[0].get("type", "bar") if series_list else "bar"
    title_text = ""
    if "title" in opt:
        title_text = opt["title"].get("text", "") if isinstance(opt["title"], dict) else str(opt["title"])

    # 提取数据
    categories = []
    xaxis = opt.get("xAxis", {})
    if isinstance(xaxis, dict):
        categories = xaxis.get("data", [])
    elif isinstance(xaxis, list) and xaxis:
        categories = xaxis[0].get("data", []) if isinstance(xaxis[0], dict) else []

    # 辅助：提取数值
    def _to_num(v):
        if v is None: return 0
        if isinstance(v, (int, float)): return float(v)
        if isinstance(v, dict): return float(v.get("value", 0))
        try: return float(v)
        except: return 0

    # 辅助：提取标签
    def _to_label(v, default=""):
        if isinstance(v, dict): return str(v.get("name", default))
        return str(v)

    # 创建图表
    fig, ax = plt.subplots(figsize=(7, 3.5))
    colors = ['#5470c6', '#91cc75', '#fac858', '#ee6666', '#73c0de', '#3ba272', '#fc8452', '#9a60b4']

    if chart_type == "pie":
        # 饼图
        if series_list and series_list[0].get("data"):
            sdata = series_list[0]["data"]
            if isinstance(sdata[0], dict):
                values = [_to_num(d) for d in sdata]
                labels = [_to_label(d) for d in sdata]
            else:
                values = [_to_num(v) for v in sdata]
                labels = [str(c) for c in categories] if categories else [str(i+1) for i in range(len(values))]
            wedges, texts, autotexts = ax.pie(values, labels=labels, autopct='%1.1f%%',
                colors=colors[:len(values)], textprops={'fontsize': 8, 'fontproperties': zh_font} if zh_font else {'fontsize': 8})
            if autotexts:
                for at in autotexts:
                    at.set_fontsize(8)
        if title_text:
            ax.set_title(title_text, fontproperties=zh_font_title, fontsize=13, pad=12)

    elif chart_type == "line":
        # 折线图
        for idx, s in enumerate(series_list):
            sdata = s.get("data", [])
            vals = [_to_num(v) for v in sdata]
            if categories:
                ax.plot(range(len(vals)), vals, marker='o', color=colors[idx % len(colors)],
                       label=s.get("name", ""), linewidth=2, markersize=5)
                ax.set_xticks(range(len(categories)))
                ax.set_xticklabels(categories, fontproperties=zh_font, fontsize=8, rotation=30)
            else:
                ax.plot(vals, marker='o', color=colors[idx % len(colors)],
                       label=s.get("name", ""), linewidth=2, markersize=5)
        ax.legend(prop=zh_font, fontsize=8, loc='best')
        ax.grid(True, alpha=0.3)
        if title_text:
            ax.set_title(title_text, fontproperties=zh_font_title, fontsize=13, pad=12)

    else:
        # 柱状图 (默认)
        bar_width = 0.7 / len(series_list) if len(series_list) > 1 else 0.6
        x = range(len(categories)) if categories else range(max((len(s.get("data", [])) for s in series_list), default=0))
        for idx, s in enumerate(series_list):
            sdata = s.get("data", [])
            vals = [_to_num(v) for v in sdata]
            offset = (idx - (len(series_list) - 1) / 2) * bar_width if len(series_list) > 1 else 0
            positions = [i + offset for i in range(len(vals))]
            ax.bar(positions, vals, bar_width * 0.9, color=colors[idx % len(colors)],
                   label=s.get("name", ""), edgecolor='white', linewidth=0.5)
        if categories:
            ax.set_xticks(range(len(categories)))
            ax.set_xticklabels(categories, fontproperties=zh_font, fontsize=8, rotation=30)
        ax.legend(prop=zh_font, fontsize=8, loc='best') if len(series_list) > 1 else None
        ax.grid(True, axis='y', alpha=0.3)
        if title_text:
            ax.set_title(title_text, fontproperties=zh_font_title, fontsize=13, pad=12)

    plt.tight_layout()

    # 保存为 PNG 字节
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=150, bbox_inches='tight', facecolor='white', edgecolor='none')
    plt.close(fig)
    buf.seek(0)

    # 嵌入 docx
    doc.add_paragraph()  # spacing
    doc.add_picture(buf, width=Inches(5.5))
    last_para = doc.paragraphs[-1]
    last_para.alignment = 1  # center
    doc.add_paragraph()
    buf.close()


def _add_mermaid_image(doc, mermaid_code: str):
    """通过 mermaid.ink 获取 SVG，嵌入 docx。"""
    import base64, zlib, io
    import requests as _req
    from docx.shared import Inches, RGBColor

    try:
        compressed = zlib.compress(mermaid_code.encode("utf-8"))[2:-4]
        encoded = base64.urlsafe_b64encode(compressed).decode("ascii").rstrip("=")
        url = f"https://mermaid.ink/svg/{encoded}"
        resp = _req.get(url, timeout=10)
        if resp.status_code == 200:
            img_stream = io.BytesIO(resp.content)
            doc.add_picture(img_stream, width=Inches(5.5))
            last_paragraph = doc.paragraphs[-1]
            last_paragraph.alignment = 1  # center
        else:
            p = doc.add_paragraph(f"[流程图: {mermaid_code[:100]}...]")
            if p.runs:
                p.runs[0].font.color.rgb = RGBColor(0x99, 0x99, 0x99)
    except Exception:
        p = doc.add_paragraph(f"[流程图加载失败]")
        if p.runs:
            p.runs[0].font.color.rgb = RGBColor(0x99, 0x99, 0x99)


def _html_to_docx(html_doc: str, title: str) -> bytes:
    """HTML → .docx 字节（使用 python-docx 构建基础文档）。"""
    from docx import Document
    from docx.shared import Pt, Inches, Cm, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    import re as _re

    doc = Document()

    # 页面设置
    section = doc.sections[0]
    section.page_width = Cm(21)
    section.page_height = Cm(29.7)
    section.left_margin = Cm(2.5)
    section.right_margin = Cm(2.5)

    # 标题
    title_para = doc.add_heading(title, level=0)
    title_para.alignment = WD_ALIGN_PARAGRAPH.CENTER

    # 简单 HTML → docx 段落转换
    # 按块级元素分割
    blocks = _re.split(r'</?(?:h[1-6]|p|ul|ol|li|table|tr|pre|blockquote|hr|div)[^>]*>', html_doc)
    # 更简单的办法：去掉 HTML 标签，保留文本结构
    # 按标题标记分段
    lines = html_doc.split('\n')
    in_table = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        # 标题
        for level in range(1, 7):
            htag = f'<h{level}>'
            if stripped.startswith(htag):
                text = _re.sub(r'<[^>]+>', '', stripped)
                doc.add_heading(text, level=level)
                break
        else:
            # 表格
            if '<table>' in stripped or '<tr>' in stripped:
                in_table = True
                continue
            if '</table>' in stripped:
                in_table = False
                continue
            if in_table:
                continue

            # 去掉所有 HTML 标签
            text = _re.sub(r'<[^>]+>', '', stripped)
            if text:
                para = doc.add_paragraph(text)
                style = para.paragraph_format
                style.space_after = Pt(6)

    # 保存到内存
    import io
    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf.read()


def _url_quote(s: str) -> str:
    """URL 编码文件名（UTF-8）。"""
    from urllib.parse import quote
    return quote(s, safe='')
async def get_processed_confirmations(db: AsyncSession = Depends(get_db)):
    """获取已处理的审批列表（通过/拒绝）。"""
    from app.repositories.message_repository import MessageRepository
    repo = MessageRepository(db)
    items = await repo.get_processed_confirmations(limit=100)
    result = []
    for msg in items:
        content_data = {}
        try:
            content_data = json.loads(msg.content) if msg.content else {}
        except Exception:
            content_data = {}
        result.append({
            "id": msg.id,
            "action_label": content_data.get("action_label", ""),
            "concept_label": content_data.get("concept_label", ""),
            "tool": content_data.get("tool", ""),
            "params": content_data.get("params", {}),
            "risk": content_data.get("risk", "write"),
            "user_id": content_data.get("user_id", ""),
            "param_schema": content_data.get("param_schema", []),
            "assigned_to": msg.assigned_to or "",
            "status": msg.status,
            "reviewed_by": msg.reviewed_by or "",
            "reviewed_at": msg.reviewed_at or "",
            "created_at": str(msg.created_at) if msg.created_at else "",
        })
    return {"processed": result, "total": len(result)}


@router.post("/{message_id}/approve")
async def approve_confirmation(
    message_id: str,
    body: ApprovalRequest,
    db: AsyncSession = Depends(get_db),
):
    """通过审批并执行原始动作。"""
    from app.repositories.message_repository import MessageRepository
    from app.models.message import MessageType, ConfirmStatus, MessageRole

    repo = MessageRepository(db)
    pending_msg = await repo.get_by_id(message_id)
    if not pending_msg:
        raise HTTPException(status_code=404, detail=f"消息不存在: {message_id}")
    if pending_msg.status != ConfirmStatus.PENDING.value:
        raise HTTPException(status_code=400, detail="该消息已处理")

    # 提取原始动作参数
    content_data = {}
    try:
        content_data = json.loads(pending_msg.content) if pending_msg.content else {}
    except Exception:
        pass
    tool_name = content_data.get("tool", "")
    params = content_data.get("params", {})
    original_user_id = content_data.get("user_id", "")
    conversation_id = pending_msg.conversation_id

    # 标记为已审批
    updated = await repo.resolve_confirmation(message_id, approved=True, reviewed_by=body.user_id)
    reviewer = body.user_id or "审批人"
    log.info(f"[审批] message_id={message_id} 已通过")

    # 执行原始动作 + 更新思考链（异常不影响审批结果）
    exec_result = {"success": False, "message": "未执行", "rowCount": 0}
    action_label = content_data.get("action_label", tool_name)
    try:
        if tool_name:
            from app.services.action_executor import action_executor
            exec_result = await action_executor.execute_structured_async(
                tool_name, {**params, '_skip_approval': True}, user_id=original_user_id or body.user_id,
            )
            log.info(f"[审批] 动作 {tool_name} 执行完成: rowCount={exec_result.get('rowCount', 0)}")
    except Exception as e:
        log.error(f"[审批] 动作执行失败: {e}")
        exec_result = {"success": False, "message": str(e), "rowCount": 0}

    try:
        # 用 param_schema 翻译字段名为中文标签
        schema_map = {}
        try:
            cdata = json.loads(pending_msg.content) if isinstance(pending_msg.content, str) else (pending_msg.content or {})
            for ps in cdata.get("param_schema", []):
                schema_map[ps.get("name", "")] = ps.get("label", ps.get("name", ""))
        except Exception:
            pass
        labeled_params = {schema_map.get(k, k): v for k, v in params.items() if v}

        params_summary = ", ".join(f"{k}={v}" for k, v in labeled_params.items())
        await _append_exec_step(db, pending_msg, f"审批通过 ({reviewer})，已执行",
            f"审批人: {reviewer} | 操作: {action_label}" +
            (f" | {params_summary}" if params_summary else "") +
            (f" | 影响 {exec_result.get('rowCount', 0)} 行" if exec_result.get('rowCount', 0) > 0 else " | 完成")
        )
    except Exception as e:
        log.error(f"[审批] 更新思考链失败: {e}")

    # 执行结果写入对话
    try:
        result_parts = [f"✅ 审批通过，已执行: **{action_label}**"]
        if exec_result.get("rowCount", 0) > 0:
            result_parts.append(f"影响行数: {exec_result['rowCount']}")
            if exec_result.get("result"):
                result_parts.append(exec_result["result"])
        await repo.create(
            conversation_id=conversation_id, role=MessageRole.ASSISTANT,
            content="\n".join(result_parts), message_type=MessageType.INFO.value,
        )
    except Exception as e:
        log.error(f"[审批] 写入对话失败: {e}")

    # 处理推理链确认
    inferences = exec_result.get("inferences", []) or []
    if exec_result.get("needs_inference_confirmation") and inferences:
        try:
            for inf in inferences:
                await repo.create(
                    conversation_id=conversation_id, role=MessageRole.SYSTEM,
                    content=json.dumps({
                        "tool": inf.get("target_action", tool_name),
                        "action_label": inf.get("rule_label", ""),
                        "concept_label": inf.get("target_concept", ""),
                        "params": inf.get("target_params", {}),
                        "risk": "inference", "user_id": body.user_id,
                        "message": f"推理链: {inf.get('description', '')}",
                    }, ensure_ascii=False),
                    message_type=MessageType.CONFIRM.value,
                    status=ConfirmStatus.PENDING.value,
                    assigned_to=content_data.get("assigned_to", ""),
                )
        except Exception as e:
            log.error(f"[审批] 写入推理链确认失败: {e}")

    # 广播审批完成事件
    from app.services.event_bus import event_bus
    await event_bus.publish("approval_done", {
        "conversation_id": conversation_id,
        "message_id": message_id,
        "action": action_label,
        "reviewer": reviewer,
        "submitter": original_user_id or "",
        "approved": True,
    })

    return {
        "success": True,
        "message_id": message_id,
        "status": updated.status,
        "exec_result": {"rowCount": exec_result.get("rowCount", 0), "message": exec_result.get("message", "")},
    }


async def _append_exec_step(db, pending_msg, label: str, detail: str = None, status: str = "done"):
    """往待审批消息关联的 AI 消息的思考链中追加执行步骤。"""
    try:
        from app.models.message import Message, MessageRole
        from sqlalchemy import select
        import json as _json
        q = select(Message).where(
            Message.conversation_id == pending_msg.conversation_id,
            Message.role == MessageRole.ASSISTANT,
        ).order_by(Message.created_at.desc()).limit(1)
        r = await db.execute(q)
        ai_msg = r.scalar_one_or_none()
        if ai_msg and ai_msg.extra_data:
            meta = _json.loads(ai_msg.extra_data) if isinstance(ai_msg.extra_data, str) else ai_msg.extra_data
            steps = meta.get("execution_steps", [])
            steps.append({
                "key": "approval_executed",
                "label": label,
                "status": status,
                "detail": detail if isinstance(detail, str) else _json.dumps(detail, ensure_ascii=False) if detail else "",
            })
            meta["execution_steps"] = steps
            ai_msg.extra_data = _json.dumps(meta, ensure_ascii=False)
            await db.commit()
            log.info(f"[审批] 思考链已更新: {label}")
    except Exception as e:
        log.warning(f"[审批] 更新思考链失败: {e}")


@router.post("/{message_id}/reject")
async def reject_confirmation(
    message_id: str,
    body: ApprovalRequest,
    db: AsyncSession = Depends(get_db),
):
    """拒绝审批。"""
    from app.repositories.message_repository import MessageRepository
    from app.models.message import MessageType, MessageRole

    repo = MessageRepository(db)
    pending_msg = await repo.get_by_id(message_id)
    if not pending_msg:
        raise HTTPException(status_code=404, detail=f"消息不存在: {message_id}")

    # 提取动作信息
    content_data = {}
    try:
        content_data = json.loads(pending_msg.content) if pending_msg.content else {}
    except Exception:
        pass
    action_label = content_data.get("action_label", "")
    reason = body.comment or ""

    updated = await repo.resolve_confirmation(message_id, approved=False, reviewed_by=body.user_id)
    if not updated:
        raise HTTPException(status_code=404, detail=f"消息不存在: {message_id}")

    reviewer = body.user_id or "审批人"
    detail = f"审批人: {reviewer} | 操作: {action_label}"
    if reason:
        detail += f" | 原因: {reason}"
    try:
        await _append_exec_step(db, pending_msg, f"审批拒绝 ({reviewer})", detail, status="error")
    except Exception as e:
        log.error(f"[审批] 更新思考链失败: {e}")

    try:
        from app.services.event_bus import event_bus
        await event_bus.publish("approval_done", {
            "conversation_id": pending_msg.conversation_id,
            "message_id": message_id,
            "action": action_label,
            "reviewer": reviewer,
            "approved": False,
        })
    except Exception:
        pass

    return {"success": True, "message_id": message_id, "status": updated.status}
