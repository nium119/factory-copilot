import React, { useState, useEffect, useCallback } from 'react';
import { Badge, Button, Dropdown, List, Tag, Empty, Spin } from 'antd';
import { BellOutlined, CheckOutlined } from '@ant-design/icons';

const SEVERITY_COLORS = {
  warning: '#faad14',
  critical: '#ff4d4f',
  info: '#1890ff',
};

export default function NotificationBell() {
  const [count, setCount] = useState(0);
  const [notifications, setNotifications] = useState([]);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);

  const fetchCount = useCallback(async () => {
    try {
      const resp = await fetch('/api/notifications/count', {
        headers: { 'X-User-Id': store('__SRMC_Data_user') || '' },
      });
      const data = await resp.json();
      setCount(data.count || 0);
    } catch { /* ignore */ }
  }, []);

  const fetchList = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await fetch('/api/notifications?status=unread&limit=10', {
        headers: { 'X-User-Id': store('__SRMC_Data_user') || '' },
      });
      const data = await resp.json();
      setNotifications(data.items || []);
    } catch { /* ignore */ }
    setLoading(false);
  }, []);

  useEffect(() => {
    fetchCount();
    const interval = setInterval(fetchCount, 30000);
    return () => clearInterval(interval);
  }, [fetchCount]);

  const handleMarkRead = async (id, e) => {
    e.stopPropagation();
    try {
      await fetch(`/api/notifications/${id}/read`, {
        method: 'PUT',
        headers: { 'X-User-Id': store('__SRMC_Data_user') || '' },
      });
      setNotifications(prev => prev.filter(n => n.id !== id));
      setCount(prev => Math.max(0, prev - 1));
    } catch { /* ignore */ }
  };

  const handleMarkAllRead = async () => {
    try {
      await fetch('/api/notifications/read-all', {
        method: 'PUT',
        headers: { 'X-User-Id': store('__SRMC_Data_user') || '' },
      });
      setNotifications([]);
      setCount(0);
    } catch { /* ignore */ }
  };

  // 构建下拉菜单项
  const headerItem = {
    key: 'header',
    label: (
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '4px 0' }}>
        <span style={{ fontWeight: 600, fontSize: 14 }}>通知中心</span>
        {count > 0 && (
          <Button type="link" size="small" onClick={handleMarkAllRead} style={{ fontSize: 12 }}>
            <CheckOutlined /> 全部已读
          </Button>
        )}
      </div>
    ),
    disabled: true,
  };

  let contentItems;
  if (loading) {
    contentItems = [{
      key: 'loading',
      label: <div style={{ textAlign: 'center', padding: 20 }}><Spin size="small" /></div>,
      disabled: true,
    }];
  } else if (notifications.length === 0) {
    contentItems = [{
      key: 'empty',
      label: <Empty description="暂无通知" image={Empty.PRESENTED_IMAGE_SIMPLE} style={{ padding: 12 }} />,
      disabled: true,
    }];
  } else {
    contentItems = notifications.map((n) => ({
      key: n.id,
      label: (
        <div style={{ maxWidth: 320, padding: '4px 0' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
            <Tag color={SEVERITY_COLORS[n.severity] || '#1890ff'} style={{ fontSize: 10, lineHeight: '18px', margin: 0 }}>
              {n.severity === 'critical' ? '严重' : n.severity === 'warning' ? '警告' : '信息'}
            </Tag>
            <span style={{ fontSize: 13, fontWeight: 500, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {n.title}
            </span>
            <Button
              type="text"
              size="small"
              icon={<CheckOutlined style={{ fontSize: 10, color: '#999' }} />}
              onClick={(e) => handleMarkRead(n.id, e)}
              title="标记已读"
            />
          </div>
          <div style={{ fontSize: 12, color: '#666', lineHeight: '18px', wordBreak: 'break-word' }}>
            {n.body}
          </div>
          <div style={{ fontSize: 11, color: '#999', marginTop: 4 }}>
            {formatTime(n.created_at)}
          </div>
        </div>
      ),
    }));
  }

  const items = [headerItem, ...contentItems];

  return (
    <Dropdown
      menu={{ items }}
      trigger={['click']}
      open={open}
      onOpenChange={(visible) => { setOpen(visible); if (visible) fetchList(); }}
      overlayStyle={{ maxHeight: 500, overflow: 'auto' }}
    >
      <Badge count={count} size="small" offset={[-2, 2]}>
        <Button
          type="text"
          icon={<BellOutlined style={{ fontSize: 16 }} />}
          style={{ color: '#555' }}
        />
      </Badge>
    </Dropdown>
  );
}

function formatTime(isoStr) {
  if (!isoStr) return '';
  try {
    const d = new Date(isoStr);
    const now = new Date();
    const diff = now - d;
    if (diff < 60000) return '刚刚';
    if (diff < 3600000) return `${Math.floor(diff / 60000)} 分钟前`;
    if (diff < 86400000) return `${Math.floor(diff / 3600000)} 小时前`;
    return d.toLocaleDateString('zh-CN');
  } catch {
    return isoStr;
  }
}

function store(key) {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return null;
    return JSON.parse(raw);
  } catch {
    return localStorage.getItem(key);
  }
}
