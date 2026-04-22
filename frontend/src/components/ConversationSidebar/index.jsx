import React, { useState, useEffect } from 'react';
import { Button, Input, List, Spin, Empty, Menu, Dropdown, Modal, message, Checkbox } from 'antd';
import { PlusOutlined, SearchOutlined, MoreOutlined, DeleteOutlined, EditOutlined, CheckCircleOutlined, ThunderboltOutlined } from '@ant-design/icons';
import { useConversation } from '../../hooks/useConversation';
import ConversationItem from './ConversationItem';
import SearchBar from './SearchBar';
import './index.css';

/**
 * 会话侧边栏组件
 */
export default function ConversationSidebar() {
  const {
    conversations,
    currentConversation,
    loading,
    createConversation,
    fetchConversations,
    switchConversation,
    updateConversationTitle,
    deleteConversationById
  } = useConversation();

  const [searchText, setSearchText] = useState('');
  const [editModalVisible, setEditModalVisible] = useState(false);
  const [editingConversation, setEditingConversation] = useState(null);
  const [newTitle, setNewTitle] = useState('');
  const [selectionMode, setSelectionMode] = useState(false); // 批量选择模式
  const [selectedIds, setSelectedIds] = useState([]); // 选中的会话ID列表

  // 初始化加载会话列表
  useEffect(() => {
    const initConversations = async () => {
      await fetchConversations();
    };
    initConversations();
  }, [fetchConversations]);

  // 自动选择第一个对话
  useEffect(() => {
    if (conversations.length > 0 && !currentConversation) {
      switchConversation(conversations[0].id);
    }
  }, [conversations, currentConversation, switchConversation]);

  // 创建新会话
  const handleCreate = async () => {
    try {
      await createConversation('新对话');
      message.success('创建成功');
    } catch (error) {
      message.error('创建失败');
    }
  };

  // 搜索会话
  const handleSearch = (value) => {
    setSearchText(value);
    fetchConversations(1, 20, value);
  };

  // 切换会话
  const handleSwitch = (conversationId) => {
    if (currentConversation?.id !== conversationId) {
      switchConversation(conversationId);
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
    Modal.confirm({
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

    Modal.confirm({
      title: '确认批量删除',
      content: `确定要删除选中的 ${selectedIds.length} 个对话吗?`,
      okText: '删除',
      okType: 'danger',
      cancelText: '取消',
      onOk: async () => {
        try {
          // 并行删除所有选中的会话
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
    <div className="conversation-sidebar">
      {/* 品牌标题区域 */}
      <div className="sidebar-brand">
        <ThunderboltOutlined style={{ fontSize: '20px', color: '#6c5ce7' }} />
        <div style={{ fontSize: '16px', fontWeight: 600, color: '#1a1a2e', letterSpacing: '0.5px' }}>
          璟岩Agent助手
        </div>
        <div className="sidebar-brand-version">v2.0</div>
      </div>
      {/* 顶部操作栏 */}
      <div className="sidebar-header">
        {selectionMode ? (
          <div style={{ display: 'flex', gap: '8px' }}>
            <Button
              icon={<CheckCircleOutlined />}
              onClick={toggleSelectAll}
              style={{ flex: 1 }}
            >
              {selectedIds.length === filteredConversations.length ? '取消全选' : '全选'}
            </Button>
            <Button
              type="primary"
              danger
              icon={<DeleteOutlined />}
              onClick={handleBatchDelete}
              disabled={selectedIds.length === 0}
            >
              删除 ({selectedIds.length})
            </Button>
            <Button onClick={toggleSelectionMode}>取消</Button>
          </div>
        ) : (
          <div style={{ display: 'flex', gap: '8px' }}>
            <Button
              type="primary"
              icon={<PlusOutlined />}
              style={{ flex: 1 }}
              onClick={handleCreate}
            >
              新建对话
            </Button>
            <Button
              icon={<CheckCircleOutlined />}
              onClick={toggleSelectionMode}
              title="批量管理"
            />
          </div>
        )}
      </div>

      {/* 搜索栏 */}
      <div className="sidebar-search">
        <SearchBar onSearch={handleSearch} />
      </div>

      {/* 会话列表 */}
      <div className="sidebar-content">
        {loading.conversations ? (
          <div className="loading-container">
            <Spin />
          </div>
        ) : filteredConversations.length === 0 ? (
          <Empty description="暂无对话" />
        ) : (
          <List
            dataSource={filteredConversations}
            renderItem={(conversation) => (
              <div
                className={`conversation-item-wrapper ${selectedIds.includes(conversation.id) ? 'selected' : ''}`}
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
                    checked={selectedIds.includes(conversation.id)}
                    onChange={() => toggleSelection(conversation.id)}
                    onClick={(e) => e.stopPropagation()}
                    style={{ marginRight: '8px' }}
                  />
                )}
                <ConversationItem
                  conversation={conversation}
                  isActive={currentConversation?.id === conversation.id}
                  onClick={() => {}}
                  onEdit={() => showEditModal(conversation)}
                  onDelete={() => handleDelete(conversation)}
                />
              </div>
            )}
          />
        )}
      </div>

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
        />
      </Modal>
    </div>
  );
}
