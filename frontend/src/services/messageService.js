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
 * @param {boolean} [data.use_agent] - 是否使用Agent模式
 * @param {boolean} [data.web_search] - 是否启用联网搜索
 * @param {boolean} [data.enable_memory] - 是否启用长期记忆
 * @param {Function} onChunk - 接收到数据块的回调 (type, content) => void
 * @param {AbortSignal} [signal] - 取消信号
 */
export async function sendMessageStream(data, onChunk, signal) {
  try {
    const response = await fetch(`${API_BASE_URL}${apiEndpoints.messages.stream}`, {
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

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      const chunk = decoder.decode(value);
      const lines = chunk.split('\n');

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          const data = line.slice(6);

          if (data === '[DONE]') {
            return;
          }

          try {
            const parsed = JSON.parse(data);
            // agent_info events don't have a content field — pass the full parsed object
            if (parsed.type === 'agent_info') {
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
  } catch (error) {
    if (error.name === 'AbortError') {
      console.log('Message stream aborted');
    } else {
      console.error('Failed to send message:', error);
      throw error;
    }
  }
}
