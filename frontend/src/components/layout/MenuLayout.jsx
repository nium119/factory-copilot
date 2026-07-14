import React, { useState, useEffect, useCallback } from 'react';
import { Menu, Badge } from 'antd';
import {
  MessageOutlined, ClockCircleOutlined, BellOutlined,
  SettingOutlined, LinkOutlined, ThunderboltOutlined,
  ApiOutlined, ToolOutlined, AlertOutlined, DashboardOutlined,
  MenuFoldOutlined, MenuUnfoldOutlined,
} from '@ant-design/icons';
import './MenuLayout.css';

// ── 静态菜单配置（开发模式）──
const MENU_ITEMS = [
  { key: 'chat',         icon: <MessageOutlined />,     label: 'AI助手' },
  { key: 'pending',      icon: <BellOutlined />,         label: '待审批',     badgeKey: 'pending' },
  { key: 'agent-config', icon: <SettingOutlined />,      label: '业务域配置' },
  { key: 'chains',       icon: <LinkOutlined />,         label: '链条编排' },
  { key: 'skills',       icon: <ThunderboltOutlined />,  label: '技能管理' },
  { key: 'systems',      icon: <ApiOutlined />,          label: '系统接入' },
  { key: 'tools',        icon: <ToolOutlined />,         label: '工具服务' },
  { key: 'monitor',      icon: <AlertOutlined />,        label: '监控告警' },
  { key: 'resources',    icon: <DashboardOutlined />,    label: '资源状态' },
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

  // 根据菜单配置过滤/覆盖菜单项
  const items = (menuConfig?.menuItems || MENU_ITEMS).map(item => ({
    key: item.key,
    icon: item.icon,
    label: (
      <span style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <span>{item.label}</span>
        {item.badgeKey === 'pending' && pendingCount > 0 && (
          <Badge count={pendingCount} size="small" style={{ marginLeft: 8 }} />
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
          {!collapsed && <span className="menu-brand-text">璟岩AI</span>}
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
