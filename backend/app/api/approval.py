"""审批流 API — Human-in-the-Loop"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from app.agents.approval import ApprovalManager

router = APIRouter(prefix="/approval", tags=["审批流"])


class ApprovalResponse(BaseModel):
    """审批操作请求"""
    approval_id: str
    approved_by: Optional[str] = "user"
    reject_reason: Optional[str] = None


@router.get("/pending", summary="获取待审批列表")
async def get_pending_approvals():
    """返回所有待审批的请求"""
    return {"items": ApprovalManager.list_pending()}


@router.post("/approve", summary="审批通过")
async def approve_request(req: ApprovalResponse):
    """审批通过指定请求"""
    result = ApprovalManager.approve(req.approval_id, req.approved_by)
    if not result:
        raise HTTPException(status_code=404, detail="审批请求不存在或已处理")
    return {"success": True, "approval": result}


@router.post("/reject", summary="审批拒绝")
async def reject_request(req: ApprovalResponse):
    """审批拒绝指定请求"""
    result = ApprovalManager.reject(req.approval_id, req.reject_reason or "用户拒绝")
    if not result:
        raise HTTPException(status_code=404, detail="审批请求不存在或已处理")
    return {"success": True, "approval": result}


@router.get("/status/{approval_id}", summary="查询审批状态")
async def get_approval_status(approval_id: str):
    """查询指定审批请求的状态"""
    result = ApprovalManager.get_status(approval_id)
    if not result:
        raise HTTPException(status_code=404, detail="审批请求不存在")
    return {"approval": result}


@router.post("/execute/{approval_id}", summary="执行已审批的操作")
async def execute_approved_action(approval_id: str):
    """审批通过后，实际执行操作"""
    result = ApprovalManager.get_status(approval_id)
    if not result:
        raise HTTPException(status_code=404, detail="审批请求不存在")
    if result["status"] != "approved":
        raise HTTPException(status_code=400, detail="该请求未获审批通过")
    if result.get("executed"):
        return {"success": True, "message": "已执行", "approval": result}

    # 根据操作类型执行实际动作
    action = result["action"]
    details = result["details"]

    if action == "andon_escalate":
        from app.agents.tools.andon_tools import escalate_andon as _do_escalate
        exec_result = await _do_escalate(details["andon_id"], details["level"], skip_approval=True)
    elif action == "andon_stop_line":
        from app.agents.tools.andon_tools import handle_line_stop as _do_stop
        exec_result = await _do_stop(details["line"], details["reason"], skip_approval=True)
    elif action == "wo_start":
        from app.agents.tools.workstation_tools import start_work_order as _do_start
        exec_result = await _do_start(details["ws_id"], details["wo_id"], details["operator"], skip_approval=True)
    elif action == "wo_complete":
        from app.agents.tools.workstation_tools import complete_work_order as _do_complete
        exec_result = await _do_complete(details["ws_id"], details["good_qty"], details["bad_qty"], details["operator"], skip_approval=True)
    elif action == "andon_create":
        from app.agents.tools.andon_tools import create_andon_alert as _do_create_andon
        exec_result = await _do_create_andon(details.get("arg0", ""), details.get("arg1", ""), details.get("arg2"))
    elif action == "ws_fa_confirm":
        from app.agents.tools.workstation_tools import first_article_confirm as _do_fa
        exec_result = await _do_fa(details.get("arg0", ""), details.get("arg1", ""), details.get("arg2", ""))
    elif action == "ws_self_inspect":
        from app.agents.tools.workstation_tools import self_inspection as _do_inspect
        exec_result = await _do_inspect(details.get("arg0", ""), details.get("arg1", ""), details.get("arg2", ""), details.get("arg3", ""))
    else:
        exec_result = {"action": action, "status": "executed"}

    result["executed"] = True
    result["exec_result"] = exec_result
    return {"success": True, "executed": True, "result": exec_result}
