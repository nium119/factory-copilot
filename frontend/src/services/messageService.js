import store from 'store2';
import api, { apiEndpoints, API_BASE_URL } from './api';

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
    const user = store('__SRMC_Data_user');
    const userId = user?.UserAccount || user?.NowLoginUser || 'default_user';

    const response = await fetch(`${API_BASE_URL}${apiEndpoints.messages.stream}`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-User-Id': userId,
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
export async function getPendingConfirmations(userId, userRoles) {
  const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '';
  const params = new URLSearchParams();
  if (userId) params.append('user_id', userId);
  if (userRoles) params.append('user_roles', userRoles);
  const resp = await fetch(`${API_BASE_URL}/messages/pending?${params.toString()}`);
  if (!resp.ok) throw new Error(`获取待审批列表失败: ${resp.status}`);
  return resp.json();
}

/**
 * 通过审批
 */
export async function approveConfirmation(messageId, userId, comment) {
  const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '';
  const resp = await fetch(`${API_BASE_URL}/messages/${messageId}/approve`, {
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
  const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '';
  const resp = await fetch(`${API_BASE_URL}/messages/${messageId}/reject`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_id: userId, comment: reason || '' }),
  });
  if (!resp.ok) throw new Error(`拒绝失败: ${resp.status}`);
  return resp.json();
}
