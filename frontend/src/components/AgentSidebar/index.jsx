import React, { useState, useEffect } from 'react';
import { Button, Spin, Empty } from 'antd';
import { PlusOutlined, ClockCircleOutlined, ThunderboltOutlined, SettingOutlined } from '@ant-design/icons';
import { getAgents } from '../../services/messageService';
import { useConversation } from '../../hooks/useConversation';
import { ExplorerAlertButton } from '../ExplorerAlert';
import './index.css';

/**
 * 智能体侧边栏组件
 * 左侧显示 Agent 列表 + 历史记录按钮
 */
const RESOURCE_META = {
  constrained: { color: '#faad14', bg: '#fffbe6', border: '#ffe58f', text: '系统繁忙' },
  critical: { color: '#ff4d4f', bg: '#fff2f0', border: '#ffccc7', text: '系统高负载' },
};

export default function AgentSidebar({ onSelectAgent, onToggleHistory, onToggleChainManager, chainManagerActive, currentAgentName, agents: propAgents, explorerAnomalies = [], onToggleExplorer }) {
  const [agents, setAgents] = useState([]);
  const [loading, setLoading] = useState(false);
  const [resourceState, setResourceState] = useState(null);

  const { createConversation } = useConversation();

  const explorerCount = explorerAnomalies.length;

  useEffect(() => {
    const checkResources = async () => {
      try {
        const resp = await fetch('/api/system/resources');
        const data = await resp.json();
        if (data.tier === 'constrained' || data.tier === 'critical') {
          setResourceState(data);
        } else {
          setResourceState(null);
        }
      } catch {
        // ignore errors silently
      }
    };
    checkResources();
    const interval = setInterval(checkResources, 30000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    if (propAgents && propAgents.length > 0) {
      setAgents(propAgents);
      return;
    }
    const loadAgents = async () => {
      setLoading(true);
      try {
        const agentList = await getAgents();
        setAgents(Array.isArray(agentList) ? agentList : []);
      } catch (error) {
        console.error('加载 Agent 列表失败:', error);
      } finally {
        setLoading(false);
      }
    };
    loadAgents();
  }, [propAgents]);

  const handleAgentClick = (agent) => {
    onSelectAgent?.(agent);
  };

  // 创建新会话并切换到通用助手
  const handleNewChat = async () => {
    try {
      await createConversation('新对话');
    } catch (error) {
      console.error('创建对话失败:', error);
    }
  };

  const displayAgents = propAgents || agents;

  return (
    <div className="agent-sidebar">
      {/* 品牌标题区域 */}
      <div className="sidebar-brand">
        <ThunderboltOutlined style={{ fontSize: '20px', color: '#6c5ce7' }} />
        <div style={{ fontSize: '16px', fontWeight: 600, color: '#1a1a2e', letterSpacing: '0.5px' }}>
        璟岩AI智能体
        </div>
        <div className="sidebar-brand-version">v1.0</div>
      </div>

      {/* 顶部操作 */}
      <div className="sidebar-header">
        <Button
          type="primary"
          icon={<PlusOutlined />}
          style={{ flex: 1 }}
          onClick={handleNewChat}
        >
          新建对话
        </Button>
        <Button
          icon={<ClockCircleOutlined />}
          onClick={onToggleHistory}
          title="历史记录"
          style={{ marginLeft: '8px' }}
        >
          历史记录
        </Button>
      </div>

      {/* 异常预警按钮 */}
      {explorerCount > 0 && (
        <div style={{ padding: '0 16px', marginBottom: '8px' }}>
          <ExplorerAlertButton count={explorerCount} onClick={onToggleExplorer} />
        </div>
      )}

      {/* 资源状态指示器 */}
      {resourceState && RESOURCE_META[resourceState.tier] && (
        <div style={{
          margin: '8px 16px',
          padding: '6px 10px',
          background: RESOURCE_META[resourceState.tier].bg,
          border: `1px solid ${RESOURCE_META[resourceState.tier].border}`,
          borderRadius: '6px',
          fontSize: '12px',
          color: RESOURCE_META[resourceState.tier].color,
          display: 'flex',
          alignItems: 'center',
          gap: '6px',
        }}>
          <span style={{ width: 8, height: 8, borderRadius: '50%', background: RESOURCE_META[resourceState.tier].color, display: 'inline-block' }} />
          <span>{RESOURCE_META[resourceState.tier].text}</span>
          <span style={{ marginLeft: 'auto', opacity: 0.7, fontSize: '11px' }}>
            {resourceState.concurrent_requests}/{resourceState.max_concurrency}
          </span>
        </div>
      )}

      {/* Agent 列表 */}
      <div className="sidebar-content">
        {loading ? (
          <div className="loading-container">
            <Spin />
          </div>
        ) : displayAgents.length === 0 ? (
          <Empty description="暂无智能体" />
        ) : (
          <div className="agent-list">
            {displayAgents.map((agent) => (
              <div
                key={agent.name}
                className={`agent-item ${currentAgentName === agent.name ? 'active' : ''}`}
                onClick={() => handleAgentClick(agent)}
              >
                <div className="agent-icon" style={{ background: `${agent.color}15`, color: agent.color }}>
                  {agent.icon}
                </div>
                <div className="agent-info">
                  <div className="agent-name" style={{ color: agent.color }}>{agent.display_name}</div>
                  <div className="agent-desc">{agent.description}</div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* 底部配置入口 */}
      <div className={`sidebar-footer ${chainManagerActive ? 'active' : ''}`}>
        <div
          className={`agent-item config-item ${chainManagerActive ? 'active' : ''}`}
          onClick={onToggleChainManager}
        >
          <div className="agent-icon" style={{
            background: chainManagerActive ? 'rgba(108, 92, 231, 0.12)' : 'rgba(0,0,0,0.04)',
            color: chainManagerActive ? '#6c5ce7' : '#8e99a4',
          }}>
            <SettingOutlined />
          </div>
          <div className="agent-info">
            <div className="agent-name" style={{ color: chainManagerActive ? '#6c5ce7' : '#8e99a4' }}>
              配置
            </div>
            <div className="agent-desc">业务域与链条管理</div>
          </div>
        </div>
      </div>

    </div>
  );
}
