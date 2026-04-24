"""Human-in-the-Loop 审批流管理"""
from typing import Dict, Any, Optional, List
from datetime import datetime
import uuid
from app.core.logger import log
from app.agents.settings import REQUIRES_APPROVAL


class ApprovalManager:
    """审批流管理器"""

    _pending: Dict[str, Dict[str, Any]] = {}

    @classmethod
    def create_approval_request(
        cls,
        action: str,
        description: str,
        details: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """创建审批请求"""
        if action not in REQUIRES_APPROVAL:
            return None

        approval_id = str(uuid.uuid4())[:8]
        request = {
            "approval_id": approval_id,
            "action": action,
            "action_name": REQUIRES_APPROVAL[action]["name"],
            "risk_level": REQUIRES_APPROVAL[action]["risk"],
            "description": description,
            "details": details,
            "status": "pending",
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "approved_by": None,
            "approved_at": None,
            "reject_reason": None,
        }
        cls._pending[approval_id] = request
        log.info(f"[审批流] 创建审批请求: {approval_id} - {request['action_name']}")
        return request

    @classmethod
    def approve(cls, approval_id: str, approved_by: str = "user") -> Optional[Dict[str, Any]]:
        """审批通过"""
        req = cls._pending.get(approval_id)
        if not req or req["status"] != "pending":
            return None
        req["status"] = "approved"
        req["approved_by"] = approved_by
        req["approved_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log.info(f"[审批流] 审批通过: {approval_id}")
        return req

    @classmethod
    def reject(cls, approval_id: str, reason: str = "") -> Optional[Dict[str, Any]]:
        """审批拒绝"""
        req = cls._pending.get(approval_id)
        if not req or req["status"] != "pending":
            return None
        req["status"] = "rejected"
        req["reject_reason"] = reason
        log.info(f"[审批流] 审批拒绝: {approval_id} - {reason}")
        return req

    @classmethod
    def get_status(cls, approval_id: str) -> Optional[Dict[str, Any]]:
        """获取审批状态"""
        return cls._pending.get(approval_id)

    @classmethod
    def list_pending(cls) -> List[Dict[str, Any]]:
        """列出所有待审批的请求"""
        return [r for r in cls._pending.values() if r["status"] == "pending"]
