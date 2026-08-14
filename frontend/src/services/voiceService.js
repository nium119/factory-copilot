// 语音识别服务 — 文件转写 + 实时流式转写
import { authFetch } from '../utils/authFetch';

const API_BASE = import.meta.env.VITE_API_BASE_URL || '/api';

/**
 * 上传录音音频，返回识别文字（文件模式，流式失败时的兜底）。
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

/**
 * 创建实时语音识别流（WebSocket）。
 * 录音过程边录边推 PCM，后端实时回传 partial/final。
 *
 * @param {Object} handlers
 * @param {(text: string) => void} handlers.onPartial 中间结果（实时字幕）
 * @param {(text: string) => void} handlers.onFinal   定稿结果
 * @returns {{ connect: () => Promise<void>, sendAudio: (bytes: ArrayBuffer) => void,
 *            end: () => Promise<void>, abort: () => void }}
 */
export function createVoiceStream({ onPartial, onFinal }) {
  const wsProtocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsUrl = `${wsProtocol}//${location.host}/api/voice/stream`;

  let ws = null;
  let settled = false;
  let resolveDone;
  let rejectDone;
  const done = new Promise((res, rej) => { resolveDone = res; rejectDone = rej; });

  const connect = () => new Promise((resolve, reject) => {
    ws = new WebSocket(wsUrl);
    ws.binaryType = 'arraybuffer';
    ws.onopen = () => resolve();
    ws.onerror = () => reject(new Error('语音流连接失败'));
    ws.onmessage = (e) => {
      let msg;
      try { msg = JSON.parse(e.data); } catch { return; }
      if (msg.type === 'partial' && onPartial) onPartial(msg.text || '');
      else if (msg.type === 'final' && onFinal) onFinal(msg.text || '');
      else if (msg.type === 'complete') { settled = true; resolveDone(); }
      else if (msg.type === 'error') { settled = true; rejectDone(new Error(msg.message || '语音识别失败')); }
    };
    ws.onclose = () => {
      if (!settled) { settled = true; resolveDone(); }
    };
  });

  const sendAudio = (bytes) => {
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(bytes);
  };

  const end = () => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'end' }));
    } else {
      settled = true;
      resolveDone();
    }
    return done;
  };

  const abort = () => {
    settled = true;
    if (ws) { try { ws.close(); } catch { /* ignore */ } ws = null; }
    rejectDone(new Error('语音识别已取消'));
  };

  return { connect, sendAudio, end, abort };
}
