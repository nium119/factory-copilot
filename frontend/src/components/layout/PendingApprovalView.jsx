import React, { useState, useEffect, useCallback } from 'react';
import { Button, Spin, Empty, message, Tag, Popconfirm, Input, Tabs, Checkbox, Space, Divider, Pagination, Modal } from 'antd';
import { CheckOutlined, CloseOutlined, ReloadOutlined, DeleteOutlined, CheckCircleOutlined } from '@ant-design/icons';
import store from 'store2';
import { getPendingConfirmations, getProcessedConfirmations, approveConfirmation, rejectConfirmation, batchApproveConfirmations, batchRejectConfirmations, batchDeleteMessages, acceptReview, rollbackReview } from '../../services/messageService';
import { addSSEListener, removeSSEListener } from '../../services/sse';
import { useConversationStore } from '../../stores/ConversationContext';

function getUserId() {
  const user = store('__SRMC_Data_user');
  return user?.UserAccount || user?.NowLoginUser || '';
}

export default function PendingApprovalView() {
  const [activeTab, setActiveTab] = useState('pending');
  const [list, setList] = useState([]);
  const [processedList, setProcessedList] = useState([]);
  const [loading, setLoading] = useState(false);
  const [rejectReason, setRejectReason] = useState('');
  const [rejectingId, setRejectingId] = useState(null);
  // 批量选择
  const [selectionMode, setSelectionMode] = useState(false);
  const [selectedIds, setSelectedIds] = useState([]);
  // 分页
  const [pendingPage, setPendingPage] = useState(1);
  const [pendingTotal, setPendingTotal] = useState(0);
  const [processedPage, setProcessedPage] = useState(1);
  const [processedTotal, setProcessedTotal] = useState(0);
  const PAGE_SIZE = 20;
  const { setViewConversation } = useConversationStore();

  // 打开原对话：右侧抽屉展示该会话上下文（优先定位到方案消息附近）
  const handleOpenConversation = (convId, messageId = '') => {
    if (!convId) return;
    setViewConversation(convId, messageId || '');
  };

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const userId = getUserId();
      const userRoles = localStorage.getItem('user_roles') || '';
      const data = await getPendingConfirmations(userId, userRoles, pendingPage, PAGE_SIZE);
      setList(data.pending || []);
      setPendingTotal(data.total || 0);
    } catch {
      // 静默降级
    } finally {
      setLoading(false);
    }
  }, [pendingPage]);

  const refreshProcessed = useCallback(async () => {
    try {
      const data = await getProcessedConfirmations(processedPage, PAGE_SIZE);
      setProcessedList(data.processed || []);
      setProcessedTotal(data.total || 0);
    } catch { /* ignore */ }
  }, [processedPage]);

  // SSE 实时监听审批事件
  useEffect(() => {
    refresh();
    refreshProcessed();
    const sseKey = 'pending-approval';
    addSSEListener(sseKey, (type) => {
      if (type === 'pending_updated') refresh();
      if (type === 'approval_done') { refresh(); refreshProcessed(); }
    });
    return () => removeSSEListener(sseKey);
  }, [refresh, refreshProcessed]);

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

  // ── 责任分离复核：接受（验证失败结果可接受）/ 回滚（撤销变更）──
  const handleAcceptReview = async (msgId) => {
    try {
      const userId = getUserId();
      await acceptReview(msgId, userId, '');
      message.success('已接受复核');
      refresh();
    } catch (e) {
      message.error(e.message || '操作失败');
    }
  };

  const handleRollbackReview = async (msgId) => {
    try {
      const userId = getUserId();
      await rollbackReview(msgId, userId, rejectReason);
      message.success('已触发回滚');
      setRejectReason('');
      setRejectingId(null);
      refresh();
    } catch (e) {
      message.error(e.message || '操作失败');
    }
  };

  // ── 批量操作 ──
  const toggleSelectionMode = () => {
    setSelectionMode(!selectionMode);
    setSelectedIds([]);
  };

  const toggleSelection = (id) => {
    setSelectedIds(prev =>
      prev.includes(id) ? prev.filter(i => i !== id) : [...prev, id]
    );
  };

  const toggleSelectAll = () => {
    const currentList = activeTab === 'pending' ? list : processedList;
    const allIds = currentList.map(item => item.id);
    setSelectedIds(selectedIds.length === allIds.length ? [] : allIds);
  };

  const handleBatchApprove = async () => {
    if (selectedIds.length === 0) return;
    try {
      const userId = getUserId();
      const result = await batchApproveConfirmations(selectedIds, userId);
      message.success(`批量通过: ${result.success} 条成功` + (result.failed?.length ? `, ${result.failed.length} 条失败` : ''));
      setSelectedIds([]);
      refresh();
    } catch {
      message.error('批量操作失败');
    }
  };

  const handleBatchReject = async () => {
    if (selectedIds.length === 0) return;
    try {
      const userId = getUserId();
      const result = await batchRejectConfirmations(selectedIds, userId, rejectReason);
      message.success(`批量拒绝: ${result.success} 条成功` + (result.failed?.length ? `, ${result.failed.length} 条失败` : ''));
      setRejectReason('');
      setSelectedIds([]);
      refresh();
    } catch {
      message.error('批量操作失败');
    }
  };

  const handleBatchDelete = () => {
    if (selectedIds.length === 0) return;
    Modal.confirm({
      title: `确认删除 ${selectedIds.length} 条记录？`,
      content: '此操作不可恢复',
      okText: '确认删除',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: async () => {
        try {
          const result = await batchDeleteMessages(selectedIds);
          message.success(`已删除 ${result.deleted} 条`);
          setSelectedIds([]);
          setSelectionMode(false);
          activeTab === 'pending' ? refresh() : refreshProcessed();
        } catch {
          message.error('删除失败');
        }
      },
    });
  };

  const RISK_COLORS = { high: '#ff4d4f', medium: '#faad14', low: '#52c41a', exception: '#722ed1' };
  const RISK_LABELS = { high: '高风险', medium: '中风险', low: '低风险', exception: '异常' };

  // 渲染审批卡片
  const renderCard = (item, isPending) => {
    const pack = item.decision_pack || {};
    const isReview = item.message_type === 'review';
    const isOwnReview = isReview && !!(item.submitter_id) && item.submitter_id === getUserId();
    const riskColor = RISK_COLORS[pack.risk_level] || (isReview ? '#fa8c16' : '#d9d9d9');
    const isSelected = selectedIds.includes(item.id);
    return (
      <div key={item.id} style={{
        width: '100%', padding: '16px 20px',
        background: isSelected ? '#e6f4ff' : '#fff', borderRadius: 8,
        position: 'relative', boxSizing: 'border-box', display: 'block',
        border: `1px solid ${isSelected ? '#1677ff' : (isPending ? riskColor : '#e8e8e8')}`,
        borderLeft: `4px solid ${isSelected ? '#1677ff' : riskColor}`,
        boxShadow: '0 1px 3px rgba(0,0,0,0.04)',
        cursor: selectionMode ? 'pointer' : 'default',
      }} onClick={() => selectionMode && toggleSelection(item.id)}>
        {selectionMode && (
          <Checkbox checked={isSelected} style={{ position: 'absolute', top: 12, left: 12 }}
            onClick={(e) => e.stopPropagation()} onChange={() => toggleSelection(item.id)} />
        )}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', width: '100%', marginBottom: 8, marginLeft: selectionMode ? 28 : 0 }}>
          <div>
            <div style={{ fontWeight: 600, fontSize: 15, color: '#333', marginBottom: 4 }}>
              {item.action_label || item.tool}
              {item.concept_label && <span style={{ color: '#8c8c8c', fontWeight: 400, marginLeft: 8 }}>→ {item.concept_label}</span>}
              <Tag color={riskColor} style={{ marginLeft: 8, fontSize: 10 }}>
                {isReview ? '待复核' : (RISK_LABELS[pack.risk_level] || '操作')}
              </Tag>
              {!isPending && (
                isReview
                  ? <Tag color={item.status === 'approved' ? 'green' : 'red'} style={{ marginLeft: 8, fontSize: 10 }}>
                      {item.status === 'approved' ? '已复核接受' : '已回滚'}
                    </Tag>
                  : <Tag color={item.status === 'approved' ? 'green' : 'red'} style={{ marginLeft: 8, fontSize: 10 }}>
                      {item.status === 'approved' ? '已通过' : '已拒绝'}
                    </Tag>
              )}
            </div>
            <div style={{ fontSize: 12, color: '#8c8c8c', marginBottom: 2 }}>
              {isReview
                ? (item.submitter_id && <span>执行人: {item.submitter_id} · </span>)
                : (item.user_id && <span>提交人: {item.user_id} · </span>)}
              {item.conversation_title && item.conversation_id && (
                <a onClick={(e) => { e.preventDefault(); handleOpenConversation(item.conversation_id, item.message_id); }}
                  style={{ cursor: 'pointer', color: '#5b6ef7' }} title="打开原对话">
                  📎 原对话: {item.conversation_title} · </a>
              )}
              {item.assigned_to && <span>{isReview ? '复核角色' : '审批角色'}: {item.assigned_to} · </span>}
              {item.reviewed_by && (
                <span>
                  {item.status === 'approved'
                    ? (isReview ? '复核人' : '通过人')
                    : (isReview ? '回滚人' : '拒绝人')}: {item.reviewed_by} · </span>
              )}
              {item.created_at && <span>{new Date(item.created_at).toLocaleString()}</span>}
            </div>
            {item.message && (
              <div style={{ fontSize: 12, color: '#999', fontStyle: 'italic' }}>
                原始消息: "{item.message.length > 60 ? item.message.slice(0, 60) + '...' : item.message}"
              </div>
            )}
          </div>
          {isPending && (
            <div style={{ display: 'flex', gap: 8, flexShrink: 0 }}>
              {isReview ? (
                <>
                  {isOwnReview ? (
                    <span style={{ fontSize: 11, color: '#faad14', alignSelf: 'center' }} title="责任分离：执行人不能复核自己的变更">
                      ⚠ 执行人不可复核
                    </span>
                  ) : (
                    <>
                      <Button type="primary" size="small" icon={<CheckOutlined />}
                        onClick={() => handleAcceptReview(item.id)}>接受</Button>
                      <Popconfirm
                        title={<div style={{ width: 220 }}>
                          <div style={{ marginBottom: 8, fontSize: 13 }}>回滚原因（可选）</div>
                          <Input.TextArea size="small" rows={2} value={rejectingId === item.id ? rejectReason : ''}
                            onChange={(e) => { setRejectReason(e.target.value); setRejectingId(item.id); }}
                            placeholder="填写回滚原因..." />
                        </div>}
                        icon={null} okText="确认回滚" cancelText="取消"
                        onConfirm={() => handleRollbackReview(item.id)}
                        onCancel={() => { setRejectReason(''); setRejectingId(null); }}
                        okButtonProps={{ danger: true, size: 'small' }}
                        cancelButtonProps={{ size: 'small' }}
                      >
                        <Button danger size="small" icon={<CloseOutlined />}>回滚</Button>
                      </Popconfirm>
                    </>
                  )}
                </>
              ) : item.risk === 'exception' ? (
                <Button type="primary" size="small" icon={<CheckOutlined />}
                  onClick={() => handleApprove(item.id)}>已处理</Button>
              ) : (
                <>
                  <Button type="primary" size="small" icon={<CheckOutlined />}
                    onClick={() => handleApprove(item.id)}>通过</Button>
                  <Popconfirm
                    title={<div style={{ width: 220 }}>
                      <div style={{ marginBottom: 8, fontSize: 13 }}>拒绝原因（可选）</div>
                      <Input.TextArea size="small" rows={2} value={rejectingId === item.id ? rejectReason : ''}
                        onChange={(e) => { setRejectReason(e.target.value); setRejectingId(item.id); }}
                        placeholder="填写拒绝原因..." />
                    </div>}
                    icon={null} okText="确认拒绝" cancelText="取消"
                    onConfirm={() => handleReject(item.id)}
                    onCancel={() => { setRejectReason(''); setRejectingId(null); }}
                    okButtonProps={{ danger: true, size: 'small' }}
                    cancelButtonProps={{ size: 'small' }}
                  >
                    <Button danger size="small" icon={<CloseOutlined />}>拒绝</Button>
                  </Popconfirm>
                </>
              )}
            </div>
          )}
        </div>
        {/* 复核：验证结果对比区（期望 vs 实际） */}
        {isReview && ((item.verify_detail || []).length > 0 || item.verify_summary) && (
          <div style={{ margin: '8px -20px -16px', padding: '8px 20px', background: '#fffbe6', borderTop: '1px solid #ffe58f', borderRadius: '0 0 8px 8px' }}>
            {item.verify_summary && (
              <div style={{ fontSize: 12, color: '#ad6800', marginBottom: (item.verify_detail || []).length ? 4 : 0 }}>
                ⚠️ {item.verify_summary}
              </div>
            )}
            {(item.verify_detail || []).length > 0 && (
              <>
                <div style={{ fontSize: 12, color: '#8c8c8c', marginBottom: 4 }}>验证结果对比：</div>
                {item.verify_detail.map((d, i) => (
                  <div key={i} style={{ display: 'flex', gap: 12, fontSize: 12, marginBottom: 2, flexWrap: 'wrap' }}>
                    <span style={{ minWidth: 90, color: '#666', fontWeight: 500 }}>{d.property || d.propertyKey || '-'}</span>
                    <span style={{ color: '#999' }}>期望: <b style={{ color: '#333' }}>{d.expected !== undefined && d.expected !== null ? String(d.expected) : '-'}</b></span>
                    <span style={{ color: '#999' }}>实际: <b style={{ color: '#333' }}>{d.actual !== undefined && d.actual !== null ? String(d.actual) : '-'}</b></span>
                    <span style={{ color: d.match === true ? '#52c41a' : d.match === false ? '#ff4d4f' : '#faad14' }}>
                      {d.match === true ? '✅ 一致' : d.match === false ? '❌ 不一致' : '? 未判定'}
                    </span>
                  </div>
                ))}
              </>
            )}
          </div>
        )}
        {/* 详情区——放在 flex 行外面，作为卡片直接子元素，撑满全宽 */}
        {item.error_detail && (
          <div style={{ margin: '8px -20px -16px', padding: '6px 20px', fontSize: 12, color: '#722ed1', background: '#f9f0ff', borderTop: '1px solid #efdbff', borderRadius: '0 0 8px 8px', whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
            🛠️ {item.error_detail}
          </div>
        )}
        {(pack.related_entities || []).length > 0 && (
          <div style={{ marginTop: 6, fontSize: 12, color: '#666' }}>
            <span style={{ color: '#999' }}>关联信息：</span>
            {pack.related_entities.map((e, i) => (
              <Tag key={i} color="blue" style={{ fontSize: 10, marginBottom: 2 }}>{e.label}: {e.value}</Tag>
            ))}
          </div>
        )}
        {(pack.rule_checks || []).length > 0 && (
          <div style={{ marginTop: 6, fontSize: 12 }}>
            {pack.rule_checks.map((rc, i) => (
              <div key={i} style={{ color: rc.passed ? '#52c41a' : '#ff4d4f' }}>
                {rc.passed ? '✅' : '❌'} {rc.label}
              </div>
            ))}
          </div>
        )}
        {(item.param_schema || []).length > 0 ? (
          <div style={{ margin: '12px -20px -16px', padding: '8px 20px', background: '#fafafa', borderTop: '1px solid #f0f0f0', borderRadius: '0 0 8px 8px' }}>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 16px', fontSize: 12, color: '#666' }}>
              {item.param_schema.map(p => {
                const val = (item.params || {})[p.name];
                const hasVal = val !== undefined && val !== null && val !== '';
                return <span key={p.name} style={{ color: hasVal ? '#333' : '#bbb' }}>
                  <strong>{p.label || p.name}:</strong> {hasVal ? String(val) : '-'}
                  {p.required && <span style={{ color: '#ff4d4f', fontSize: 10 }}> *</span>}
                </span>;
              })}
            </div>
          </div>
        ) : item.params && Object.keys(item.params).length > 0 && (
          <div style={{ margin: '12px -20px -16px', padding: '8px 20px', background: '#fafafa', borderTop: '1px solid #f0f0f0', borderRadius: '0 0 8px 8px' }}>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 16px', fontSize: 12, color: '#666' }}>
              {Object.entries(item.params).map(([k, v]) => <span key={k}><strong>{k}:</strong> {String(v)}</span>)}
            </div>
          </div>
        )}
      </div>
    );
  };

  const currentList = activeTab === 'pending' ? list : processedList;
  const isPendingTab = activeTab === 'pending';

  return (
    <div style={{ padding: '24px 24px 48px', height: '100%', overflow: 'auto', background: '#f5f5f7', boxSizing: 'border-box' }}>
      <div style={{ maxWidth: 800, margin: '0 auto' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
          <h2 style={{ margin: 0, fontSize: 18, fontWeight: 600 }}>审批管理</h2>
          <Space>
            {!selectionMode ? (
              <Button icon={<CheckCircleOutlined />} onClick={toggleSelectionMode}>批量选择</Button>
            ) : (
              <Button onClick={toggleSelectionMode}>完成</Button>
            )}
            <Button icon={<ReloadOutlined />} onClick={() => { refresh(); refreshProcessed(); }} loading={loading}>刷新</Button>
          </Space>
        </div>

        {/* 批量操作栏 */}
        {selectionMode && (
          <>
            <div style={{
              display: 'flex', alignItems: 'center', gap: 12, marginBottom: 8,
              padding: '8px 16px', background: '#fff', borderRadius: 8, border: '1px solid #e8e8e8',
            }}>
              <Tag color="blue">{selectedIds.length} 项已选</Tag>
              <Button size="small" onClick={toggleSelectAll}>
                {selectedIds.length === currentList.length ? '取消全选' : '全选'}
              </Button>
              {isPendingTab && (
                <>
                  <Button type="primary" size="small" icon={<CheckOutlined />}
                    disabled={selectedIds.length === 0} onClick={handleBatchApprove}>
                    批量通过
                  </Button>
                  <Popconfirm
                    title={<div style={{ width: 240 }}>
                      <div style={{ marginBottom: 8, fontSize: 13 }}>批量拒绝原因（可选）</div>
                      <Input.TextArea rows={2} value={rejectReason}
                        onChange={(e) => setRejectReason(e.target.value)}
                        placeholder="填写拒绝原因..." />
                    </div>}
                    icon={null} okText="确认拒绝" cancelText="取消"
                    onConfirm={handleBatchReject}
                    onCancel={() => setRejectReason('')}
                    okButtonProps={{ danger: true, size: 'small' }}
                    cancelButtonProps={{ size: 'small' }}
                  >
                    <Button danger size="small" icon={<CloseOutlined />}
                      disabled={selectedIds.length === 0}>批量拒绝</Button>
                  </Popconfirm>
                </>
              )}
              <Button danger size="small" icon={<DeleteOutlined />}
                disabled={selectedIds.length === 0} onClick={handleBatchDelete}>
                批量删除
              </Button>
            </div>
            <Divider style={{ margin: '4px 0 12px' }} />
          </>
        )}

        <Tabs activeKey={activeTab} onChange={(key) => {
          setActiveTab(key);
          setSelectionMode(false);
          setSelectedIds([]);
          if (key === 'pending') refresh(); else refreshProcessed();
        }} items={[
          { key: 'pending', label: `待审批 (${pendingTotal})`, children: (
            loading && list.length === 0 ? <div style={{ textAlign: 'center', padding: 60 }}><Spin /></div>
            : list.length === 0 ? <Empty description="暂无待审批" style={{ padding: 60 }} />
            : <>
              <div style={{ width: '100%' }}>
                {list.map(item => <div key={item.id} style={{ marginBottom: 12 }}>{renderCard(item, true)}</div>)}
              </div>
              <div style={{ textAlign: 'center', marginTop: 16 }}>
                <Pagination current={pendingPage} pageSize={PAGE_SIZE} total={pendingTotal}
                  onChange={(p) => { setPendingPage(p); setSelectedIds([]); }} showSizeChanger={false} size="small" />
              </div>
            </>
          )},
          { key: 'processed', label: `已处理 (${processedTotal})`, children: (
            processedList.length === 0 ? <Empty description="暂无已审批" style={{ padding: 60 }} />
            : <>
              <div style={{ width: '100%' }}>
                {processedList.map(item => <div key={item.id} style={{ marginBottom: 12 }}>{renderCard(item, false)}</div>)}
              </div>
              <div style={{ textAlign: 'center', marginTop: 16 }}>
                <Pagination current={processedPage} pageSize={PAGE_SIZE} total={processedTotal}
                  onChange={(p) => { setProcessedPage(p); setSelectedIds([]); }} showSizeChanger={false} size="small" />
              </div>
            </>
          )},
        ]} />
      </div>
    </div>
  );
}
