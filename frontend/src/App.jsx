import React, { useState, useEffect, useCallback } from 'react';
import { ConfigProvider, theme, App as AntApp, Button, Space, Select } from 'antd';
import { UserOutlined, LogoutOutlined, LoginOutlined } from '@ant-design/icons';
import store from 'store2';
import ChatInterface from './components/ChatInterface';
import ConversationDrawer from './components/ConversationDrawer';
import ExplorerAlertDrawer from './components/ExplorerAlert';
import ChainManager from './components/ChainManager';
import LoginModal from './components/LoginModal';
import MenuLayout from './components/layout/MenuLayout';
import PendingApprovalView from './components/layout/PendingApprovalView';
import ResourceStatusView from './components/layout/ResourceStatusView';

import { ConversationProvider } from './stores/ConversationContext';
import './index.css';
import { getAgents } from './services/messageService';
import request from './services/request';

// ── 菜单 key → ChainManager initialTab 映射 ──
const TAB_MAP = {
  'agent-config': 'agents',
  'chains': 'chains',
  'skills': 'skills',
  'systems': 'systems',
  'tools': 'mcp_servers',
  'monitor': 'explorer_rules',
};

function App() {
  const [sessionId, setSessionId] = useState('default');
  const [initialMessage, setInitialMessage] = useState(null);
  const [initialWebSearch, setInitialWebSearch] = useState(false);
  const [agents, setAgents] = useState([]);
  const [selectedAgent, setSelectedAgent] = useState(null);

  // ── 菜单视图状态 ──
  const [activeMenu, setActiveMenu] = useState('chat');
  const [menuCollapsed, setMenuCollapsed] = useState(false);
  const [configRefreshKey, setConfigRefreshKey] = useState(0);
  const [pendingCount, setPendingCount] = useState(0);

  // 历史记录
  const [historyOpen, setHistoryOpen] = useState(false);

  // 探索者异常预警
  const [explorerOpen, setExplorerOpen] = useState(false);
  const [explorerAnomalies, setExplorerAnomalies] = useState([]);

  // 用户登录
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
    const cookiesToClear = ['plant', 'jyToken', 'jyToken2', 'ex', 'currentUserInfo', 'order_execute_loginInfoDto'];
    cookiesToClear.forEach((name) => {
      document.cookie = `${name}=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/;`;
    });
    setUser(null);
  };

  // 探索者轮询
  useEffect(() => {
    const fetchAnomalies = async () => {
      try {
        const result = await request.get('/explorer/analyze?hours=24');
        const anomalies = result?.analysis?.anomalies || result?.anomalies || [];
        if (anomalies.length > 0) {
          setExplorerAnomalies(anomalies);
        }
      } catch { /* ignore */ }
    };
    fetchAnomalies();
    const interval = setInterval(fetchAnomalies, 5 * 60 * 1000);
    return () => clearInterval(interval);
  }, []);

  // 待审批轮询
  useEffect(() => {
    const fetchPending = async () => {
      try {
        const userId = localStorage.getItem('user_id') || '';
        const userRoles = localStorage.getItem('user_roles') || '';
        const resp = await fetch(`/api/messages/pending?user_id=${userId}&user_roles=${userRoles}`);
        const data = await resp.json();
        setPendingCount(data.total || 0);
      } catch { /* ignore */ }
    };
    fetchPending();
    const interval = setInterval(fetchPending, 15000);
    return () => clearInterval(interval);
  }, []);

  // 加载 Agent 列表
  const refreshAgents = useCallback(async () => {
    try {
      const list = await getAgents();
      setAgents(Array.isArray(list) ? list : []);
    } catch { /* silent */ }
  }, []);

  const handleNamespaceChange = useCallback(() => {
    refreshAgents();
    setConfigRefreshKey(k => k + 1);
  }, [refreshAgents]);

  useEffect(() => { refreshAgents(); }, [refreshAgents]);

  // URL 参数
  useEffect(() => {
    const urlParams = new URLSearchParams(window.location.search);
    const sendUserMsg = urlParams.get('sendUserMsg');
    if (sendUserMsg) {
      setInitialMessage(decodeURIComponent(sendUserMsg));
      setInitialWebSearch(true);
      window.history.replaceState({}, document.title, window.location.pathname);
    }
  }, []);

  // ── 菜单路由 ──
  const handleMenuChange = (key) => {
    setActiveMenu(key);
    if (key === 'history') {
      setHistoryOpen(true);
      return;
    }
    // 配置类菜单刷新 ChainManager
    if (TAB_MAP[key]) {
      setConfigRefreshKey(k => k + 1);
    }
  };

  const renderContent = () => {
    // 配置类：统一用 ChainManager
    if (TAB_MAP[activeMenu]) {
      return (
        <ChainManager
          key={configRefreshKey}
          initialTab={TAB_MAP[activeMenu]}
          onBack={() => setActiveMenu('chat')}
          onNamespaceChange={handleNamespaceChange}
          onRefresh={refreshAgents}
        />
      );
    }

    switch (activeMenu) {
      case 'chat':
        return (
          <ChatInterface
            sessionId={sessionId}
            initialMessage={initialMessage}
            initialWebSearch={initialWebSearch}
            agents={agents}
            selectedAgent={selectedAgent}
          />
        );
      case 'pending':
        return <PendingApprovalView />;
      case 'resources':
        return <ResourceStatusView />;
      default:
        return (
          <ChatInterface
            sessionId={sessionId}
            initialMessage={initialMessage}
            initialWebSearch={initialWebSearch}
            agents={agents}
            selectedAgent={selectedAgent}
          />
        );
    }
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
          <MenuLayout
            activeMenu={activeMenu}
            onMenuChange={handleMenuChange}
            pendingCount={pendingCount}
            collapsed={menuCollapsed}
            onToggleCollapse={() => setMenuCollapsed(!menuCollapsed)}
          >
            {/* 顶部用户栏 */}
            <div style={{
              display: 'flex', alignItems: 'center', justifyContent: 'space-between',
              padding: '6px 16px', height: 40,
              background: '#ffffff', borderBottom: '1px solid #f0f0f0',
              flexShrink: 0,
            }}>
              {/* 左侧：Agent 选择器（对话模式下显示）*/}
              <div style={{ flex: 1 }}>
                {activeMenu === 'chat' && agents.length > 0 && (
                  <Select
                    size="small"
                    value={selectedAgent?.name || undefined}
                    onChange={(name) => {
                      const agent = agents.find(a => a.name === name);
                      setSelectedAgent(agent || null);
                    }}
                    placeholder="选择 Agent"
                    style={{ width: 200, fontSize: 12 }}
                    options={agents.map(a => ({
                      value: a.name,
                      label: (
                        <span>
                          <span style={{
                            display: 'inline-block', width: 8, height: 8, borderRadius: 4,
                            background: a.color || '#6c5ce7', marginRight: 6,
                          }} />
                          {a.display_name || a.name}
                        </span>
                      ),
                    }))}
                    allowClear
                    onClear={() => setSelectedAgent(null)}
                  />
                )}
              </div>

              {/* 右侧：用户信息 */}
              {user ? (
                <Space>
                  <span style={{ fontSize: 13, color: '#6b7280' }}>
                    <UserOutlined style={{ marginRight: 4 }} />
                    {user.RealName || user.NowLoginUser || user.UserAccount}
                  </span>
                  <Button type="text" size="small" icon={<LogoutOutlined />}
                    onClick={handleLogout} style={{ fontSize: 12, color: '#94a3b8' }}>
                    退出
                  </Button>
                </Space>
              ) : (
                <Button type="primary" size="small" ghost icon={<LoginOutlined />}
                  onClick={() => setLoginOpen(true)} style={{ fontSize: 12 }}>
                  登录
                </Button>
              )}
            </div>

            {/* 主视图 */}
            <div style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
              {renderContent()}
            </div>
          </MenuLayout>

          {/* 历史记录抽屉 */}
          <ConversationDrawer
            open={historyOpen}
            onClose={() => { setHistoryOpen(false); }}
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
