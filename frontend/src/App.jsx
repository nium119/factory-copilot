import React, { useState, useEffect, useCallback } from 'react';
import { ConfigProvider, theme, App as AntApp, Button, Space } from 'antd';
import { UserOutlined, LogoutOutlined, LoginOutlined, BellOutlined } from '@ant-design/icons';
import store from 'store2';
import ChatInterface from './components/ChatInterface';
import ConversationDrawer from './components/ConversationDrawer';
import ExplorerAlertDrawer from './components/ExplorerAlert';
import ChainManager from './components/ChainManager';
import LoginModal from './components/LoginModal';
import MenuLayout from './components/layout/MenuLayout';
import ChatView from './components/layout/ChatView';
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
  const [doneMsg, setDoneMsg] = useState(null); // { text, reviewer, action }

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

  // 待审批 SSE 实时更新角标 + 浏览器通知
  useEffect(() => {
    let lastTotal = 0;
    const fetchPending = async () => {
      try {
        const resp = await fetch('/api/messages/pending');
        const data = await resp.json();
        const total = data.total || 0;
        setPendingCount(total);
        if (total > lastTotal) {
            setDoneMsg(null);
        }
        lastTotal = total;
      } catch { /* ignore */ }
    };
    fetchPending();
    if (Notification.permission === 'default') Notification.requestPermission();
    const es = new EventSource('/api/messages/events/stream');
    es.addEventListener('pending_updated', fetchPending);
    es.addEventListener('pending_updated', fetchPending);
    es.addEventListener('approval_done', (e) => {
      fetchPending();
      setPendingCount(0); // 立即清掉待审批浮标
      try {
        const data = JSON.parse(e.data);
        setDoneMsg({
          text: `${data.approved ? '已通过' : '已拒绝'}`,
          reviewer: data.reviewer,
          action: data.action,
        });
      } catch {}
    });
    return () => es.close();
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

  // 始终挂载对话视图，避免切菜单时状态丢失
  const isChat = activeMenu === 'chat';
  const isPending = activeMenu === 'pending';
  const isConfig = !!TAB_MAP[activeMenu] || activeMenu === 'resources';
  const configTab = TAB_MAP[activeMenu] || '';

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

            {/* 主视图 — 对话始终挂载，隐藏而非销毁 */}
            <div style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
              <div style={{ display: isChat ? 'flex' : 'none', height: '100%', flexDirection: 'column' }}>
                <ChatView
                  sessionId={sessionId}
                  initialMessage={initialMessage}
                  initialWebSearch={initialWebSearch}
                  agents={agents}
                  selectedAgent={selectedAgent}
                  onSelectAgent={setSelectedAgent}
                  onToggleHistory={() => setHistoryOpen(true)}
                  onToggleChainManager={() => setActiveMenu('agent-config')}
                  chainManagerActive={false}
                  explorerAnomalies={explorerAnomalies}
                  onToggleExplorer={() => setExplorerOpen(!explorerOpen)}
                />
              </div>
              {isPending && <PendingApprovalView />}
              {activeMenu === 'resources' && <ResourceStatusView />}
              {isConfig && configTab && (
                <ChainManager
                  key={configRefreshKey}
                  initialTab={configTab}
                  onNamespaceChange={handleNamespaceChange}
                  onRefresh={refreshAgents}
                />
              )}
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
          {/* 待审批/审批完成 悬浮通知 */}
          {(pendingCount > 0 || doneMsg) && (
            <div onClick={() => { setActiveMenu(doneMsg ? 'chat' : 'pending'); setDoneMsg(null); }} style={{
              position: 'fixed', bottom: 24, right: 24, zIndex: 1000,
              background: doneMsg ? (doneMsg.text === '已拒绝' ? '#ff4d4f' : '#52c41a') : '#ff4d4f',
              color: '#fff', borderRadius: 12,
              padding: '10px 16px', cursor: 'pointer',
              boxShadow: '0 4px 12px rgba(0,0,0,0.2)',
              display: 'flex', flexDirection: 'column', gap: 2, fontSize: 13, fontWeight: 500,
              maxWidth: 240,
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <BellOutlined style={{ fontSize: 14 }} />
                <span style={{ fontWeight: 600, fontSize: 14 }}>
                  {doneMsg
                    ? `${doneMsg.reviewer} ${doneMsg.text}: ${doneMsg.action}`
                    : `${pendingCount} 条待审批`}
                </span>
              </div>
              {doneMsg && <div style={{ fontSize: 11, opacity: 0.8 }}>点击查看详情</div>}
            </div>
          )}

        </ConversationProvider>
      </AntApp>
    </ConfigProvider>
  );
}

export default App;
