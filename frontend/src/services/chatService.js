import api, { API_ENDPOINTS, API_BASE_URL } from './api';

class ChatService {
  /**
   * 获取可用模型列表
   */
  async getModels() {
    try {
      const response = await api.get(API_ENDPOINTS.CHAT.MODELS);
      return response;
    } catch (error) {
      throw error;
    }
  }

  /**
   * 发送聊天消息
   */
  async sendMessage(content, sessionId = 'default') {
    try {
      const response = await api.post(API_ENDPOINTS.CHAT.SEND, {
        content,
        session_id: sessionId,
      });
      return response;
    } catch (error) {
      throw error;
    }
  }

  /**
   * 流式发送聊天消息
   */
  async sendMessageStream(content, sessionId = 'default', onMessage, signal, model = null, useAgent = false, webSearch = false) {
    try {
      // 使用完整的API地址
      const response = await fetch(`${API_BASE_URL}${API_ENDPOINTS.CHAT.STREAM}`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          content,
          session_id: sessionId,
          model_name: model,
          use_agent: useAgent,
          web_search: webSearch,
        }),
        signal, // 添加AbortSignal
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
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const data = JSON.parse(line.slice(6));
              onMessage(data);
            } catch (e) {
              console.error('Parse error:', e);
            }
          }
        }
      }
    } catch (error) {
      console.error('Stream error:', error);
      throw error;
    }
  }

  /**
   * 获取会话历史
   */
  async getSessionHistory(sessionId) {
    try {
      const response = await api.get(API_ENDPOINTS.CHAT.HISTORY(sessionId));
      return response;
    } catch (error) {
      throw error;
    }
  }

  /**
   * 清除会话
   */
  async clearSession(sessionId) {
    try {
      const response = await api.delete(API_ENDPOINTS.CHAT.SESSION(sessionId));
      return response;
    } catch (error) {
      throw error;
    }
  }

  /**
   * 健康检查
   */
  async healthCheck() {
    try {
      const response = await api.get(API_ENDPOINTS.HEALTH);
      return response;
    } catch (error) {
      throw error;
    }
  }
}

export default new ChatService();
