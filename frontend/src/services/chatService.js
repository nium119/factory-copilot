import api, { apiEndpoints } from './api';

/**
 * ChatService — 仅保留模型列表和会话清除
 * 流式消息统一走 messageService.sendMessageStream()
 */
class ChatService {
  async getModels() {
    try {
      const response = await api.get(apiEndpoints.chat.models);
      return response;
    } catch (error) {
      throw error;
    }
  }

  async clearSession(sessionId) {
    try {
      const response = await api.delete(apiEndpoints.chat.session(sessionId));
      return response;
    } catch (error) {
      throw error;
    }
  }
}

export default new ChatService();
