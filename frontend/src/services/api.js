import request from './request';

// API基础地址
export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api';

// API 端点统一管理
export const API_ENDPOINTS = {
  // Chat 相关
  CHAT: {
    MODELS: '/chat/models',
    SEND: '/chat',
    STREAM: '/chat/stream',
    HISTORY: (sessionId) => `/chat/history/${sessionId}`,
    SESSION: (sessionId) => `/chat/session/${sessionId}`,
  },
  
  // 会话相关
  CONVERSATIONS: {
    LIST: '/conversations',
    DETAIL: (id) => `/conversations/${id}`,
    MESSAGES: (id) => `/conversations/${id}/messages`,
  },
  
  // 消息相关
  MESSAGES: {
    STREAM: '/messages/stream',
  },
  
  // 记忆相关
  MEMORY: {
    RETRIEVE: '/memory/retrieve',
    CONFIG: '/memory/config',
    CONVERSATION: (id) => `/memory/conversation/${id}`,
  },
  
  // 健康检查
  HEALTH: '/health',
};

// 导出request实例
export default request;
