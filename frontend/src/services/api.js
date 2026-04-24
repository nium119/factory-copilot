import request from './request';

// API基础地址
export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api';

// API 端点统一管理
export const apiEndpoints = {
  // Chat 相关（流式消息统一走 /messages/stream）
  chat: {
    models: '/chat/models',
    session: (sessionId) => `/chat/session/${sessionId}`,
  },

  // 会话相关
  conversations: {
    list: '/conversations',
    detail: (id) => `/conversations/${id}`,
    messages: (id) => `/conversations/${id}/messages`,
  },

  // 消息相关
  messages: {
    stream: '/messages/stream',
    agents: '/messages/agents',
  },

  // 记忆相关
  memory: {
    retrieve: '/memory/retrieve',
    config: '/memory/config',
    conversation: (id) => `/memory/conversation/${id}`,
  },

  // 健康检查
  health: '/health',
};

// 导出request实例
export default request;
