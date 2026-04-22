import api, { API_ENDPOINTS } from './api';

/**
 * 会话API服务
 */

// 创建会话
export async function create(data) {
  return await api.post(API_ENDPOINTS.CONVERSATIONS.LIST, data);
}

// 获取会话列表
export async function getList(params = {}) {
  return await api.get(API_ENDPOINTS.CONVERSATIONS.LIST, { params });
}

// 获取会话详情
export async function getById(conversationId) {
  return await api.get(API_ENDPOINTS.CONVERSATIONS.DETAIL(conversationId));
}

// 更新会话
export async function update(conversationId, data) {
  return await api.put(API_ENDPOINTS.CONVERSATIONS.DETAIL(conversationId), data);
}

// 删除会话
export async function deleteConversation(conversationId) {
  return await api.delete(API_ENDPOINTS.CONVERSATIONS.DETAIL(conversationId));
}

// 获取会话消息
export async function getMessages(conversationId, params = {}) {
  return await api.get(API_ENDPOINTS.CONVERSATIONS.MESSAGES(conversationId), { params });
}
