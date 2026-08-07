import store from 'store2';
import api, { apiEndpoints, API_BASE_URL } from './api';
import { authFetch } from '../utils/authFetch';

/**
 * 消息API服务
 */

/**
 * 获取可用 Agent 列表
 */
export async function getAgents() {
  try {
    const response = await api.get(apiEndpoints.messages.agents);
    // api interceptor returns response.data, which should be an array
    // but handle case where it might be wrapped
    if (Array.isArray(response)) return response;
    if (response && Array.isArray(response.data)) return response.data;
    return [];
  } catch (error) {
    console.error('获取 Agent 列表失败:', error);
    return [];
  }
}

/**
 * 流式发送消息
 * @param {Object} data - 消息数据
 * @param {string} data.conversation_id - 会话ID
 * @param {string} data.content - 消息内容
 * @param {string} [data.model_name] - 模型名称
 * @param {string} [data.agent_name] - Agent名称
 * @param {boolean} [data.use_agent] - 是否启用协作模式（多 Agent 并发查询）
 * @param {boolean} [data.web_search] - 是否启用联网搜索
 * @param {boolean} [data.enable_memory] - 是否启用长期记忆
 * @param {Function} onChunk - 接收到数据块的回调 (type, content) => void
 * @param {AbortSignal} [signal] - 取消信号
 */
export async function sendMessageStream(data, onChunk, signal) {
  try {
    const response = await authFetch(`${API_BASE_URL}${apiEndpoints.messages.stream}`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        conversation_id: data.conversation_id,
        content: data.content,
        model_name: data.model_name,
        agent_name: data.agent_name,
        use_agent: data.use_agent,
        web_search: data.web_search,
        enable_memory: data.enable_memory,
        enable_thinking: data.enable_thinking,
      }),
      signal
    });

    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      // 最后一段可能是不完整的行，保留到下次读取
      buffer = lines.pop() || '';

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          const data = line.slice(6);

          if (data === '[DONE]') {
            continue;
          }

          try {
            const parsed = JSON.parse(data);
            if (parsed.type === 'agent_info' || parsed.type === 'data_source') {
              onChunk(parsed.type, parsed);
            } else {
              onChunk(parsed.type, parsed.content);
            }
          } catch (e) {
            console.error('Failed to parse SSE data:', e, 'raw:', line);
          }
        }
      }
    }

    // 处理流结束后 buffer 中残留的最后一行
    if (buffer.startsWith('data: ') && buffer !== 'data: [DONE]') {
      const finalData = buffer.slice(6);
      try {
        const parsed = JSON.parse(finalData);
        if (parsed.type === 'agent_info' || parsed.type === 'data_source') {
          onChunk(parsed.type, parsed);
        } else {
          onChunk(parsed.type, parsed.content);
        }
      } catch (e) {
        console.error('Failed to parse final SSE data:', e, 'raw:', buffer);
      }
    }
  } catch (error) {
    if (error.name === 'AbortError') {
      console.log('Message stream aborted');
    } else {
      console.error('Failed to send message:', error);
      throw error;
    }
  }
}

/**
 * 获取待审批消息列表
 */
export async function getPendingConfirmations(userId, userRoles, page = 1, pageSize = 20) {
  const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api';
  const params = new URLSearchParams();
  if (userId) params.append('user_id', userId);
  if (userRoles) params.append('user_roles', userRoles);
  params.append('page', page);
  params.append('page_size', pageSize);
  const resp = await authFetch(`${API_BASE_URL}/messages/pending?${params.toString()}`);
  if (!resp.ok) throw new Error(`获取待审批列表失败: ${resp.status}`);
  return resp.json();
}

export async function getProcessedConfirmations(page = 1, pageSize = 20) {
  const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api';
  const params = new URLSearchParams();
  params.append('page', page);
  params.append('page_size', pageSize);
  const resp = await authFetch(`${API_BASE_URL}/messages/processed?${params.toString()}`);
  if (!resp.ok) throw new Error(`获取已处理列表失败: ${resp.status}`);
  return resp.json();
}

/**
 * 通过审批
 */
export async function approveConfirmation(messageId, userId, comment) {
  const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api';
  const resp = await authFetch(`${API_BASE_URL}/messages/${messageId}/approve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_id: userId, comment: comment || '' }),
  });
  if (!resp.ok) throw new Error(`审批失败: ${resp.status}`);
  return resp.json();
}

/**
 * 拒绝审批
 */
export async function rejectConfirmation(messageId, userId, reason) {
  const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api';
  const resp = await authFetch(`${API_BASE_URL}/messages/${messageId}/reject`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_id: userId, comment: reason || '' }),
  });
  if (!resp.ok) throw new Error(`拒绝失败: ${resp.status}`);
  return resp.json();
}

/**
 * 批量通过审批
 */
export async function batchApproveConfirmations(messageIds, userId) {
  const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api';
  const resp = await authFetch(`${API_BASE_URL}/messages/batch-approve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message_ids: messageIds, user_id: userId || '', comment: '' }),
  });
  if (!resp.ok) throw new Error(`批量审批失败: ${resp.status}`);
  return resp.json();
}

/**
 * 批量拒绝审批
 */
export async function batchRejectConfirmations(messageIds, userId, reason) {
  const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api';
  const resp = await authFetch(`${API_BASE_URL}/messages/batch-reject`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message_ids: messageIds, user_id: userId || '', comment: reason || '' }),
  });
  if (!resp.ok) throw new Error(`批量拒绝失败: ${resp.status}`);
  return resp.json();
}

/**
 * 复核：确认接受（验证失败结果可接受，不重新执行）
 */
export async function acceptReview(messageId, userId, comment) {
  const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api';
  const resp = await authFetch(`${API_BASE_URL}/messages/${messageId}/review-accept`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_id: userId, comment: comment || '' }),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.detail || `复核失败: ${resp.status}`);
  }
  return resp.json();
}

/**
 * 复核：触发回滚（撤销变更）
 */
export async function rollbackReview(messageId, userId, reason) {
  const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api';
  const resp = await authFetch(`${API_BASE_URL}/messages/${messageId}/review-rollback`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_id: userId, comment: reason || '' }),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.detail || `回滚失败: ${resp.status}`);
  }
  return resp.json();
}

/**
 * 批量删除消息
 */
export async function batchDeleteMessages(messageIds) {
  const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api';
  const resp = await authFetch(`${API_BASE_URL}/messages/batch`, {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message_ids: messageIds }),
  });
  if (!resp.ok) throw new Error(`批量删除失败: ${resp.status}`);
  return resp.json();
}
