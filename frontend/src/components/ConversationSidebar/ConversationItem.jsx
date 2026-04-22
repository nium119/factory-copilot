import React from 'react';
import { List, Dropdown, Button, message } from 'antd';
import { MoreOutlined, EditOutlined, DeleteOutlined } from '@ant-design/icons';
import { formatDistanceToNow } from 'date-fns';
import zhCN from 'date-fns/locale/zh-CN';

/**
 * 会话项组件
 */
export default function ConversationItem({
  conversation,
  isActive,
  onClick,
  onEdit,
  onDelete
}) {
  // 右键菜单项
  const menuItems = [
    {
      key: 'edit',
      icon: <EditOutlined />,
      label: '重命名',
      onClick: onEdit
    },
    {
      key: 'delete',
      icon: <DeleteOutlined />,
      label: '删除',
      danger: true,
      onClick: onDelete
    }
  ];

  // 格式化时间
  const formatTime = (timestamp) => {
    try {
      // 后端返回的是 UTC 时间字符串，需要转换为本地时间
      const date = new Date(timestamp);
      // 检查日期是否有效
      if (isNaN(date.getTime())) {
        return '';
      }
      return formatDistanceToNow(date, {
        addSuffix: true,
        locale: zhCN
      });
    } catch (error) {
      console.error('时间格式化错误:', error);
      return '';
    }
  };

  return (
    <List.Item
      className={`conversation-item ${isActive ? 'active' : ''}`}
      onClick={onClick}
    >
      <div className="item-content">
        <div className="item-title">
          {conversation.title || '未命名对话'}
        </div>
        <div className="item-meta">
          <span className="message-count">
            {conversation.message_count || 0} 条消息
          </span>
          <span className="time">
            {formatTime(conversation.updated_at)}
          </span>
        </div>
      </div>

      <Dropdown
        menu={{ items: menuItems }}
        trigger={['click']}
      >
        <Button
          type="text"
          icon={<MoreOutlined />}
          className="item-actions"
          onClick={(e) => e.stopPropagation()}
        />
      </Dropdown>
    </List.Item>
  );
}
