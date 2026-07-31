import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './index.css'

// 全局 API base URL，供 fetch/EventSource 等原生 API 使用
window.__API_BASE__ = import.meta.env.VITE_API_BASE_URL || '/api';

// 屏蔽 antd 5.x + React 18 的 findDOMNode 内部警告
const warn = console.warn;
console.warn = (...args) => {
  if (typeof args[0] === 'string' && args[0].includes('findDOMNode')) return;
  warn(...args);
};

ReactDOM.createRoot(document.getElementById('root')).render(<App />)
