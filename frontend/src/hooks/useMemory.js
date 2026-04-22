import { useCallback } from 'react';
import { useConversationStore } from '../stores/ConversationContext';
import * as memoryService from '../services/memoryService';

/**
 * 记忆管理Hook
 * 封装记忆配置和检索操作
 */
export function useMemory() {
  const {
    state,
    setMemoryConfig
  } = useConversationStore();

  // 获取记忆配置
  const fetchMemoryConfig = useCallback(async () => {
    try {
      const config = await memoryService.getConfig();
      setMemoryConfig(config);
      return config;
    } catch (error) {
      console.error('Failed to fetch memory config:', error);
      throw error;
    }
  }, [setMemoryConfig]);

  // 更新记忆配置
  const updateMemoryConfig = useCallback(async (config) => {
    try {
      const updatedConfig = await memoryService.updateConfig(config);
      setMemoryConfig(updatedConfig);
      return updatedConfig;
    } catch (error) {
      console.error('Failed to update memory config:', error);
      throw error;
    }
  }, [setMemoryConfig]);

  // 检索记忆
  const retrieveMemory = useCallback(async (query, conversationId = null, options = {}) => {
    try {
      const response = await memoryService.retrieve({
        query,
        conversation_id: conversationId,
        top_k: options.topK || state.memoryConfig.top_k,
        similarity_threshold: options.similarityThreshold || state.memoryConfig.similarity_threshold
      });
      return response.memories;
    } catch (error) {
      console.error('Failed to retrieve memory:', error);
      throw error;
    }
  }, [state.memoryConfig]);

  // 删除会话记忆
  const deleteConversationMemory = useCallback(async (conversationId) => {
    try {
      await memoryService.deleteByConversation(conversationId);
    } catch (error) {
      console.error('Failed to delete conversation memory:', error);
      throw error;
    }
  }, []);

  return {
    // 状态
    memoryConfig: state.memoryConfig,

    // 操作
    fetchMemoryConfig,
    updateMemoryConfig,
    retrieveMemory,
    deleteConversationMemory
  };
}
