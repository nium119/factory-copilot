import React from 'react';
import { Button, Tag, message } from 'antd';
import { BellOutlined } from '@ant-design/icons';
import { ProTable } from '@ant-design/pro-components';
import request from '../../services/request';

export default function NotificationList() {
  const [typeLabels, setTypeLabels] = React.useState({});
  const [expandedKeys, setExpandedKeys] = React.useState([]);
  const actionRef = React.useRef();

  React.useEffect(() => {
    request.get('/notifications/event-types').then(d => {
      const m = {};
      (d.items || []).forEach(e => { m[e.key] = e.label; });
      setTypeLabels(m);
    }).catch(() => {});
  }, []);

  return (
    <div style={{ height: '100%', overflow: 'auto', background: '#fff' }}>
      <div style={{ padding: '16px 24px', borderBottom: '1px solid #f0f0f0', display: 'flex', alignItems: 'center', gap: 12 }}>
        <BellOutlined style={{ fontSize: 18, color: '#6c5ce7' }} />
        <span style={{ fontSize: 16, fontWeight: 600 }}>通知列表</span>
      </div>
      <div style={{ padding: 24 }}>
        <ProTable
          actionRef={actionRef}
          columns={[
            { title: '时间', dataIndex: 'created_at', width: 170, search: false,
              render: (_, r) => r.created_at ? new Date(r.created_at).toLocaleString() : '-' },
            { title: '标题', dataIndex: 'title', width: 240, ellipsis: true },
            { title: '内容', dataIndex: 'body', ellipsis: true, search: false,
              render: (_, r) => <span style={{ color: '#888', fontSize: 12 }}>{r.body}</span> },
            { title: '类型', dataIndex: 'type', width: 100,
              valueType: 'select',
              valueEnum: typeLabels,
              render: (_, r) => <Tag>{typeLabels[r.type] || r.type}</Tag> },
            { title: '状态', dataIndex: 'status', width: 80, search: false,
              render: (_, r) => <Tag color={r.status === 'unread' ? 'red' : 'default'}>{r.status === 'unread' ? '未读' : r.status === 'read' ? '已读' : '归档'}</Tag> },
            { title: '操作', width: 80, search: false,
              render: (_, r) => r.status === 'unread' ? (
                <Button type="link" size="small" onClick={async (e) => {
                  e.stopPropagation();
                  await request.put(`/notifications/${r.id}/read`);
                  message.success('已标记为已读');
                  actionRef.current?.reload();
                }}>标记已读</Button>
              ) : null,
            },
          ]}
          rowKey="id"
          search={{ labelWidth: 'auto' }}
          expandable={{
            expandedRowRender: (r) => (
              <div style={{ padding: '12px 16px', background: '#fafafa', borderRadius: 4, fontSize: 13, lineHeight: 2 }}>
                <div style={{ marginBottom: 8, color: '#333' }}>{r.body}</div>
                <div style={{ fontSize: 12, color: '#888' }}>
                  {r.source && <span style={{ marginRight: 16 }}>触发源：{r.source}</span>}
                  {r.ref_conversation_id && <span style={{ marginRight: 16 }}>会话：<code>{r.ref_conversation_id}</code></span>}
                  {r.ref_chain_id && <span style={{ marginRight: 16 }}>执行链：<code>{r.ref_chain_id}</code></span>}
                  {r.read_at && <span>阅读于 {new Date(r.read_at).toLocaleString()}</span>}
                </div>
              </div>
            ),
            expandedRowKeys: expandedKeys,
            onExpand: (expanded, record) => setExpandedKeys(expanded ? [record.id] : []),
          }}
          onRow={(r) => ({
            onClick: () => setExpandedKeys(expandedKeys.includes(r.id) ? [] : [r.id]),
            style: { cursor: 'pointer' },
          })}
          options={{ reload: true, density: true }}
          pagination={{ defaultPageSize: 20, showSizeChanger: true, showTotal: (t) => `共 ${t} 条` }}
          headerTitle="通知列表"
          toolBarRender={() => [
            <Button key="readAll" onClick={async () => {
              await request.put('/notifications/read-all');
              message.success('已全部标记为已读');
              actionRef.current?.reload();
            }}>全部已读</Button>,
          ]}
          request={async (params) => {
            const data = await request.get('/notifications', { params: { status: 'all', limit: 50, title: params?.title || '', type: params?.type || '' } });
            return { data: data.items || [], total: data.total || 0, success: true };
          }}
          locale={{ emptyText: '暂无通知' }}
        />
      </div>
    </div>
  );
}
