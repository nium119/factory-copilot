import React, { useState, useEffect, useCallback } from 'react';
import { Button, Spin, Empty, message } from 'antd';
import { CheckOutlined, CloseOutlined, ReloadOutlined } from '@ant-design/icons';
import { getPendingConfirmations, approveConfirmation, rejectConfirmation } from '../../services/messageService';

export default function PendingApprovalView() {
  const [list, setList] = useState([]);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const userId = localStorage.getItem('user_id') || '';
      const userRoles = localStorage.getItem('user_roles') || '';
      const data = await getPendingConfirmations(userId, userRoles);
      setList(data.pending || []);
    } catch {
      // 静默降级
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 15000);
    return () => clearInterval(interval);
  }, [refresh]);

  const handleApprove = async (msgId) => {
    try {
      const userId = localStorage.getItem('user_id') || '';
      await approveConfirmation(msgId, userId, '');
      message.success('已通过');
      refresh();
    } catch {
      message.error('操作失败');
    }
  };

  const handleReject = async (msgId) => {
    try {
      const userId = localStorage.getItem('user_id') || '';
      await rejectConfirmation(msgId, userId, '');
      message.success('已拒绝');
      refresh();
    } catch {
      message.error('操作失败');
    }
  };

  return (
    <div style={{ padding: 24, height: '100%', overflow: 'auto', background: '#f5f5f7' }}>
      <div style={{ maxWidth: 800, margin: '0 auto' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
          <h2 style={{ margin: 0, fontSize: 18, fontWeight: 600 }}>⏳ 待审批操作</h2>
          <Button icon={<ReloadOutlined />} onClick={refresh} loading={loading}>刷新</Button>
        </div>

        {loading && list.length === 0 ? (
          <div style={{ textAlign: 'center', padding: 60 }}><Spin /></div>
        ) : list.length === 0 ? (
          <Empty description="暂无待审批操作" style={{ padding: 60 }} />
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {list.map(item => (
              <div key={item.id} style={{
                padding: '16px 20px', background: '#fff', borderRadius: 8,
                border: '1px solid #f0e0c0', boxShadow: '0 1px 3px rgba(0,0,0,0.04)',
              }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 8 }}>
                  <div>
                    <div style={{ fontWeight: 600, fontSize: 15, color: '#333', marginBottom: 4 }}>
                      {item.action_label || item.tool}
                      {item.concept_label && <span style={{ color: '#8c8c8c', fontWeight: 400, marginLeft: 8 }}>→ {item.concept_label}</span>}
                    </div>
                    <div style={{ fontSize: 12, color: '#8c8c8c' }}>
                      {item.assigned_to && <span>审批角色: {item.assigned_to} · </span>}
                      {item.created_at && <span>{new Date(item.created_at).toLocaleString()}</span>}
                    </div>
                  </div>
                  <div style={{ display: 'flex', gap: 8 }}>
                    <Button type="primary" size="small" icon={<CheckOutlined />}
                      onClick={() => handleApprove(item.id)}>通过</Button>
                    <Button danger size="small" icon={<CloseOutlined />}
                      onClick={() => handleReject(item.id)}>拒绝</Button>
                  </div>
                </div>
                {item.params && Object.keys(item.params).length > 0 && (
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 16px', fontSize: 12, color: '#666' }}>
                    {Object.entries(item.params).map(([k, v]) => (
                      <span key={k}><strong>{k}:</strong> {String(v)}</span>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
