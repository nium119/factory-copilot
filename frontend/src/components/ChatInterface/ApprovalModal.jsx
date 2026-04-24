/**
 * 审批弹窗组件
 *
 * 用于 HITL（Human-in-the-Loop）审批流程。
 * 当后端发出 approval_request SSE 事件时触发此弹窗。
 *
 * Props:
 *   approval       object  {approval_id, action, action_name, risk_level, description, details}
 *   visible        bool    是否显示
 *   onApprove      func    审批通过回调 → await approveRequest → await executeApproved
 *   onReject       func    拒绝回调 → await rejectRequest
 *   onCancel       func    关闭弹窗
 */
import React, { useState } from 'react';
import { Modal, Button, Input, Spin } from 'antd';
import { WarningOutlined, CheckCircleOutlined, CloseCircleOutlined } from '@ant-design/icons';

const { TextArea } = Input;

// 风险等级颜色
const RISK_COLORS = {
  high: '#ff4d4f',
  medium: '#faad14',
  low: '#52c413',
};

const RISK_LABELS = {
  high: '高风险',
  medium: '中风险',
  low: '低风险',
};

function ApprovalModal({ approval, visible, onApprove, onReject, onCancel }) {
  const [rejectReason, setRejectReason] = useState('');
  const [loading, setLoading] = useState(false);

  if (!approval) return null;

  const riskColor = RISK_COLORS[approval.risk_level] || '#999';
  const riskLabel = RISK_LABELS[approval.risk_level] || '未知';

  const handleApprove = async () => {
    setLoading(true);
    try {
      await onApprove(approval);
    } finally {
      setLoading(false);
    }
  };

  const handleReject = async () => {
    setLoading(true);
    try {
      await onReject(approval, rejectReason);
      setRejectReason('');
    } finally {
      setLoading(false);
    }
  };

  return (
    <Modal
      title={
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <WarningOutlined style={{ color: riskColor, fontSize: '18px' }} />
          <span>操作审批</span>
        </div>
      }
      open={visible}
      onCancel={onCancel}
      footer={null}
      width={480}
      maskClosable={false}
    >
      {/* 风险等级横幅 */}
      <div style={{
        background: `${riskColor}15`,
        border: `1px solid ${riskColor}40`,
        borderRadius: '8px',
        padding: '10px 14px',
        marginBottom: '16px',
        display: 'flex',
        alignItems: 'center',
        gap: '8px',
      }}>
        <span style={{
          background: riskColor,
          color: '#fff',
          borderRadius: '4px',
          padding: '2px 8px',
          fontSize: '12px',
          fontWeight: 600,
        }}>
          {riskLabel}
        </span>
        <span style={{ fontSize: '14px', fontWeight: 500 }}>{approval.action_name}</span>
      </div>

      {/* 描述 */}
      <div style={{ marginBottom: '12px' }}>
        <div style={{ fontSize: '12px', color: '#999', marginBottom: '4px' }}>操作描述</div>
        <div style={{ fontSize: '13px', color: '#333' }}>{approval.description}</div>
      </div>

      {/* 详情 key-value */}
      {approval.details && Object.keys(approval.details).length > 0 && (
        <div style={{ marginBottom: '16px' }}>
          <div style={{ fontSize: '12px', color: '#999', marginBottom: '6px' }}>详细信息</div>
          <div style={{
            background: '#fafafa',
            borderRadius: '6px',
            padding: '8px 12px',
            fontSize: '13px',
          }}>
            {Object.entries(approval.details).map(([key, value]) => (
              <div key={key} style={{ marginBottom: '4px', display: 'flex' }}>
                <span style={{ color: '#666', minWidth: '80px' }}>{key}:</span>
                <span style={{ color: '#333' }}>{String(value)}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 操作按钮 */}
      <div style={{ display: 'flex', gap: '12px', marginTop: '20px' }}>
        <Button
          type="primary"
          danger={false}
          icon={loading ? <Spin size="small" /> : <CheckCircleOutlined />}
          onClick={handleApprove}
          loading={loading}
          style={{ flex: 1, backgroundColor: '#52c413', borderColor: '#52c413' }}
        >
          审批通过
        </Button>
        <Button
          danger
          icon={<CloseCircleOutlined />}
          onClick={handleReject}
          loading={loading}
          style={{ flex: 1 }}
        >
          拒绝
        </Button>
      </div>

      {/* 拒绝理由输入（可选） */}
      <TextArea
        rows={2}
        value={rejectReason}
        onChange={(e) => setRejectReason(e.target.value)}
        placeholder="拒绝理由（可选）"
        style={{ marginTop: '12px', fontSize: '12px' }}
        maxLength={200}
      />
    </Modal>
  );
}

export default ApprovalModal;
