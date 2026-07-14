import React, { useState, useEffect, useCallback } from 'react';
import { Button, Spin, Empty, message, Tag, Popconfirm, Input } from 'antd';
import { CheckOutlined, CloseOutlined, ReloadOutlined } from '@ant-design/icons';
import store from 'store2';
import { getPendingConfirmations, approveConfirmation, rejectConfirmation } from '../../services/messageService';

function getUserId() {
  const user = store('__SRMC_Data_user');
  return user?.UserAccount || user?.NowLoginUser || getUserId();
}

export default function PendingApprovalView() {
  const [list, setList] = useState([]);
  const [loading, setLoading] = useState(false);
  const [rejectReason, setRejectReason] = useState('');
  const [rejectingId, setRejectingId] = useState(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const userId = getUserId();
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
      const userId = getUserId();
      await approveConfirmation(msgId, userId, '');
      message.success('已通过');
      refresh();
    } catch {
      message.error('操作失败');
    }
  };

  const handleReject = async (msgId) => {
    try {
      const userId = getUserId();
      await rejectConfirmation(msgId, userId, rejectReason);
      message.success('已拒绝');
      setRejectReason('');
      setRejectingId(null);
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
            {list.map(item => {
              // 用 param_schema 获取参数中文标签
              const schemaMap = {};
              (item.param_schema || []).forEach(p => { schemaMap[p.name] = p.label || p.name; });

              return (
              <div key={item.id} style={{
                padding: '16px 20px', background: '#fff', borderRadius: 8,
                border: '1px solid #f0e0c0', boxShadow: '0 1px 3px rgba(0,0,0,0.04)',
              }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 8 }}>
                  <div>
                    <div style={{ fontWeight: 600, fontSize: 15, color: '#333', marginBottom: 4 }}>
                      {item.action_label || item.tool}
                      {item.concept_label && <span style={{ color: '#8c8c8c', fontWeight: 400, marginLeft: 8 }}>→ {item.concept_label}</span>}
                      {item.risk === 'write' && <Tag color="orange" style={{ marginLeft: 8, fontSize: 10 }}>写操作</Tag>}
                    </div>
                    <div style={{ fontSize: 12, color: '#8c8c8c', marginBottom: 2 }}>
                      {item.user_id && <span>提交人: {item.user_id} · </span>}
                      {item.assigned_to && <span>审批角色: {item.assigned_to} · </span>}
                      {item.created_at && <span>{new Date(item.created_at).toLocaleString()}</span>}
                    </div>
                    {item.message && (
                      <div style={{ fontSize: 12, color: '#999', fontStyle: 'italic' }}>
                        原始消息: "{item.message.length > 60 ? item.message.slice(0, 60) + '...' : item.message}"
                      </div>
                    )}
                  </div>
                  <div style={{ display: 'flex', gap: 8, flexShrink: 0 }}>
                    <Button type="primary" size="small" icon={<CheckOutlined />}
                      onClick={() => handleApprove(item.id)}>通过</Button>
                    <Popconfirm
                      title={
                        <div style={{ width: 220 }}>
                          <div style={{ marginBottom: 8, fontSize: 13 }}>拒绝原因（可选）</div>
                          <Input.TextArea
                            size="small"
                            rows={2}
                            value={rejectingId === item.id ? rejectReason : ''}
                            onChange={(e) => { setRejectReason(e.target.value); setRejectingId(item.id); }}
                            placeholder="填写拒绝原因..."
                          />
                        </div>
                      }
                      icon={null}
                      okText="确认拒绝"
                      cancelText="取消"
                      onConfirm={() => handleReject(item.id)}
                      onCancel={() => { setRejectReason(''); setRejectingId(null); }}
                      okButtonProps={{ danger: true, size: 'small' }}
                      cancelButtonProps={{ size: 'small' }}
                    >
                      <Button danger size="small" icon={<CloseOutlined />}>拒绝</Button>
                    </Popconfirm>
                  </div>
                </div>
                {(item.param_schema || []).length > 0 && (
                  <div style={{
                    display: 'flex', flexWrap: 'wrap', gap: '4px 16px', fontSize: 12, color: '#666',
                    background: '#fafafa', padding: '8px 12px', borderRadius: 4,
                  }}>
                    {item.param_schema.map(p => {
                      const val = (item.params || {})[p.name];
                      const hasVal = val !== undefined && val !== null && val !== '';
                      return (
                        <span key={p.name} style={{ color: hasVal ? '#333' : '#bbb' }}>
                          <strong>{p.label || p.name}:</strong> {hasVal ? String(val) : '-'}
                          {p.required && <span style={{ color: '#ff4d4f', fontSize: 10 }}> *</span>}
                        </span>
                      );
                    })}
                  </div>
                )}
                {/* 兜底：无 schema 时直接显示 params */}
                {(!item.param_schema || item.param_schema.length === 0) && item.params && Object.keys(item.params).length > 0 && (
                  <div style={{
                    display: 'flex', flexWrap: 'wrap', gap: '4px 16px', fontSize: 12, color: '#666',
                    background: '#fafafa', padding: '8px 12px', borderRadius: 4,
                  }}>
                    {Object.entries(item.params).map(([k, v]) => (
                      <span key={k}><strong>{k}:</strong> {String(v)}</span>
                    ))}
                  </div>
                )}
                {item.context && Object.keys(item.context).length > 0 && (
                  <div style={{ fontSize: 11, color: '#999', marginTop: 4 }}>
                    上下文: {Object.entries(item.context).map(([k,v]) => `${k}=${v}`).join(', ')}
                  </div>
                )}
              </div>
            )})}
          </div>
        )}
      </div>
    </div>
  );
}
