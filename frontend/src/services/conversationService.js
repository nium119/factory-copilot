import api, { apiEndpoints } from './api';

/**
 * 会话API服务
 */

// 创建会话
export async function create(data) {
  return await api.post(apiEndpoints.conversations.list, data);
}

// 获取会话列表
export async function getList(params = {}) {
  return await api.get(apiEndpoints.conversations.list, { params });
}

// 获取会话详情
export async function getById(conversationId) {
  return await api.get(apiEndpoints.conversations.detail(conversationId));
}

// 更新会话
export async function update(conversationId, data) {
  return await api.put(apiEndpoints.conversations.detail(conversationId), data);
}

// 删除会话
export async function deleteConversation(conversationId) {
  return await api.delete(apiEndpoints.conversations.detail(conversationId));
}

// 获取会话消息
export async function getMessages(conversationId, params = {}) {
  return await api.get(apiEndpoints.conversations.messages(conversationId), { params });
}
