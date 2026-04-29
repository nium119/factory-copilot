import React, { useState, useEffect } from 'react';
import { Badge, Button, Input, List, Space, Spin, Empty, Checkbox, Drawer, Divider, Tag, Modal, App, Dropdown, Pagination } from 'antd';
import { DeleteOutlined, CheckCircleOutlined, ClockCircleOutlined, MoreOutlined, EditOutlined } from '@ant-design/icons';
import { useConversation } from '../../hooks/useConversation';
import SearchBar from '../ConversationSidebar/SearchBar';

/**
 * 历史记录抽屉组件
 * 右侧滑出显示会话列表
 */
export default function ConversationDrawer({ open, onClose }) {
  const { message, modal } = App.useApp();
  const {
    conversations,
    currentConversation,
    loading,
    pagination,
    fetchConversations,
    switchConversation,
    updateConversationTitle,
    deleteConversationById
  } = useConversation();

  const [searchText, setSearchText] = useState('');
  const [editModalVisible, setEditModalVisible] = useState(false);
  const [editingConversation, setEditingConversation] = useState(null);
  const [newTitle, setNewTitle] = useState('');
  const [selectionMode, setSelectionMode] = useState(false);
  const [selectedIds, setSelectedIds] = useState([]);

  // 打开抽屉时刷新会话列表
  useEffect(() => {
    if (open) {
      fetchConversations();
    }
  }, [open]);

  // 搜索会话
  const handleSearch = (value) => {
    setSearchText(value);
    fetchConversations(1, 20, value);
  };

  // 分页切换
  const handlePageChange = (page, pageSize) => {
    fetchConversations(page, pageSize, searchText);
  };

  // 切换会话
  const handleSwitch = (conversationId) => {
    if (currentConversation?.id !== conversationId) {
      switchConversation(conversationId);
      onClose?.();
    }
  };

  // 显示编辑对话框
  const showEditModal = (conversation) => {
    setEditingConversation(conversation);
    setNewTitle(conversation.title || '');
    setEditModalVisible(true);
  };

  // 更新标题
  const handleUpdateTitle = async () => {
    if (!newTitle.trim()) {
      message.warning('标题不能为空');
      return;
    }
    try {
      await updateConversationTitle(editingConversation.id, newTitle);
      setEditModalVisible(false);
      message.success('更新成功');
    } catch (error) {
      message.error('更新失败');
    }
  };

  // 删除会话
  const handleDelete = (conversation) => {
    modal.confirm({
      title: '确认删除',
      content: `确定要删除对话"${conversation.title || '未命名'}"吗?`,
      okText: '删除',
      okType: 'danger',
      cancelText: '取消',
      onOk: async () => {
        try {
          await deleteConversationById(conversation.id);
          message.success('删除成功');
        } catch (error) {
          message.error('删除失败');
        }
      }
    });
  };

  // 切换选择模式
  const toggleSelectionMode = () => {
    setSelectionMode(!selectionMode);
    setSelectedIds([]);
  };

  // 切换选中状态
  const toggleSelection = (conversationId) => {
    setSelectedIds(prev => {
      if (prev.includes(conversationId)) {
        return prev.filter(id => id !== conversationId);
      } else {
        return [...prev, conversationId];
      }
    });
  };

  // 全选/取消全选
  const toggleSelectAll = () => {
    if (selectedIds.length === filteredConversations.length) {
      setSelectedIds([]);
    } else {
      setSelectedIds(filteredConversations.map(conv => conv.id));
    }
  };

  // 批量删除
  const handleBatchDelete = () => {
    if (selectedIds.length === 0) {
      message.warning('请先选择要删除的对话');
      return;
    }
    modal.confirm({
      title: '确认批量删除',
      content: `确定要删除选中的 ${selectedIds.length} 个对话吗?`,
      okText: '删除',
      okType: 'danger',
      cancelText: '取消',
      onOk: async () => {
        try {
          await Promise.all(selectedIds.map(id => deleteConversationById(id)));
          message.success(`成功删除 ${selectedIds.length} 个对话`);
          setSelectedIds([]);
          setSelectionMode(false);
        } catch (error) {
          message.error('批量删除失败');
        }
      }
    });
  };

  // 过滤会话列表
  const filteredConversations = searchText
    ? conversations.filter(conv =>
        conv.title?.toLowerCase().includes(searchText.toLowerCase())
      )
    : conversations;

  return (
    <>
      <Drawer
        title={
          <Space align="center">
            <ClockCircleOutlined />
            <span>历史记录</span>
            <Badge count={filteredConversations.length} style={{ backgroundColor: '#52c41a' }} />
          </Space>
        }
        placement="right"
        open={open}
        onClose={() => {
          setSelectionMode(false);
          setSelectedIds([]);
          onClose?.();
        }}
        width={400}
        extra={
          selectionMode ? (
            <Button size="small" onClick={toggleSelectionMode}>完成</Button>
          ) : (
            <Button type="text" size="small" icon={<CheckCircleOutlined />} onClick={toggleSelectionMode} />
          )
        }
      >
        {/* 批量操作区域 */}
        {selectionMode && (
          <>
            <Space style={{ width: '100%', marginBottom: 8 }}>
              <Tag color="blue">{selectedIds.length} 项已选</Tag>
              <Space.Compact style={{ flex: 1 }}>
                <Button icon={<CheckCircleOutlined />} onClick={toggleSelectAll}>
                  {selectedIds.length === filteredConversations.length ? '取消全选' : '全选'}
                </Button>
                <Button danger icon={<DeleteOutlined />} onClick={handleBatchDelete} disabled={selectedIds.length === 0}>
                  删除
                </Button>
              </Space.Compact>
            </Space>
            <Divider style={{ margin: '4px 0 12px' }} />
          </>
        )}

        {/* 搜索栏 */}
        <SearchBar onSearch={handleSearch} />

        <Divider style={{ margin: '12px 0' }} />

        {/* 会话列表 */}
        <div style={{ height: 'calc(100vh - 216px)', overflowY: 'auto', padding: '0 4px' }}>
          {loading.conversations ? (
            <div style={{ textAlign: 'center', padding: '60px 0' }}>
              <Spin size="large" />
            </div>
          ) : filteredConversations.length === 0 ? (
            <Empty description="暂无对话" style={{ marginTop: '40px' }} />
          ) : (
            <List
              dataSource={filteredConversations}
              renderItem={(conversation) => {
                const isActive = currentConversation?.id === conversation.id;
                const isSelected = selectedIds.includes(conversation.id);

                return (
                  <List.Item
                    style={{
                      cursor: 'pointer',
                      padding: '8px 12px',
                      marginBottom: 4,
                      borderRadius: 8,
                      background: isActive ? '#f0eeff' : (isSelected ? '#f5f3ff' : 'transparent'),
                      border: isActive ? '1px solid #d4cfff' : '1px solid transparent',
                      transition: 'all 0.2s',
                      alignItems: 'center',
                    }}
                    onMouseEnter={(e) => {
                      if (!isActive) e.currentTarget.style.background = '#fafaff';
                    }}
                    onMouseLeave={(e) => {
                      if (!isActive && !isSelected) e.currentTarget.style.background = 'transparent';
                    }}
                    onClick={() => {
                      if (selectionMode) {
                        toggleSelection(conversation.id);
                      } else {
                        handleSwitch(conversation.id);
                      }
                    }}
                  >
                    {selectionMode && (
                      <Checkbox
                        checked={isSelected}
                        onChange={() => toggleSelection(conversation.id)}
                        onClick={(e) => e.stopPropagation()}
                        style={{ marginRight: 8 }}
                      />
                    )}
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{
                        fontSize: 14,
                        fontWeight: 500,
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                      }}>
                        {conversation.title || '未命名'}
                      </div>
                      <div style={{ fontSize: 12, color: '#8c8c8c', marginTop: 4 }}>
                        {conversation.created_at ? new Date(conversation.created_at).toLocaleString('zh-CN') : ''}
                      </div>
                    </div>
                    {!selectionMode && (
                      <span onClick={(e) => e.stopPropagation()} style={{ display: 'inline-flex', flexShrink: 0 }}>
                        <Dropdown
                          menu={{
                            items: [
                              { key: 'edit', icon: <EditOutlined />, label: '重命名' },
                              { key: 'delete', icon: <DeleteOutlined />, label: '删除', danger: true },
                            ],
                            onClick: ({ key }) => {
                              if (key === 'edit') showEditModal(conversation);
                              if (key === 'delete') handleDelete(conversation);
                            },
                          }}
                          trigger={['click']}
                          placement="bottomRight"
                        >
                          <Button
                            type="text"
                            size="small"
                            icon={<MoreOutlined />}
                            style={{ color: '#999' }}
                          />
                        </Dropdown>
                      </span>
                    )}
                  </List.Item>
                );
              }}
            />
          )}
        </div>

        {/* 分页器 */}
        {!loading.conversations && (
          <div style={{ textAlign: 'center', marginTop: 8 }}>
            <Pagination
              size="small"
              current={pagination.page}
              pageSize={pagination.pageSize}
              total={pagination.total}
              onChange={handlePageChange}
              showSizeChanger
              locale={{ items_per_page: '条/页', jump_to: '前往', page: '页' }}
            />
          </div>
        )}

      </Drawer>

      {/* 编辑对话框 */}
      <Modal
        title="编辑对话标题"
        open={editModalVisible}
        onOk={handleUpdateTitle}
        onCancel={() => setEditModalVisible(false)}
        okText="确定"
        cancelText="取消"
      >
        <Input
          value={newTitle}
          onChange={(e) => setNewTitle(e.target.value)}
          placeholder="请输入对话标题"
          allowClear
        />
      </Modal>
    </>
  );
}
