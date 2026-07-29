"""默认通知规则 seed — 首次启动时写入预置规则"""
import json

from app.core.logger import log

DEFAULT_RULES = [
    {
        "event_type": "plan.generated",
        "condition": "$.missing_actions_count > 0",
        "target": "owner",
        "channels": json.dumps(["inapp"]),
        "title_template": "方案「{plan_label}」无法执行",
        "body_template": "缺少 {missing_actions_count} 个操作需在本体图谱创建：{missing_actions_list}。创建并推送 Schema 后返回 FC 配置执行链即可执行。",
        "priority": 10,
    },
    {
        "event_type": "plan.generated",
        "condition": "$.missing_actions_count > 0",
        "target": "role:工程经理",
        "channels": json.dumps(["inapp"]),
        "title_template": "有待创建的操作请求",
        "body_template": "用户 {conversation_owner} 的分析方案需要 {missing_actions_count} 个新操作：{missing_actions_list}",
        "priority": 5,
    },
    {
        "event_type": "plan.executed",
        "condition": '$.status != "ok"',
        "target": "owner",
        "channels": json.dumps(["inapp"]),
        "title_template": "执行失败：{chain_name}",
        "body_template": "执行链「{chain_name}」执行失败（{steps_completed}/{total_steps} 步成功）。错误：{error_summary}",
        "priority": 10,
    },
    {
        "event_type": "schema.pushed",
        "condition": "$.actions_added_count > 0",
        "target": "role:工程经理",
        "channels": json.dumps(["inapp"]),
        "title_template": "Schema 已更新：{actions_added_count} 个新操作",
        "body_template": "概念 {concepts_updated} 新增操作：{actions_added}。命名空间：{namespace}",
        "priority": 5,
    },
    {
        "event_type": "system.alert",
        "condition": '$.tier == "critical"',
        "target": "role:系统管理员",
        "channels": json.dumps(["inapp"]),
        "title_template": "系统资源严重不足",
        "body_template": "当前等级：{tier}。原因：{reason}。并发请求：{concurrent_requests}，API调用/分：{api_calls_per_minute}，Token/时：{token_usage_this_hour}",
        "priority": 20,
    },
    {
        "event_type": "approval.required",
        "condition": "",
        "target": "role:系统管理员",
        "channels": json.dumps(["inapp"]),
        "title_template": "新的待审批异常",
        "body_template": "用户 {user_id} 请求了不支持的操作「{message}」，候选操作: {candidates}。请在待审批工作台处理。",
        "priority": 15,
    },
]


async def seed_default_rules():
    """写入默认通知规则（如果不存在）"""
    try:
        from app.models.notification import NotificationRule
        from app.db import get_db
        from sqlalchemy import select, func

        async for session in get_db():
            # 检查是否已有规则
            count_stmt = select(func.count()).select_from(NotificationRule)
            result = await session.execute(count_stmt)
            existing = result.scalar()

            if existing and existing > 0:
                log.debug(f"[NotificationSeed] 已有 {existing} 条规则，跳过 seed")
                return

            # 写入预置规则
            for rule_data in DEFAULT_RULES:
                rule = NotificationRule(**rule_data)
                session.add(rule)

            await session.commit()
            log.info(f"[NotificationSeed] 写入 {len(DEFAULT_RULES)} 条默认通知规则")
            return

    except Exception as e:
        log.warning(f"[NotificationSeed] seed 失败（非致命）: {e}")
