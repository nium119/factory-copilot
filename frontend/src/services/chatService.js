import api, { apiEndpoints } from './api';

export async function getModels() {
  const response = await api.get(apiEndpoints.chat.models);
  return response;
}

export async function clearSession(sessionId) {
  const response = await api.delete(apiEndpoints.chat.session(sessionId));
  return response;
}
