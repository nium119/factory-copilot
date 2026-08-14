import React, { useState, useEffect, useCallback } from 'react';
import { ConfigProvider, theme, App as AntApp, Button, Space, message } from 'antd';
import { UserOutlined, LogoutOutlined, LoginOutlined, BellOutlined } from '@ant-design/icons';
import store from 'store2';
import ChatInterface from './components/ChatInterface';
import ConversationDrawer from './components/ConversationDrawer';
import ExplorerAlertDrawer from './components/ExplorerAlert';
import ChainManager from './components/ChainManager';
import LoginModal from './components/LoginModal';
import MenuLayout from './components/layout/MenuLayout';
import ChatView from './components/layout/ChatView';
import OriginalConversationDrawer from './components/layout/OriginalConversationDrawer';
import PendingApprovalView from './components/layout/PendingApprovalView';
import ReportHistoryView from './components/layout/ReportHistoryView';
import ResourceStatusView from './components/layout/ResourceStatusView';
import PromptLogView from './components/layout/PromptLogView';
import AuditLogView from './components/layout/AuditLogView';
import TraceView from './components/layout/TraceView';
import NotificationBell from './components/layout/NotificationBell';
import NotificationList from './components/layout/NotificationList';
import NotificationPrefs from './components/settings/NotificationPrefs';

import { ConversationProvider } from './stores/ConversationContext';
import './index.css';
import { getAgents } from './services/messageService';
import request from './services/request';
import { addSSEListener, removeSSEListener } from './services/sse';

// ── 菜单 key → ChainManager initialTab + tab 过滤 ──
const TAB_CONFIG = {
  'agent-config':  { initialTab: 'agents', tabs: ['agents', 'knowledge', 'chains', 'skills', 'systems', 'vectorization'] },
  'system-config': { initialTab: 'models', tabs: ['models', 'resources', 'connections'] },
  'integrations':  { initialTab: 'mcp', tabs: ['mcp', 'a2a', 'a2a-server'] },
  'api-logs':      { initialTab: 'api-logs', tabs: ['api-logs'] },
  'stats':         { initialTab: 'stats', tabs: ['stats'] },
};

function App() {
  const envSite = import.meta.env.VITE_ENV_SITE || 'main';
  const isSubApp = envSite !== 'main';

  const [sessionId, setSessionId] = useState('default');
  const [initialMessage, setInitialMessage] = useState(null);
  const [initialWebSearch, setInitialWebSearch] = useState(false);
  const [agents, setAgents] = useState([]);
  const [selectedAgent, setSelectedAgent] = useState(null);

  // ── 菜单视图状态 ──
  // path 最后一段 → menu key（兼容 /AI-OS/chat 这种带 base 的路径）
  const SEG_MENU = {
    chat: 'chat', pending: 'pending', reports: 'reports',
    'agent-config': 'agent-config', 'system-config': 'system-config', integrations: 'integrations',
    notifications: 'notifications', 'notif-list': 'notif-list',
    resources: 'resources', 'api-logs': 'api-logs', stats: 'stats', 'prompt-logs': 'prompt-logs',
    'audit-logs': 'audit-logs', traces: 'traces',
  };
  const pathToMenu = (p) => SEG_MENU[p.replace(/\/+$/, '').split('/').pop()] || 'chat';

  const [activeMenu, setActiveMenu] = useState(() => pathToMenu(window.location.pathname));
  const [menuCollapsed, setMenuCollapsed] = useState(false);
  const [configRefreshKey, setConfigRefreshKey] = useState(0);
  const [pendingCount, setPendingCount] = useState(0);
  const [notificationCount, setNotificationCount] = useState(0);
  const [doneMsg, setDoneMsg] = useState(null); // { text, reviewer, action, conversation_id }
  const [sseConnected, setSseConnected] = useState(true); // 全局 SSE 实时连接状态

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
        const data = await request.get('/messages/pending');
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
    const sseKey = 'main-app';
    addSSEListener(sseKey, (type, data) => {
      if (type === '__connected') setSseConnected(true);
      if (type === '__disconnected') setSseConnected(false);
      if (type === 'pending_updated') fetchPending();
      if (type === 'approval_done') {
        fetchPending();
        setPendingCount(0);
        try {
          setDoneMsg({
            text: `${data.approved ? '已通过' : '已拒绝'}`,
            reviewer: data.reviewer,
            action: data.action,
            conversation_id: data.conversation_id,
          });
        } catch {}
      }
    });


    // 通知标记已读后，刷新通知计数（右下角浮层同步消失）
    const fetchNotifCount = async () => {
      try {
        const data = await request.get('/notifications/count');
        setNotificationCount(data.count || 0);
      } catch { /* ignore */ }
    };
    fetchNotifCount();
    const onNotifRead = () => fetchNotifCount();
    window.addEventListener('notifications-read', onNotifRead);

    return () => {
      removeSSEListener(sseKey);
      window.removeEventListener('notifications-read', onNotifRead);
    };
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

  // G 方案路由同步：主应用通过 postMessage 通知菜单切换(subapp-router-change)
  useEffect(() => {
    const handler = (e) => {
      if (e.data?.type !== 'subapp-router-change') return;
      // origin 白名单校验
      const allowed = [window.location.origin, 'http://localhost:8002', 'http://172.21.10.8:8002'];
      if (!allowed.includes(e.origin)) return;
      const subPath = e.data.subPath || '';
      const menu = SEG_MENU[subPath.replace(/^\//, '')] || 'chat';
      setActiveMenu(menu);
    };
    window.addEventListener('message', handler);
    return () => window.removeEventListener('message', handler);
  }, []);

  // 401 未授权提示：引导登录（独立登录模式弹提示；子应用由宿主处理）。
  // 页面加载时多个请求并发 401，节流避免刷屏（5 秒内只提示一次）
  useEffect(() => {
    const lastShown = { t: 0 };
    const handler = () => {
      if (isSubApp) return;
      const now = Date.now();
      if (now - lastShown.t < 5000) return;
      lastShown.t = now;
      message.warning('请先登录后再操作');
    };
    window.addEventListener('fc:auth-required', handler);
    return () => window.removeEventListener('fc:auth-required', handler);
  }, [isSubApp]);

  // Wujie 单 iframe 路由：主应用通过 bus 通知菜单切换(兼容)
  useEffect(() => {
    const bus = window.$wujie?.bus;
    if (!bus) return;
    const handler = (path) => {
      const menu = SEG_MENU[String(path).replace(/^\//, '')] || 'chat';
      setActiveMenu(menu);
    };
    bus.$on('ai-os-route-change', handler);
    return () => bus.$off('ai-os-route-change', handler);
  }, []);

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
    // 提取 base（如 /AI-OS），拼接菜单路径
    const seg = window.location.pathname.replace(/\/+$/, '').split('/').pop();
    const base = SEG_MENU[seg] ? window.location.pathname.substring(0, window.location.pathname.lastIndexOf('/')) || '/' : window.location.pathname.replace(/\/+$/, '');
    const dest = key === 'chat' ? (base || '/') : `${base}/${key}`;
    if (window.location.pathname !== dest) {
      window.history.pushState({}, '', dest);
    }
    if (key === 'history') {
      setHistoryOpen(true);
      return;
    }
    // 配置类菜单刷新 ChainManager
    if (TAB_CONFIG[key]) {
      setConfigRefreshKey(k => k + 1);
    }
  };

  // 始终挂载对话视图，避免切菜单时状态丢失
  const isChat = activeMenu === 'chat';
  const isPending = activeMenu === 'pending';
  const isReports = activeMenu === 'reports';
  const cfg = TAB_CONFIG[activeMenu];
  const isConfig = !!cfg;

  return (
    <div id="app-root" style={{ height: '100%' }}>
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
        <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
        <ConversationProvider>
          {isSubApp ? (
            /* 子应用模式：无侧栏、无 header——菜单和登录由父应用提供 */
            <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
              <div style={{ display: isChat ? 'flex' : 'none', flex: 1, flexDirection: 'column', minHeight: 0 }}>
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
              {isReports && <ReportHistoryView />}
              {activeMenu === 'resources' && <ResourceStatusView />}
              {activeMenu === 'prompt-logs' && <PromptLogView />}
              {activeMenu === 'audit-logs' && <AuditLogView />}
              {activeMenu === 'traces' && <TraceView />}
              {activeMenu === 'notif-list' && <NotificationList />}
              {activeMenu === 'notifications' && <NotificationPrefs />}
              {isConfig && cfg && (
                <ChainManager
                  key={configRefreshKey}
                  initialTab={cfg.initialTab}
                  tabFilter={cfg.tabs}
                  onNamespaceChange={handleNamespaceChange}
                  onRefresh={refreshAgents}
                />
              )}
            </div>
          ) : (
          <MenuLayout
            activeMenu={activeMenu}
            onMenuChange={handleMenuChange}
            pendingCount={pendingCount}
            notificationCount={notificationCount}
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
              {/* 左侧：通知中心（仅主应用） */}
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                {!isSubApp && <NotificationBell />}
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
              {isReports && <ReportHistoryView />}
              {activeMenu === 'resources' && <ResourceStatusView />}
              {activeMenu === 'prompt-logs' && <PromptLogView />}
              {activeMenu === 'audit-logs' && <AuditLogView />}
              {activeMenu === 'traces' && <TraceView />}
              {activeMenu === 'notif-list' && <NotificationList />}
              {activeMenu === 'notifications' && <NotificationPrefs />}
              {isConfig && cfg && (
                <ChainManager
                  key={configRefreshKey}
                  initialTab={cfg.initialTab}
                  tabFilter={cfg.tabs}
                  onNamespaceChange={handleNamespaceChange}
                  onRefresh={refreshAgents}
                />
              )}
            </div>
          </MenuLayout>
          )}

          {/* 历史记录抽屉 */}
          <ConversationDrawer
            open={historyOpen}
            onClose={() => { setHistoryOpen(false); }}
          />

          {/* 原对话抽屉（通知/复核卡片"打开原对话"右侧展示上下文） */}
          <OriginalConversationDrawer />

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
            <div onClick={() => {
              if (doneMsg) {
                setActiveMenu('chat');
              } else {
                setActiveMenu('pending');
              }
              setDoneMsg(null);
            }} style={{
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

          {/* 通知浮层 — 和待审批一样，子应用也可见 */}
          {notificationCount > 0 && (
            <div onClick={async () => {
              if (!isSubApp) {
                setActiveMenu('notif-list');
              } else {
                // 子应用模式：一键标记已读
                try {
                  await request.put('/notifications/read-all');
                  setNotificationCount(0);
                } catch { /* ignore */ }
              }
            }} style={{
              position: 'fixed', bottom: 24, right: isSubApp ? 24 : 120, zIndex: 1000,
              background: '#fa8c16',
              color: '#fff', borderRadius: 12,
              padding: '8px 14px', cursor: 'pointer',
              boxShadow: '0 4px 12px rgba(0,0,0,0.2)',
              display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, fontWeight: 500,
            }}>
              <BellOutlined style={{ fontSize: 14 }} />
              <span>{notificationCount} 条通知</span>
            </div>
          )}

        </ConversationProvider>

        {/* SSE 实时连接断开提示 */}
        {!sseConnected && (
          <div style={{
            position: 'fixed', top: 0, left: 0, right: 0, zIndex: 2000,
            background: 'linear-gradient(90deg, #ff9800, #ff5722)',
            color: '#fff', textAlign: 'center', fontSize: 13, fontWeight: 500,
            padding: '6px 16px',
            boxShadow: '0 2px 8px rgba(0,0,0,0.15)',
          }}>
            实时连接已断开，正在重连…
          </div>
        )}
        </div>
      </AntApp>
    </ConfigProvider>
    </div>
  );
}

export default App;
