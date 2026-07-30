"""通知 API — CRUD + SSE 实时推送"""
import json

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, select, func, update

from app.core.config import settings
from app.core.logger import log
from app.db import get_db
from app.models.notification import Notification, NotificationRule
from app.services.event_bus import event_bus

router = APIRouter(prefix="/notifications", tags=["通知"])


@router.get("/count")
async def get_unread_count(request: Request):
    """获取当前用户未读通知数"""
    user_id = _get_user_id(request)
    async for session in get_db():
        stmt = select(func.count()).select_from(Notification).where(
            Notification.recipient == user_id,
            Notification.status == "unread",
        )
        result = await session.execute(stmt)
        count = result.scalar() or 0
        return {"count": count, "user_id": user_id}
    return {"count": 0, "user_id": user_id}


@router.get("")
async def list_notifications(
    request: Request,
    status: str = Query("unread", description="unread | read | archived | all"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    title: str = Query("", description="标题搜索"),
    type: str = Query("", description="类型过滤"),
):
    """获取通知列表"""
    user_id = _get_user_id(request)
    async for session in get_db():
        stmt = select(Notification).where(Notification.recipient == user_id)
        if status != "all":
            stmt = stmt.where(Notification.status == status)
        if title:
            stmt = stmt.where(Notification.title.contains(title))
        if type:
            stmt = stmt.where(Notification.type == type)

        # 计数
        count_stmt = select(func.count()).select_from(Notification).where(
            Notification.recipient == user_id,
        )
        if status != "all":
            count_stmt = count_stmt.where(Notification.status == status)
        count_result = await session.execute(count_stmt)
        total = count_result.scalar() or 0

        # 分页
        stmt = stmt.order_by(Notification.created_at.desc()).offset(offset).limit(limit)
        result = await session.execute(stmt)
        notifications = result.scalars().all()

        return {
            "total": total,
            "items": [
                {
                    "id": n.id,
                    "type": n.type,
                    "severity": n.severity,
                    "title": n.title,
                    "body": n.body,
                    "status": n.status,
                    "source": n.source,
                    "ref_conversation_id": n.ref_conversation_id,
                    "ref_chain_id": n.ref_chain_id,
                    "created_at": n.created_at.isoformat() if n.created_at else "",
                    "read_at": n.read_at.isoformat() if n.read_at else None,
                }
                for n in notifications
            ],
        }
    return {"total": 0, "items": []}


@router.put("/{notification_id}/read")
async def mark_read(notification_id: str, request: Request):
    """标记单条通知已读"""
    user_id = _get_user_id(request)
    async for session in get_db():
        from datetime import datetime
        stmt = (
            update(Notification)
            .where(
                Notification.id == notification_id,
                Notification.recipient == user_id,
            )
            .values(status="read", read_at=datetime.now())
        )
        await session.execute(stmt)
        await session.commit()
        return {"ok": True}


@router.put("/read-all")
async def mark_all_read(request: Request):
    """全部标记已读"""
    user_id = _get_user_id(request)
    async for session in get_db():
        from datetime import datetime
        stmt = (
            update(Notification)
            .where(
                Notification.recipient == user_id,
                Notification.status == "unread",
            )
            .values(status="read", read_at=datetime.now())
        )
        await session.execute(stmt)
        await session.commit()
        return {"ok": True}


@router.post("/schema-pushed")
async def on_schema_pushed(request: Request):
    """接收 OntoStudio Schema 推送完成事件。
    FC 侧将其入队，由 EventDispatcher 匹配规则后通知相关人员。
    """
    try:
        body = await request.json()
    except Exception:
        body = {}

    namespace = body.get("namespace", "")
    actions_added = body.get("actions_added", [])
    concepts_updated = body.get("concepts_updated", [])

    log.info(
        f"[SchemaPushed] namespace={namespace}, "
        f"actions_added={len(actions_added)}, concepts_updated={len(concepts_updated)}"
    )

    try:
        from app.models.event import EventQueue
        from app.db import get_db
        import json

        async for session in get_db():
            eq = EventQueue(
                type="schema.pushed",
                payload=json.dumps({
                    "namespace": namespace,
                    "actions_added": actions_added,
                    "actions_added_count": len(actions_added),
                    "concepts_updated": ", ".join(concepts_updated[:10]),
                    "action_data": {
                        "missing_actions": actions_added,
                    },
                }),
            )
            session.add(eq)
            await session.commit()
            break

        return {"ok": True, "queued": True}
    except Exception as e:
        log.warning(f"[SchemaPushed] 入队失败: {e}")
        return {"ok": False, "error": str(e)}


@router.delete("/{notification_id}")
async def archive_notification(notification_id: str, request: Request):
    """归档通知"""
    user_id = _get_user_id(request)
    async for session in get_db():
        stmt = (
            update(Notification)
            .where(
                Notification.id == notification_id,
                Notification.recipient == user_id,
            )
            .values(status="archived")
        )
        await session.execute(stmt)
        await session.commit()
        return {"ok": True}


@router.get("/events/stream")
async def notification_stream(request: Request):
    """SSE 实时推送通知"""
    user_id = _get_user_id(request)

    async def event_generator():
        async for sse_data in event_bus.subscribe():
            # 解析事件，只推送给目标用户
            if f'"recipient":"{user_id}"' in sse_data or '"recipient":"*"' in sse_data:
                yield sse_data
            # 透传 notification 事件（前端自己过滤）
            if '"notification"' in sse_data or "event: notification" in sse_data:
                yield sse_data

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── 操作请求（供本体图谱拉取） ──

@router.get("/pending-actions")
async def get_pending_actions():
    """返回所有待处理的操作请求 — 本体图谱轮询此接口展示待创建 action。"""
    try:
        from app.models.event import EventQueue
        async for session in get_db():
            stmt = (
                select(EventQueue)
                .where(
                    EventQueue.type == "plan.generated",
                    EventQueue.status.in_(["processed"]),
                )
                .order_by(EventQueue.created_at.desc())
                .limit(20)
            )
            result = await session.execute(stmt)
            events = result.scalars().all()

            items = []
            seen = set()
            for ev in events:
                try:
                    payload = json.loads(ev.payload) if isinstance(ev.payload, str) else ev.payload
                    missing = payload.get("missing_actions_list", "")
                    if not missing:
                        continue
                    key = f"{payload.get('conversation_owner','')}:{missing}"
                    if key in seen:
                        continue
                    seen.add(key)
                    items.append({
                        "id": ev.id,
                        "plan_label": payload.get("plan_label", ""),
                        "owner": payload.get("conversation_owner", ""),
                        "missing_actions": payload.get("missing_actions_list", ""),
                        "existing_actions": payload.get("existing_actions_list", ""),
                        "action_labels": payload.get("action_labels", ""),
                        "missing_count": payload.get("missing_actions_count", 0),
                        "unchained_count": payload.get("unchained_count", 0),
                        "steps": payload.get("steps", []),
                        "actions": payload.get("actions", []),
                        "namespace": payload.get("namespace", ""),
                        "created_at": ev.created_at.isoformat() if ev.created_at else "",
                    })
                except Exception:
                    pass

            return {"items": items}
    except Exception as e:
        log.warning(f"[PendingActions] 查询失败: {e}")
        return {"items": []}


# ── 操作请求 ──

@router.post("/action-request")
async def submit_action_request(request: Request):
    """提交操作请求 — 方案无可用执行链，通知建模人员需要创建 action 或配置链。"""
    body = await request.json()
    plan_label = body.get("plan_label", "")
    steps = body.get("steps", [])
    actions = body.get("actions", [])
    conversation_id = body.get("conversation_id", "")

    user_id = _get_user_id(request) or "unknown"
    existing = body.get("existing_actions", [])
    missing = body.get("missing_actions", [])
    all_actions = body.get("actions", [])
    steps_list = body.get("steps", [])
    namespace = settings.NEO4J_NAMESPACE or ""

    try:
        from app.models.event import EventQueue
        import json as _json

        async for session in get_db():
            eq = EventQueue(
                type="plan.generated",
                payload=_json.dumps({
                    "conversation_id": conversation_id,
                    "conversation_owner": user_id,
                    "plan_label": plan_label,
                    "steps": steps_list,
                    "actions": all_actions,
                    "existing_actions": existing,
                    "missing_actions_list": ", ".join(missing) if missing else "",
                    "existing_actions_list": ", ".join(existing) if existing else "",
                    "missing_actions_count": len(missing),
                    "unchained_count": len(existing),
                    "namespace": namespace,
                }),
            )
            session.add(eq)
            await session.commit()
            break

        log.info(f"[ActionRequest] 用户 {user_id} 提交方案「{plan_label}」: 缺失{missing}, 已有{existing}")
        return {"ok": True, "queued": True}
    except Exception as e:
        log.warning(f"[ActionRequest] 入队失败: {e}")
        return {"ok": False, "error": str(e)}


# ── 企微测试 ──

@router.post("/wecom-test")
async def test_wecom(request: Request):
    """测试企微 Webhook 发送"""
    body = await request.json()
    webhook_url = body.get("webhook_url", "")
    if not webhook_url:
        return {"ok": False, "error": "缺少 webhook_url"}

    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(webhook_url, json={
                "msgtype": "text",
                "text": {"content": "【本体图谱 通知系统】\n测试消息 — 如果您收到此消息，说明企业微信通知配置成功。"},
            })
            if resp.status_code == 200:
                return {"ok": True, "status": resp.status_code}
            return {"ok": False, "status": resp.status_code, "body": resp.text[:200]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.post("/email-test")
async def test_email(request: Request):
    """测试邮件发送"""
    body = await request.json()
    to_email = body.get("email", "").strip()
    if not to_email:
        return {"ok": False, "error": "缺少收件邮箱"}

    try:
        import smtplib
        from email.mime.text import MIMEText
        from email.header import Header
        import asyncio

        msg = MIMEText("这是本体图谱通知系统的测试邮件。\n如果您收到此邮件，说明邮件通知配置成功。", "plain", "utf-8")
        msg["Subject"] = Header("【本体图谱】邮件通知测试", "utf-8")
        msg["From"] = settings.SMTP_FROM or "ontostudio@local"
        msg["To"] = to_email

        def _send():
            host = settings.SMTP_HOST
            port = settings.SMTP_PORT or 587
            if port == 465:
                server = smtplib.SMTP_SSL(host, port, timeout=10)
            else:
                server = smtplib.SMTP(host, port, timeout=10)
                if settings.SMTP_USE_TLS:
                    server.starttls()
            if settings.SMTP_USER and settings.SMTP_PASSWORD:
                server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.sendmail(msg["From"], [msg["To"]], msg.as_string())
            server.quit()

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _send)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── 通知规则管理 ──

@router.get("/rules")
async def list_rules():
    """获取所有通知规则"""
    async for session in get_db():
        stmt = select(NotificationRule).order_by(NotificationRule.priority.desc())
        result = await session.execute(stmt)
        rules = result.scalars().all()
        return {
            "items": [
                {
                    "id": r.id, "event_type": r.event_type,
                    "condition": r.condition or "", "target": r.target,
                    "channels": r.channels, "title_template": r.title_template,
                    "body_template": r.body_template, "enabled": r.enabled,
                    "priority": r.priority,
                }
                for r in rules
            ],
        }
    return {"items": []}


@router.post("/rules")
async def create_rule(request: Request):
    """创建通知规则"""
    body = await request.json()
    async for session in get_db():
        rule = NotificationRule(
            event_type=body.get("event_type", ""),
            condition=body.get("condition", ""),
            target=body.get("target", "owner"),
            channels=body.get("channels", '["inapp"]'),
            title_template=body.get("title_template", ""),
            body_template=body.get("body_template", ""),
            enabled=body.get("enabled", True),
            priority=body.get("priority", 0),
        )
        session.add(rule)
        await session.commit()
        return {"ok": True, "id": rule.id}
    return {"ok": False}


@router.put("/rules/{rule_id}")
async def update_rule(rule_id: int, request: Request):
    """更新通知规则"""
    body = await request.json()
    async for session in get_db():
        stmt = select(NotificationRule).where(NotificationRule.id == rule_id)
        result = await session.execute(stmt)
        rule = result.scalar_one_or_none()
        if not rule:
            return {"ok": False, "error": "规则不存在"}
        for field in ["event_type", "condition", "target", "channels",
                       "title_template", "body_template", "priority"]:
            if field in body:
                setattr(rule, field, body[field])
        if "enabled" in body:
            rule.enabled = body["enabled"]
        await session.commit()
        return {"ok": True}


@router.delete("/rules/{rule_id}")
async def delete_rule(rule_id: int):
    """删除通知规则"""
    async for session in get_db():
        stmt = delete(NotificationRule).where(NotificationRule.id == rule_id)
        await session.execute(stmt)
        await session.commit()
        return {"ok": True}
    return {"ok": False}


# ── 事件类型定义（供前端下拉使用） ──

@router.get("/employees/search")
async def search_employees(q: str = ""):
    """搜索员工 — 供通知目标「指定用户」下拉使用"""
    if not q or len(q) < 1:
        q = ""  # 空搜索返回前20条
    try:
        from app.services.neo4j_service import neo4j_service
        if not neo4j_service.connected:
            return {"items": []}
        ns = settings.NEO4J_NAMESPACE or ""
        records = await neo4j_service.execute_read(
            "MATCH (e:Employee) WHERE (e._namespace = $ns OR $ns = '') AND "
            "(e.code CONTAINS $q OR e.name CONTAINS $q) "
            "RETURN e.code AS code, e.name AS name ORDER BY e.code LIMIT 20",
            {"ns": ns, "q": q},
        )
        return {"items": [{"code": r["code"], "name": r.get("name", ""), "label": f"{r['code']} {r.get('name', '')}".strip()} for r in records]}
    except Exception:
        return {"items": []}


@router.get("/event-types")
async def get_event_types():
    """返回可用的事件类型列表 + 动态角色目标"""
    # 从 Neo4j 查 Role 个体数据，兜底查 Concept.conceptType
    role_targets = []
    try:
        from app.services.neo4j_service import neo4j_service
        if neo4j_service.connected:
            ns = settings.NEO4J_NAMESPACE or ""
            # 优先查 Role 个体节点（推送了数据）
            records = await neo4j_service.execute_read(
                "MATCH (r:Role) WHERE (r._namespace = $ns OR $ns = '') "
                "RETURN DISTINCT r.name AS name ORDER BY r.name",
                {"ns": ns},
            )
            # 没个体数据则查 Concept.conceptType
            if not records:
                records = await neo4j_service.execute_read(
                    "MATCH (c:Concept) WHERE c.conceptType IN ['role', 'dictionary'] "
                    "AND (c.namespace = $ns OR $ns = '') "
                    "RETURN DISTINCT c.label AS name ORDER BY c.name",
                    {"ns": ns},
                )
            for rec in records:
                name = rec.get("name", "")
                if name:
                    role_targets.append({
                        "key": f"role:{name}",
                        "label": name,
                        "desc": f"所有{name}",
                    })
    except Exception:
        pass

    return {
        "items": [
            {"key": "plan.generated", "label": "方案分析完成", "desc": "智能分析生成变更方案时触发"},
            {"key": "plan.executed", "label": "执行链完成", "desc": "变更方案执行成功或失败时触发"},
            {"key": "approval.required", "label": "需要审批", "desc": "操作需要审批确认时触发"},
            {"key": "schema.pushed", "label": "操作创建完成", "desc": "建模人员在本体图谱创建操作并推送 Schema 时触发"},
            {"key": "system.alert", "label": "系统告警", "desc": "资源降级、配额超限等系统告警"},
        ],
        "targets": [
            {"key": "owner", "label": "对话发起人", "desc": "触发事件的用户"},
            *role_targets,
            {"key": "user:", "label": "指定用户", "desc": "按工号指定"},
        ],
        "conditions": {
            "plan.generated": [
                {"key": "", "label": "总是通知"},
                {"key": "$.missing_actions_count > 0", "label": "仅当方案缺少操作时通知"},
            ],
            "plan.executed": [
                {"key": "", "label": "总是通知"},
                {"key": "$.status != \"ok\"", "label": "仅执行失败时通知"},
            ],
            "approval.required": [
                {"key": "", "label": "总是通知"},
            ],
            "schema.pushed": [
                {"key": "", "label": "总是通知"},
                {"key": "$.actions_added_count > 0", "label": "仅当有新操作时通知"},
            ],
            "system.alert": [
                {"key": "", "label": "总是通知"},
                {"key": "$.tier == \"critical\"", "label": "仅系统严重降级时通知"},
            ],
        },
        "channels": [
            {"key": "inapp", "label": "🔔 应用内", "icon": "🔔"},
            {"key": "wecom", "label": "💬 企业微信", "icon": "💬"},
            {"key": "dingtalk", "label": "📌 钉钉", "icon": "📌"},
            {"key": "email", "label": "📧 邮件", "icon": "📧"},
            {"key": "sms", "label": "📱 短信", "icon": "📱"},
            {"key": "webhook", "label": "🔗 Webhook", "icon": "🔗"},
        ],
    }


@router.post("/dingtalk-test")
async def test_dingtalk():
    """测试钉钉 Webhook 发送"""
    if not settings.DINGTALK_WEBHOOK_URL:
        return {"ok": False, "error": "未配置 DINGTALK_WEBHOOK_URL"}
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(settings.DINGTALK_WEBHOOK_URL, json={
                "msgtype": "text",
                "text": {"content": "【本体图谱 通知系统】\n测试消息 — 钉钉通知配置成功。"},
            })
            return {"ok": resp.status_code == 200, "status": resp.status_code}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.post("/webhook-test")
async def test_webhook():
    """测试通用 Webhook 发送"""
    if not settings.WEBHOOK_URL:
        return {"ok": False, "error": "未配置 WEBHOOK_URL"}
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(settings.WEBHOOK_URL, json={
                "title": "测试消息",
                "body": "本体图谱通知系统 Webhook 测试成功。",
                "type": "test",
            })
            return {"ok": resp.status_code in (200, 201, 204), "status": resp.status_code}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _get_user_id(request: Request) -> str:
    """从请求中提取当前用户工号"""
    user_id = request.headers.get("X-User-Id", "").strip()
    if not user_id:
        # fallback: 尝试从 Authorization header 解析
        from app.services.auth_service import auth_service
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            user_id = auth_service.resolve_user(token) or ""
    return user_id
