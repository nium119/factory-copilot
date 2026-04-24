/**
 * 审批流 API 服务
 *
 * 用于 HITL（Human-in-the-Loop）审批流程：
 * - 排产变更、安灯停线、安灯升级等关键操作需用户确认
 * - 审批通过后执行对应操作
 */
import request from './request';

/**
 * 获取待审批列表
 */
export async function getPendingApprovals() {
  return await request.get('/approval/pending');
}

/**
 * 审批通过
 * @param {string} approvalId - 审批 ID
 * @param {string} approvedBy - 审批人
 */
export async function approveRequest(approvalId, approvedBy = 'user') {
  return await request.post('/approval/approve', {
    approval_id: approvalId,
    approved_by: approvedBy,
  });
}

/**
 * 拒绝审批
 * @param {string} approvalId - 审批 ID
 * @param {string} rejectReason - 拒绝理由（可选）
 */
export async function rejectRequest(approvalId, rejectReason = '') {
  return await request.post('/approval/reject', {
    approval_id: approvalId,
    reject_reason: rejectReason,
  });
}

/**
 * 执行已审批通过的操作
 * @param {string} approvalId - 审批 ID
 */
export async function executeApproved(approvalId) {
  return await request.post(`/approval/execute/${approvalId}`);
}
