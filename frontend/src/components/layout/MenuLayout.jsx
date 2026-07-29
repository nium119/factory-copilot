import React, { useState, useEffect, useCallback } from 'react';
import { Menu, Badge } from 'antd';
import {
  MessageOutlined, BellOutlined, SettingOutlined, FileTextOutlined,
  MenuFoldOutlined, MenuUnfoldOutlined, ControlOutlined,
  ApiOutlined, BarChartOutlined, CloudServerOutlined,
} from '@ant-design/icons';
import './MenuLayout.css';

// ── 静态菜单配置（开发模式）──
const MENU_ITEMS = [
  { key: 'chat',           icon: <MessageOutlined />,     label: 'AI助手' },
  { key: 'pending',        icon: <BellOutlined />,         label: '待审批',     badgeKey: 'pending' },
  { key: 'notifications',  icon: <BellOutlined />,         label: '通知中心',   badgeKey: 'notifications' },
  { key: 'reports',        icon: <FileTextOutlined />,     label: '历史分析' },
  { key: 'agent-config',   icon: <ControlOutlined />,      label: '业务配置' },
  { key: 'system-config',  icon: <SettingOutlined />,      label: '系统设置' },
  { key: 'integrations',   icon: <CloudServerOutlined />,  label: '集成管理' },
  { key: 'api-logs',       icon: <ApiOutlined />,          label: 'API 日志' },
  { key: 'stats',          icon: <BarChartOutlined />,      label: '行为数据' },
  { key: 'prompt-logs',    icon: <FileTextOutlined />,     label: '提示词日志' },
];

/**
 * 左侧扁平菜单 + 右侧内容区布局
 *
 * 部署时通过 window.__MENU_CONFIG__ 覆盖菜单项和 activeKey：
 *   { menuItems, activeMenu, userRoles }
 */
export default function MenuLayout({
  activeMenu,
  onMenuChange,
  pendingCount = 0,
  notificationCount = 0,
  children,          // 当前激活的视图内容
  collapsed = false,
  onToggleCollapse,
}) {
  const [menuConfig, setMenuConfig] = useState(null);

  // 读取 wujie 父应用传入的菜单配置
  useEffect(() => {
    if (window.__MENU_CONFIG__) {
      setMenuConfig(window.__MENU_CONFIG__);
    }
  }, []);

  // 菜单来源: wujie 父应用 > 默认全部
  let sourceItems = MENU_ITEMS;
  if (menuConfig?.menuItems) {
    sourceItems = menuConfig.menuItems;
  }
  const items = sourceItems.map(item => ({
    key: item.key,
    icon: item.icon,
    label: (
      <span style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <span>{item.label}</span>
        {item.badgeKey === 'pending' && pendingCount > 0 && (
          <Badge count={pendingCount} size="small" style={{ marginLeft: 8 }} />
        )}
        {item.badgeKey === 'notifications' && notificationCount > 0 && (
          <Badge count={notificationCount} size="small" style={{ marginLeft: 8 }} />
        )}
      </span>
    ),
  }));

  const currentKey = menuConfig?.activeMenu || activeMenu || 'chat';

  return (
    <div className="menu-layout">
      {/* 左侧菜单 */}
      <div className={`menu-sidebar ${collapsed ? 'collapsed' : ''}`}>
        <div className="menu-brand" onClick={onToggleCollapse}>
          {collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
          {!collapsed && <span className="menu-brand-text">璟岩知行OS</span>}
        </div>
        <Menu
          mode="inline"
          inlineCollapsed={collapsed}
          selectedKeys={[currentKey]}
          onClick={({ key }) => onMenuChange(key)}
          items={items}
          className="menu-tree"
        />
      </div>

      {/* 右侧内容区 */}
      <div className="menu-content">
        {children}
      </div>
    </div>
  );
}
