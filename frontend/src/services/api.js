import request from './request';

// API基础地址
export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api';

// API 端点统一管理
export const apiEndpoints = {
  // Chat 相关（流式消息统一走 /messages/stream）
  chat: {
    models: '/chat/models',
    session: (sessionId) => `/conversations/${sessionId}`,
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

  // 评估与反馈
  eval: {
    feedback: '/eval/feedback',
    selfEval: '/eval/self',
  },

  // 探索与发现
  explorer: {
    analyze: (hours = 24) => `/explorer/analyze?hours=${hours}`,
  },

  // 链条管理
  chains: {
    list: '/chains',
    detail: (id) => `/chains/${encodeURIComponent(id)}`,
    create: '/chains',
    update: (id) => `/chains/${encodeURIComponent(id)}`,
    delete: (id) => `/chains/${encodeURIComponent(id)}`,
    reload: '/chains/reload',
    agents: '/chains/agents/list',
  },

  // Agent 管理
  agents: {
    list: '/agents',
    detail: (name) => `/agents/${encodeURIComponent(name)}`,
    create: '/agents',
    update: (name) => `/agents/${encodeURIComponent(name)}`,
    delete: (name) => `/agents/${encodeURIComponent(name)}`,
  },

  // MCP 服务器管理
  mcpServers: {
    list: '/mcp/servers',
    create: '/mcp/servers',
    update: (name) => `/mcp/servers/${encodeURIComponent(name)}`,
    delete: (name) => `/mcp/servers/${encodeURIComponent(name)}`,
    connect: (name) => `/mcp/servers/${encodeURIComponent(name)}/connect`,
    disconnect: (name) => `/mcp/servers/${encodeURIComponent(name)}/disconnect`,
  },

  // A2A 外部 Agent 管理
  a2aAgents: {
    list: '/a2a/agents',
    create: '/a2a/agents',
    update: (name) => `/a2a/agents/${encodeURIComponent(name)}`,
    delete: (name) => `/a2a/agents/${encodeURIComponent(name)}`,
  },

  // KPI 阈值管理
  kpiAdmin: {
    list: '/admin/kpis',
    domains: '/admin/kpis/domains',
    create: '/admin/kpis',
    update: (key) => `/admin/kpis/${encodeURIComponent(key)}`,
    delete: (key) => `/admin/kpis/${encodeURIComponent(key)}`,
    reload: '/admin/kpis/reload',
  },

  // 异常检测规则
  explorerRules: {
    list: '/admin/explorer-rules',
    create: '/admin/explorer-rules',
    update: (name) => `/admin/explorer-rules/${encodeURIComponent(name)}`,
    delete: (name) => `/admin/explorer-rules/${encodeURIComponent(name)}`,
    reload: '/admin/explorer-rules/reload',
  },

  // 资源阈值
  resourceThresholds: {
    get: '/admin/resources',
    update: '/admin/resources',
  },

};

// 导出request实例
export default request;
