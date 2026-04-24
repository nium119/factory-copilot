import React, { useState, useEffect, useRef, useCallback } from 'react';
import { ConfigProvider, theme, App as AntApp } from 'antd';
import ChatInterface from './components/ChatInterface';
import AgentSidebar from './components/AgentSidebar';
import ConversationDrawer from './components/ConversationDrawer';
import ExplorerAlertDrawer from './components/ExplorerAlert';
import { ConversationProvider } from './stores/ConversationContext';
import './index.css';
import request from './services/request';

function App() {
  const [sessionId, setSessionId] = useState('default');
  const [initialMessage, setInitialMessage] = useState(null);
  const [initialUseAgent, setInitialUseAgent] = useState(false);
  const [initialWebSearch, setInitialWebSearch] = useState(false);
  const [siderWidth, setSiderWidth] = useState(300);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [selectedAgent, setSelectedAgent] = useState(null);

  // 探索者异常预警状态
  const [explorerOpen, setExplorerOpen] = useState(false);
  const [explorerAnomalies, setExplorerAnomalies] = useState([]);
  const isDraggingRef = useRef(false);
  const startXRef = useRef(0);
  const startWidthRef = useRef(0);

  // 探索者轮询：每 5 分钟检查一次异常
  useEffect(() => {
    const fetchAnomalies = async () => {
      try {
        const result = await request.get('/explorer/analyze?hours=24');
        if (result?.anomalies?.length > 0) {
          setExplorerAnomalies(result.anomalies);
        }
      } catch {
        // 接口未实现时静默忽略
      }
    };

    // 初始立即执行一次
    fetchAnomalies();
    const interval = setInterval(fetchAnomalies, 5 * 60 * 1000); // 5 分钟

    return () => clearInterval(interval);
  }, []);

  const handleToggleExplorer = () => {
    setExplorerOpen(!explorerOpen);
  };

  // 解析URL参数
  useEffect(() => {
    const urlParams = new URLSearchParams(window.location.search);
    const sendUserMsg = urlParams.get('sendUserMsg');
    if (sendUserMsg) {
      setInitialMessage(decodeURIComponent(sendUserMsg));
      setInitialUseAgent(true);
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
      const newWidth = Math.min(Math.max(startWidthRef.current + delta, 200), 500);
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

  const handleSelectAgent = (agent) => {
    setSelectedAgent(agent);
  };

  const handleToggleHistory = () => {
    setHistoryOpen(!historyOpen);
  };

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
      <AntApp>
        <ConversationProvider>
          <div style={{
            display: 'flex',
            height: '100vh',
            background: '#f5f5f7',
            overflow: 'hidden',
          }}>
            {/* 智能体侧边栏 */}
            <div
              style={{
                width: siderWidth,
                minWidth: 200,
                maxWidth: 500,
                height: '100%',
                background: '#ffffff',
                borderRight: '1px solid #e8e8ec',
                position: 'relative',
                overflow: 'hidden',
                flexShrink: 0,
              }}
            >
              <AgentSidebar
                onSelectAgent={handleSelectAgent}
                onToggleHistory={handleToggleHistory}
                currentAgentName={selectedAgent?.name}
                explorerAnomalies={explorerAnomalies}
                onToggleExplorer={handleToggleExplorer}
              />
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
                initialUseAgent={initialUseAgent}
                initialWebSearch={initialWebSearch}
                selectedAgent={selectedAgent}
              />
            </div>
          </div>

          {/* 历史记录抽屉 */}
          <ConversationDrawer
            open={historyOpen}
            onClose={() => setHistoryOpen(false)}
          />

          {/* 异常预警抽屉 */}
          <ExplorerAlertDrawer
            anomalies={explorerAnomalies}
            visible={explorerOpen}
            onClose={() => setExplorerOpen(false)}
          />
        </ConversationProvider>
      </AntApp>
    </ConfigProvider>
  );
}

export default App;