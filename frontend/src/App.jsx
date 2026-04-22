import React, { useState, useEffect, useRef, useCallback } from 'react';
import { ConfigProvider, theme } from 'antd';
import ChatInterface from './components/ChatInterface';
import ConversationSidebar from './components/ConversationSidebar';
import { ConversationProvider } from './stores/ConversationContext';
import './index.css';

function App() {
  const [sessionId, setSessionId] = useState('default');
  const [initialMessage, setInitialMessage] = useState(null);
  const [initialDeepThinking, setInitialDeepThinking] = useState(false);
  const [initialWebSearch, setInitialWebSearch] = useState(false);
  const [siderWidth, setSiderWidth] = useState(280);
  const isDraggingRef = useRef(false);
  const startXRef = useRef(0);
  const startWidthRef = useRef(0);

  // 解析URL参数
  useEffect(() => {
    const urlParams = new URLSearchParams(window.location.search);
    const sendUserMsg = urlParams.get('sendUserMsg');
    if (sendUserMsg) {
      setInitialMessage(decodeURIComponent(sendUserMsg));
      setInitialDeepThinking(true);
      setInitialWebSearch(true);
      const newUrl = window.location.pathname;
      window.history.replaceState({}, document.title, newUrl);
    }
  }, []);

  // 拖拽调整宽度的事件处理
  const handleMouseDown = useCallback((e) => {
    isDraggingRef.current = true;
    startXRef.current = e.clientX;
    startWidthRef.current = siderWidth;
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    e.preventDefault();
  }, [siderWidth]);

  useEffect(() => {
    const handleMouseMove = (e) => {
      if (!isDraggingRef.current) return;
      const delta = e.clientX - startXRef.current;
      const newWidth = Math.min(Math.max(startWidthRef.current + delta, 200), 480);
      setSiderWidth(newWidth);
    };

    const handleMouseUp = () => {
      if (!isDraggingRef.current) return;
      isDraggingRef.current = false;
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };

    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);
    return () => {
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
    };
  }, []);

  return (
    <ConfigProvider
      theme={{
        algorithm: theme.defaultAlgorithm,
        token: {
          colorPrimary: '#6c5ce7',
          borderRadius: 8,
          colorBgContainer: '#ffffff',
          colorBgLayout: '#f5f5f7',
        },
      }}
    >
      <ConversationProvider>
        <div style={{
          display: 'flex',
          height: '100vh',
          background: '#f5f5f7',
          overflow: 'hidden',
        }}>
          {/* 会话侧边栏 - 自定义宽度 + 拖拽调整 */}
          <div
            style={{
              width: siderWidth,
              minWidth: 200,
              maxWidth: 480,
              height: '100%',
              background: '#ffffff',
              borderRight: '1px solid #e8e8ec',
              position: 'relative',
              overflow: 'hidden',
              flexShrink: 0,
            }}
          >
            <ConversationSidebar />
            {/* 拖拽手柄 */}
            <div
              onMouseDown={handleMouseDown}
              style={{
                position: 'absolute',
                right: 0,
                top: 0,
                bottom: 0,
                width: '6px',
                cursor: 'col-resize',
                zIndex: 10,
                transition: 'background 0.2s',
              }}
              onMouseEnter={(e) => { e.currentTarget.style.background = 'rgba(108, 92, 231, 0.15)'; }}
              onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; }}
            />
          </div>

          {/* 内容区 */}
          <div style={{
            flex: 1,
            minWidth: 0,
            height: '100%',
            background: '#f5f5f7',
            overflow: 'hidden',
            display: 'flex',
            flexDirection: 'column',
          }}>
            <ChatInterface 
              sessionId={sessionId} 
              initialMessage={initialMessage}
              initialDeepThinking={initialDeepThinking}
              initialWebSearch={initialWebSearch}
            />
          </div>
        </div>
      </ConversationProvider>
    </ConfigProvider>
  );
}

export default App;