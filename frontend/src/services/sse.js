/**
 * 全局 SSE 单例 — 同一 iframe 内所有组件共享一个 EventSource 连接。
 *
 * 手动管理重连（而非依赖浏览器默认自动重连）：
 *   1. 每次重连都重新读取最新 token（解决 token 失效/刷新问题）
 *   2. 指数退避（1s → 2s → 4s → 5s 封顶）
 *   3. 通过 __connected / __disconnected 通知监听者连接状态，便于 UI 提示
 */
import { getAuthToken } from '../utils/authFetch';

let _es = null;
let _retry = 0;
let _reconnectTimer = null;
let _manuallyClosed = false;
const _listeners = new Map();
const MAX_RETRY_MS = 5000; // 最大重连间隔

function _notify(type, data) {
  _listeners.forEach((fn) => { try { fn(type, data); } catch {} });
}

function _connect() {
  // 每次连接都取最新 token（登录态变化后能自动带上新 token）
  const token = getAuthToken();
  const url = (window.__API_BASE__ || '/api') + '/messages/events/stream'
    + (token ? `?token=${encodeURIComponent(token)}` : '');

  const es = new EventSource(url);
  _es = es;

  es.onopen = () => {
    _retry = 0;
    _notify('__connected', {});
  };

  // 后端每事件发「命名事件 + 默认消息(__type)」双通道，这里只用 onmessage 消费默认消息
  es.onmessage = (e) => {
    let data;
    try { data = JSON.parse(e.data); } catch { data = e.data; }
    if (data && typeof data === 'object' && data.__type) {
      _notify(data.__type, data);
    } else {
      _notify('message', data);
    }
  };

  es.onerror = () => {
    if (_manuallyClosed) return;
    es.close();
    _notify('__disconnected', {});
    const delay = Math.min(1000 * 2 ** _retry, MAX_RETRY_MS);
    _retry += 1;
    clearTimeout(_reconnectTimer);
    _reconnectTimer = setTimeout(() => {
      if (!_manuallyClosed) _connect();
    }, delay);
  };

  return es;
}

export function getSharedEventSource() {
  if (_es && _es.readyState !== EventSource.CLOSED) return _es;
  _manuallyClosed = false;
  return _connect();
}

export function addSSEListener(key, fn) {
  _listeners.set(key, fn);
  getSharedEventSource();
}

export function removeSSEListener(key) {
  _listeners.delete(key);
  if (_listeners.size === 0 && _es) {
    _manuallyClosed = true;
    clearTimeout(_reconnectTimer);
    _es.close();
    _es = null;
    _retry = 0;
  }
}
