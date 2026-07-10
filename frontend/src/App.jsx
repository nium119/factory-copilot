import React, { useState, useEffect, useRef, useCallback } from 'react';
import { ConfigProvider, theme, App as AntApp, Button, Space, Dropdown } from 'antd';
import { UserOutlined, LogoutOutlined, LoginOutlined } from '@ant-design/icons';
import store from 'store2';
import ChatInterface from './components/ChatInterface';
import AgentSidebar from './components/AgentSidebar';
import ConversationDrawer from './components/ConversationDrawer';
import ExplorerAlertDrawer from './components/ExplorerAlert';
import ChainManager from './components/ChainManager';
import LoginModal from './components/LoginModal';

import { ConversationProvider } from './stores/ConversationContext';
import './index.css';
import { getAgents } from './services/messageService';
import request from './services/request';

function App() {
  const [sessionId, setSessionId] = useState('default');
  const [initialMessage, setInitialMessage] = useState(null);
  const [initialWebSearch, setInitialWebSearch] = useState(false);
  const [agents, setAgents] = useState([]);
  const [siderWidth, setSiderWidth] = useState(300);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [selectedAgent, setSelectedAgent] = useState(null);

  // 探索者异常预警状态
  const [explorerOpen, setExplorerOpen] = useState(false);
  const [explorerAnomalies, setExplorerAnomalies] = useState([]);

  // 链条管理状态
  const [chainManagerOpen, setChainManagerOpen] = useState(false);
  const [configRefreshKey, setConfigRefreshKey] = useState(0);

  // 用户登录状态
  const [user, setUser] = useState(null);
  const [loginOpen, setLoginOpen] = useState(false);

  useEffect(() => {
    const savedUser = store('__SRMC_Data_user');
    if (savedUser) setUser(savedUser);
  }, []);

  const handleLoginSuccess = (loggedInUser) => {
    if (loggedInUser) {
      setUser(loggedInUser);
    } else {
      setUser(null);
    }
  };

  const handleLogout = () => {
    store.remove('__SRMC_Config_token');
    store.remove('__SRMC_Data_user');
    localStorage.removeItem('token');
    // 清除 MES OAuth 设置的 cookie
    const cookiesToClear = ['plant', 'jyToken', 'jyToken2', 'ex', 'currentUserInfo', 'order_execute_loginInfoDto'];
    cookiesToClear.forEach((name) => {
      document.cookie = `${name}=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/;`;
    });
    setUser(null);
  };

  const isDraggingRef = useRef(false);
  const startXRef = useRef(0);
  const startWidthRef = useRef(0);

  // 探索者轮询：每 5 分钟检查一次异常
  useEffect(() => {
    const fetchAnomalies = async () => {
      try {
        const result = await request.get('/explorer/analyze?hours=24');
        const anomalies = result?.analysis?.anomalies || result?.anomalies || [];
        if (anomalies.length > 0) {
          setExplorerAnomalies(anomalies);
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

  // 加载 Agent 列表（唯一数据源，向下传递）
  const refreshAgents = async () => {
    try {
      const list = await getAgents();
      setAgents(Array.isArray(list) ? list : []);
    } catch { /* silent */ }
  };
  const handleRefresh = useCallback(() => { refreshAgents(); }, [refreshAgents]);
  const handleNamespaceChange = useCallback(() => {
    refreshAgents();
    setConfigRefreshKey(k => k + 1);
  }, [refreshAgents]);

  useEffect(() => { refreshAgents(); }, []);

  // 解析URL参数
  useEffect(() => {
    const urlParams = new URLSearchParams(window.location.search);
    const sendUserMsg = urlParams.get('sendUserMsg');
    if (sendUserMsg) {
      setInitialMessage(decodeURIComponent(sendUserMsg));
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
                onToggleChainManager={() => setChainManagerOpen(!chainManagerOpen)}
                chainManagerActive={chainManagerOpen}
                currentAgentName={selectedAgent?.name}
                agents={agents}
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
              {/* 顶部用户栏 */}
              <div style={{
                display: 'flex', alignItems: 'center', justifyContent: 'flex-end',
                padding: '6px 16px', height: 40,
                background: '#ffffff', borderBottom: '1px solid #f0f0f0',
                flexShrink: 0,
              }}>
                {user ? (
                  <Space>
                    <span style={{ fontSize: 13, color: '#6b7280' }}>
                      <UserOutlined style={{ marginRight: 4 }} />
                      {user.RealName || user.NowLoginUser || user.UserAccount}
                    </span>
                    <Button
                      type="text" size="small" icon={<LogoutOutlined />}
                      onClick={handleLogout}
                      style={{ fontSize: 12, color: '#94a3b8' }}
                    >
                      退出
                    </Button>
                  </Space>
                ) : (
                  <Button
                    type="primary" size="small" ghost icon={<LoginOutlined />}
                    onClick={() => setLoginOpen(true)}
                    style={{ fontSize: 12 }}
                  >
                    登录
                  </Button>
                )}
              </div>

              {chainManagerOpen ? (
                <ChainManager key={configRefreshKey} onBack={() => setChainManagerOpen(false)} onNamespaceChange={handleNamespaceChange} onRefresh={handleRefresh} />
              ) : (
                <ChatInterface
                  sessionId={sessionId}
                  initialMessage={initialMessage}
                  initialWebSearch={initialWebSearch}
                  agents={agents}
                  selectedAgent={selectedAgent}
                />
              )}
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

          {/* 登录弹窗 */}
          <LoginModal
            open={loginOpen}
            onClose={() => setLoginOpen(false)}
            onLoginSuccess={handleLoginSuccess}
          />

        </ConversationProvider>
      </AntApp>
    </ConfigProvider>
  );
}

export default App;