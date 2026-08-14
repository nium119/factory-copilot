// 语音识别服务 — 录音文件上传后端，调 DashScope Paraformer 转写为文字
import { authFetch } from '../utils/authFetch';

const API_BASE = import.meta.env.VITE_API_BASE_URL || '/api';

/**
 * 上传录音音频，返回识别文字。
 * @param {Blob} blob - 录音音频 blob
 * @param {string} filename - 文件名（带扩展名，决定后端临时文件后缀）
 */
export async function transcribeAudio(blob, filename = 'recording.webm') {
  const form = new FormData();
  form.append('file', blob, filename);
  const resp = await authFetch(`${API_BASE}/voice/transcribe`, {
    method: 'POST',
    body: form,
  });
  if (!resp.ok) {
    let detail = `语音识别失败: ${resp.status}`;
    try {
      const err = await resp.json();
      if (err && err.detail) detail = err.detail;
    } catch { /* ignore */ }
    throw new Error(detail);
  }
  return resp.json();
}
