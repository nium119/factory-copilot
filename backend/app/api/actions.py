"""本体 Action 执行网关 — 把 FC 的 action_executor 通过 REST 暴露给外部调用方（OntoStudio MCP）。

定位（当前阶段）：
- 「能力优先，治理后补」：execute 直通执行，跳过人机确认与审批门禁（_skip_approval）；
- RBAC 角色权限仍生效（authorized_roles 有配置时按 user_id 校验）；
- 全局 AuthMiddleware 强制 Bearer JWT——调用方（OntoStudio）需配置服务 token；
- 生产启用前必须补：执行审计落盘、审批门禁接入、调用方白名单。
"""
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.logger import log

router = APIRouter(prefix="/actions", tags=["Action 执行网关"])


class ExecuteRequest(BaseModel):
    tool: str = Field(..., description="工具全名，如 WorkOrder_create / Material_query")
    params: Dict[str, Any] = Field(default_factory=dict, description="动作参数（按本体 action 签名）")
    user_id: str = Field(default="mcp_service", description="执行者标识（RBAC 角色检查与审计用）")
    namespace: Optional[str] = Field(default=None, description="预期 namespace；与 FC 激活本体不符时拒绝（防跨主体误操作）")


@router.get("", summary="列出可执行工具")
async def list_actions():
    """列出当前激活本体的全部可执行 action 签名（含参数 schema 与确认标记）。"""
    from app.services.action_executor import action_executor

    action_executor._ensure_loaded()
    tools = []
    for name, sig in action_executor._sigs.items():
        if sig.get("source") == "mcp":
            continue  # 排除 MCP 回环工具（经本端点执行会递归回调 MCP）
        tools.append({
            "name": name,
            "concept": sig.get("conceptName", ""),
            "action": sig.get("actionName", ""),
            "description": sig.get("description", ""),
            "params": [
                {"name": p.get("name", ""), "label": p.get("label", ""),
                 "type": p.get("type", "string"), "required": bool(p.get("required"))}
                for p in (sig.get("params") or [])
            ],
            "requiresConfirmation": bool(sig.get("requiresConfirmation")),
        })
    return {"count": len(tools), "tools": tools}


@router.post("/execute", summary="执行 action（直通，无确认/审批）")
async def execute_action(req: ExecuteRequest):
    """直通执行一个本体 action。

    - 跳过人机确认与审批门禁（演示阶段拍板，生产前必须补回）；
    - requiresConfirmation 的 action 在响应中带 skipped_confirmation=True 透明标记；
    - RBAC：action 配置了 authorized_roles 时按 user_id 校验，权限不足返回错误结果。
    """
    from app.services.action_executor import action_executor
    from app.services.ontology_service import ontology_service

    action_executor._ensure_loaded()
    sig = action_executor._sigs.get(req.tool)
    if not sig:
        raise HTTPException(404, f"工具不存在: {req.tool}（可用 GET /api/actions 查询）")
    if sig.get("source") == "mcp":
        raise HTTPException(400, f"工具 {req.tool} 是 MCP 回环工具，不允许经此端点执行")

    # namespace 防呆：调用方声明的主体与 FC 激活本体不一致时拒绝
    if req.namespace:
        active_ns = (ontology_service.meta or {}).get("namespace") or \
                    (ontology_service.meta or {}).get("projectName") or ""
        if active_ns and req.namespace != active_ns:
            raise HTTPException(
                409, f"namespace 不匹配：请求 {req.namespace}，FC 当前激活 {active_ns}；"
                     f"如需操作其他本体请先在 FC 切换并应用")

    log.warning(f"[Action网关] 直通执行 {req.tool} params={req.params} user={req.user_id} "
                f"(skip_confirmation={bool(sig.get('requiresConfirmation'))})")

    try:
        result = await action_executor.execute_structured_async(
            req.tool, {**req.params, "_skip_approval": True}, user_id=req.user_id,
        )
    except Exception as e:
        log.error(f"[Action网关] 执行失败 {req.tool}: {e}")
        return {"ok": False, "tool": req.tool, "error": str(e)}

    result = result or {}
    result_text = str(result.get("result", ""))
    failed = (
        result.get("source") == "validation"
        or "权限不足" in result_text
        or "必填" in result_text
        or "失败" in result_text
    )
    return {
        "ok": not failed,
        "tool": req.tool,
        "rowCount": result.get("rowCount", 0),
        "result": result.get("result", ""),
        "source": result.get("source", ""),
        "skipped_confirmation": bool(sig.get("requiresConfirmation")),
    }
