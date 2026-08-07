import React, { useState, useEffect, useCallback } from 'react';
import { Table, Tag, Input, Space, Switch } from 'antd';
import request from '../../services/request';

export default function AuditLogView() {
  const [logs, setLogs] = useState([]);
  const [loading, setLoading] = useState(false);
  const [keyword, setKeyword] = useState('');
  const [autoRollback, setAutoRollback] = useState(false);

  // 自动回滚开关（DB 配置，优先级高于 .env）
  useEffect(() => {
    request.get('/chains/compile/auto-rollback').then((d) => setAutoRollback(!!d.enabled)).catch(() => {});
  }, []);

  const toggleRollback = (v) => {
    setAutoRollback(v);
    request.post('/chains/compile/auto-rollback', { enabled: v }).catch(() => {});
  };

  const fetchLogs = useCallback(async () => {
    setLoading(true);
    try {
      const data = await request.get(`/messages/audit/logs?limit=200${keyword ? `&keyword=${encodeURIComponent(keyword)}` : ''}`);
      setLogs(data.logs || []);
    } catch { /* ignore */ }
    finally { setLoading(false); }
  }, [keyword]);

  useEffect(() => { fetchLogs(); }, [fetchLogs]);

  const columns = [
    { title: '时间', dataIndex: 'timestamp', key: 'timestamp', width: 165, render: (v) => (v || '').replace('T', ' ').substring(0, 19) },
    { title: '动作', dataIndex: 'action', key: 'action', width: 170, render: (v) => <code style={{ fontSize: 12 }}>{v || '-'}</code> },
    { title: '类型', dataIndex: 'tool', key: 'tool', width: 120, render: (v) => <Tag style={{ fontSize: 11 }}>{v || '-'}</Tag> },
    {
      title: '结果', dataIndex: 'success', key: 'success', width: 90, align: 'center',
      render: (v) => v ? <Tag color="success">成功</Tag> : <Tag color="error">失败/复核</Tag>,
    },
    { title: '内容', dataIndex: 'result_preview', key: 'result_preview', render: (v) => v || '-' },
  ];

  return (
    <div style={{ padding: 24, height: '100%', overflow: 'auto', background: '#f5f5f7' }}>
      <div style={{ maxWidth: 1100, margin: '0 auto' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
          <h2 style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>审计日志</h2>
          <Space>
            <Switch checked={autoRollback} onChange={toggleRollback} size="small" />
            <span style={{ fontSize: 12, color: '#666' }}>验证失败自动回滚（高风险，默认关）</span>
            <Input.Search placeholder="搜索关键词" allowClear onSearch={setKeyword} style={{ width: 240 }} />
            <a onClick={fetchLogs}>刷新</a>
          </Space>
        </div>
        <Table
          rowKey={(r, i) => `${i}-${r.timestamp}`}
          dataSource={logs}
          columns={columns}
          size="small"
          loading={loading}
          pagination={{ pageSize: 20, showTotal: (t) => `共 ${t} 条` }}
          scroll={{ y: 'calc(100vh - 220px)' }}
        />
      </div>
    </div>
  );
}
