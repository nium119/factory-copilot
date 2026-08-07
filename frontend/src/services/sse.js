/** 全局 SSE 单例 — 同一 iframe 内所有组件共享一个 EventSource 连接 */
import { getAuthToken } from '../utils/authFetch';

let _es = null;
const _listeners = new Map();

export function getSharedEventSource() {
  if (_es && _es.readyState !== EventSource.CLOSED) return _es;

  // EventSource 无法自定义 Authorization header，统一鉴权后用 query token（后端 events/stream 支持）
  const token = getAuthToken();
  const url = (window.__API_BASE__ || '/api') + '/messages/events/stream'
    + (token ? `?token=${encodeURIComponent(token)}` : '');
  _es = new EventSource(url);

  // 后端发送的是命名事件（event: approval_done / pending_updated / heartbeat 等），
  // onmessage 只处理默认消息，命名事件必须用 addEventListener 才能收到。
  const dispatch = (type, raw) => {
    let data;
    try { data = JSON.parse(raw); } catch { data = raw; }
    _listeners.forEach((fn) => { try { fn(type, data); } catch {} });
  };

  // 统一监听：后端每事件都发命名事件 + 默认消息（双通道）。
  // 这里只用 onmessage 消费默认消息（带 __type 字段），避免命名事件重复触发。
  _es.onmessage = (e) => {
    let data;
    try { data = JSON.parse(e.data); } catch { data = e.data; }
    if (data && typeof data === 'object' && data.__type) {
      dispatch(data.__type, data);
    } else {
      dispatch('message', data);
    }
  };

  // 重连时通知所有监听者
  _es.onerror = () => {
    // 浏览器会自动重连，不需要手动处理
  };

  return _es;
}

export function addSSEListener(key, fn) {
  _listeners.set(key, fn);
  getSharedEventSource();
}

export function removeSSEListener(key) {
  _listeners.delete(key);
  if (_listeners.size === 0 && _es) {
    _es.close();
    _es = null;
  }
}
