/** 全局 SSE 单例 — 同一 iframe 内所有组件共享一个 EventSource 连接 */

let _es = null;
const _listeners = new Map();

export function getSharedEventSource() {
  if (_es && _es.readyState !== EventSource.CLOSED) return _es;

  const url = (window.__API_BASE__ || '/api') + '/messages/events/stream';
  _es = new EventSource(url);

  _es.onmessage = (e) => {
    let data;
    try { data = JSON.parse(e.data); } catch { data = e.data; }
    _listeners.forEach((fn) => { try { fn(e.type || 'message', data, e); } catch {} });
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
