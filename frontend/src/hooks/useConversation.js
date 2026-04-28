import { useCallback } from 'react';
import { useConversationStore, getPersistedConversationId } from '../stores/ConversationContext';
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

  // 切换会话（仅设置当前会话，消息加载由 ChatInterface.loadHistory 统一处理）
  const switchConversation = useCallback(async (conversationId) => {
    try {
      setLoading({ messages: true });
      const conversation = await conversationService.getById(conversationId);
      setCurrentConversation(conversation);
    } catch (error) {
      console.error('Failed to switch conversation:', error);
      throw error;
    } finally {
      setLoading({ messages: false });
    }
  }, [setCurrentConversation, setLoading]);

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

  // 恢复持久化会话（页面刷新后恢复上次对话，消息加载由 ChatInterface.loadHistory 统一处理）
  const restoreConversation = useCallback(async () => {
    const savedId = getPersistedConversationId();
    if (!savedId) return null;

    try {
      setLoading({ messages: true });
      const conversation = await conversationService.getById(savedId);
      if (!conversation) return null;
      setCurrentConversation(conversation);
      return conversation;
    } catch (error) {
      console.error('恢复会话失败:', error);
      return null;
    } finally {
      setLoading({ messages: false });
    }
  }, [setCurrentConversation, setLoading]);

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
    restoreConversation,
    updateConversationTitle,
    deleteConversationById,
    saveDraft,
    getDraft,
    clearConversationDraft
  };
}
