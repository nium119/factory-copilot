import api, { API_BASE_URL, API_ENDPOINTS } from './api';

/**
 * 消息API服务
 */

/**
 * 流式发送消息
 * @param {Object} data - 消息数据
 * @param {string} data.conversation_id - 会话ID
 * @param {string} data.content - 消息内容
 * @param {string} [data.model_name] - 模型名称
 * @param {boolean} [data.use_agent] - 是否使用Agent模式
 * @param {boolean} [data.web_search] - 是否启用联网搜索
 * @param {boolean} [data.enable_memory] - 是否启用长期记忆
 * @param {Function} onChunk - 接收到数据块的回调 (type, content) => void
 * @param {AbortSignal} [signal] - 取消信号
 */
export async function sendMessageStream(data, onChunk, signal) {
  try {
    const response = await fetch(`${API_BASE_URL}${API_ENDPOINTS.MESSAGES.STREAM}`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(data),
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
            onChunk(parsed.type, parsed.content);
          } catch (e) {
            console.error('Failed to parse SSE data:', e);
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
