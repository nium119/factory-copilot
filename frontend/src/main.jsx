import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './index.css'

// 全局 API base URL，供 fetch/EventSource 等原生 API 使用
window.__API_BASE__ = import.meta.env.VITE_API_BASE_URL || '/api';

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
