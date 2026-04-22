import { useCallback } from 'react';
import { useConversationStore } from '../stores/ConversationContext';
import * as conversationService from '../services/conversationService';

/**
 * 会话管理Hook
 * 封装会话的CRUD操作和切换逻辑
 */
export function useConversation() {
  const {
    state,
    setConversations,
    setCurrentConversation,
    addConversation,
    updateConversation,
    deleteConversation,
    setMessages,
    clearMessages,
    setDraft,
    clearDraft,
    setLoading,
    setPagination
  } = useConversationStore();

  // 创建新会话
  const createConversation = useCallback(async (title = null) => {
    try {
      setLoading({ conversations: true });
      const conversation = await conversationService.create({ title });
      addConversation(conversation);
      setCurrentConversation(conversation);
      clearMessages();
      return conversation;
    } catch (error) {
      console.error('Failed to create conversation:', error);
      throw error;
    } finally {
      setLoading({ conversations: false });
    }
  }, [addConversation, setCurrentConversation, clearMessages, setLoading]);

  // 获取会话列表
  // silent=true时不触发loading状态，用于静默更新标题等场景
  const fetchConversations = useCallback(async (page = 1, pageSize = 20, search = null, silent = false) => {
    try {
      if (!silent) setLoading({ conversations: true });
      const response = await conversationService.getList({
        page,
        page_size: pageSize,
        search
      });
      setConversations(response.conversations);
      setPagination({
        page: response.page,
        pageSize: response.page_size,
        total: response.total
      });
    } catch (error) {
      console.error('Failed to fetch conversations:', error);
      throw error;
    } finally {
      if (!silent) setLoading({ conversations: false });
    }
  }, [setConversations, setPagination, setLoading]);

  // 切换会话
  const switchConversation = useCallback(async (conversationId) => {
    try {
      setLoading({ messages: true });

      // 获取会话详情
      const conversation = await conversationService.getById(conversationId);
      setCurrentConversation(conversation);

      // 获取会话消息
      const response = await conversationService.getMessages(conversationId);
      
      // 转换消息格式为前端格式
      const formattedMessages = response.messages.map(msg => ({
        id: msg.id,
        content: msg.content,
        role: msg.role === 'user' ? 'user' : 'agent',
        timestamp: new Date(msg.created_at),
        ...(msg.metadata || {})
      }));
      
      setMessages(formattedMessages);
    } catch (error) {
      console.error('Failed to switch conversation:', error);
      throw error;
    } finally {
      setLoading({ messages: false });
    }
  }, [setCurrentConversation, setMessages, setLoading]);

  // 更新会话标题
  const updateConversationTitle = useCallback(async (conversationId, title) => {
    try {
      const conversation = await conversationService.update(conversationId, { title });
      updateConversation(conversation);
    } catch (error) {
      console.error('Failed to update conversation title:', error);
      throw error;
    }
  }, [updateConversation]);

  // 删除会话
  const deleteConversationById = useCallback(async (conversationId) => {
    try {
      await conversationService.deleteConversation(conversationId);
      deleteConversation(conversationId);

      // 如果删除的是当前会话,清空消息
      if (state.currentConversation?.id === conversationId) {
        setCurrentConversation(null);
        clearMessages();
      }
    } catch (error) {
      console.error('Failed to delete conversation:', error);
      throw error;
    }
  }, [deleteConversation, state.currentConversation, setCurrentConversation, clearMessages]);

  // 保存草稿
  const saveDraft = useCallback((conversationId, content) => {
    setDraft(conversationId, content);
  }, [setDraft]);

  // 获取草稿
  const getDraft = useCallback((conversationId) => {
    return state.drafts[conversationId] || '';
  }, [state.drafts]);

  // 清除草稿
  const clearConversationDraft = useCallback((conversationId) => {
    clearDraft(conversationId);
  }, [clearDraft]);

  return {
    // 状态
    conversations: state.conversations,
    currentConversation: state.currentConversation,
    loading: state.loading,
    pagination: state.pagination,

    // 操作
    createConversation,
    fetchConversations,
    switchConversation,
    updateConversationTitle,
    deleteConversationById,
    saveDraft,
    getDraft,
    clearConversationDraft
  };
}
