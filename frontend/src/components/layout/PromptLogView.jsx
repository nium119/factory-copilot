import React, { useRef, useState } from 'react';
import { Tag, Typography } from 'antd';
import { ProTable } from '@ant-design/pro-components';
import request from '../../services/request';

export default function PromptLogView() {
  const actionRef = useRef();
  const [expandedKeys, setExpandedKeys] = useState([]);

  const columns = [
    { title: '时间', dataIndex: 'created_at', width: 150, search: false,
      render: (_, r) => r.created_at ? new Date(r.created_at).toLocaleString() : '-' },
    { title: '模型', dataIndex: 'model', width: 160, search: false,
      render: (_, r) => <Tag color="blue">{r.model}</Tag> },
    { title: '用户消息', dataIndex: 'user_message', ellipsis: true,
      render: (_, r) => r.user_message ? <span style={{ fontSize: 12 }}>{r.user_message.length > 60 ? r.user_message.slice(0, 60) + '...' : r.user_message}</span> : '-' },
    { title: '提示词长度', dataIndex: 'system_prompt_len', width: 100, search: false,
      render: (_, r) => r.system_prompt_len || '-' },
    { title: '输入Token', dataIndex: 'input_tokens', width: 100, search: false,
      render: (_, r) => r.input_tokens ? r.input_tokens.toLocaleString() : '-' },
    { title: '输出Token', dataIndex: 'output_tokens', width: 100, search: false,
      render: (_, r) => r.output_tokens ? r.output_tokens.toLocaleString() : '-' },
    { title: '深度思考', dataIndex: 'enable_thinking', width: 90, search: false,
      render: (_, r) => r.enable_thinking ? <Tag color="purple">思考</Tag> : '-' },
  ];

  return (
    <div style={{ padding: '24px 24px 48px', height: '100%', overflow: 'auto', background: '#f5f5f7', boxSizing: 'border-box' }}>
      <ProTable
        actionRef={actionRef}
        columns={columns}
        rowKey="id"
        search={{ labelWidth: 'auto', defaultCollapsed: false }}
        options={{ reload: true, density: true }}
        pagination={{ defaultPageSize: 20, showSizeChanger: false }}
        scroll={{ x: 'max-content' }}
        headerTitle="提示词日志"
        expandable={{
          expandedRowRender: (record) => (
            <div style={{ padding: '12px 16px', background: '#fafafa' }}>
              <Typography.Title level={5} style={{ fontSize: 14, marginBottom: 8 }}>System Prompt ({record.system_prompt_len} 字符)</Typography.Title>
              <pre style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontSize: 12, lineHeight: 1.8, color: '#555', background: '#fff', padding: '12px 16px', borderRadius: 6, border: '1px solid #f0f0f0', maxHeight: 400, overflow: 'auto' }}>
                {record.system_prompt || '(未记录)'}
              </pre>
              <Typography.Title level={5} style={{ fontSize: 14, marginTop: 12, marginBottom: 8 }}>User Message</Typography.Title>
              <pre style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontSize: 12, lineHeight: 1.8, color: '#555', background: '#fff', padding: '12px 16px', borderRadius: 6, border: '1px solid #f0f0f0', maxHeight: 300, overflow: 'auto' }}>
                {record.user_message || '(未记录)'}
              </pre>
            </div>
          ),
          expandedRowKeys: expandedKeys,
          onExpand: (expanded, record) => setExpandedKeys(expanded ? [record.id] : []),
        }}
        onRow={(record) => ({
          onClick: () => setExpandedKeys(expandedKeys.includes(record.id) ? [] : [record.id]),
          style: { cursor: 'pointer' },
        })}
        request={async (params) => {
          const qs = new URLSearchParams({ page: params.current || 1, page_size: params.pageSize || 20 });
          if (params.user_message) qs.set('keyword', params.user_message);
          const data = await request.get(`/messages/prompt-logs?${qs}`);
          return { data: data.logs || [], total: data.total || 0, success: true };
        }}
        locale={{ emptyText: '暂无提示词记录' }}
      />
    </div>
  );
}
