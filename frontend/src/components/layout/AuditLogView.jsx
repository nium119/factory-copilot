import React, { useRef, useState, useEffect } from 'react';
import { Tag, Switch, Space } from 'antd';
import { ProTable } from '@ant-design/pro-components';
import request from '../../services/request';

export default function AuditLogView() {
  const actionRef = useRef();
  const [autoRollback, setAutoRollback] = useState(false);

  // 自动回滚开关（DB 配置，优先级高于 .env）
  useEffect(() => {
    request.get('/chains/compile/auto-rollback').then((d) => setAutoRollback(!!d.enabled)).catch(() => {});
  }, []);

  const toggleRollback = (v) => {
    setAutoRollback(v);
    request.post('/chains/compile/auto-rollback', { enabled: v }).catch(() => {});
  };

  const columns = [
    { title: '时间', dataIndex: 'timestamp', width: 165, search: false,
      render: (_, r) => (r.timestamp || '').replace('T', ' ').substring(0, 19) },
    { title: '关键词', dataIndex: 'keyword', hideInTable: true },
    { title: '动作', dataIndex: 'action', width: 170, search: false,
      render: (_, r) => <code style={{ fontSize: 12 }}>{r.action || '-'}</code> },
    { title: '类型', dataIndex: 'tool', width: 120, search: false,
      render: (_, r) => <Tag style={{ fontSize: 11 }}>{r.tool || '-'}</Tag> },
    { title: '结果', dataIndex: 'success', width: 90, align: 'center', search: false,
      render: (_, r) => r.success ? <Tag color="success">成功</Tag> : <Tag color="error">失败/复核</Tag> },
    { title: '内容', dataIndex: 'result_preview', ellipsis: true, search: false,
      render: (_, r) => r.result_preview || '-' },
  ];

  return (
    <div style={{ padding: '24px 24px 48px', height: '100%', overflow: 'auto', background: '#f5f5f7', boxSizing: 'border-box' }}>
      <ProTable
        actionRef={actionRef}
        columns={columns}
        rowKey={(r, i) => `${i}-${r.timestamp}`}
        search={{ labelWidth: 'auto', defaultCollapsed: false }}
        options={{ reload: true, density: true }}
        pagination={{ defaultPageSize: 20, showSizeChanger: false, showTotal: (t) => `共 ${t} 条` }}
        scroll={{ x: 'max-content' }}
        headerTitle="审计日志"
        toolBarRender={() => [
          <Space key="rollback">
            <Switch checked={autoRollback} onChange={toggleRollback} size="small" />
            <span style={{ fontSize: 12, color: '#666' }}>验证失败自动回滚（高风险，默认关）</span>
          </Space>,
        ]}
        request={async (params) => {
          const qs = new URLSearchParams({ limit: '200' });
          if (params.keyword) qs.set('keyword', params.keyword);
          const data = await request.get(`/messages/audit/logs?${qs}`);
          return { data: data.logs || [], total: data.total || 0, success: true };
        }}
        locale={{ emptyText: '暂无审计日志' }}
      />
    </div>
  );
}
