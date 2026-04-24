import React, { useState, useEffect } from 'react';
import { Button, Spin, Empty } from 'antd';
import { PlusOutlined, ClockCircleOutlined, ThunderboltOutlined } from '@ant-design/icons';
import { getAgents } from '../../services/messageService';
import { useConversation } from '../../hooks/useConversation';
import './index.css';

/**
 * 智能体侧边栏组件
 * 左侧显示 Agent 列表 + 历史记录按钮
 */
export default function AgentSidebar({ onSelectAgent, onToggleHistory, currentAgentName, agents: propAgents }) {
  const [agents, setAgents] = useState([]);
  const [loading, setLoading] = useState(false);

  const { createConversation } = useConversation();

  useEffect(() => {
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
  }, []);

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
          璟岩MES AI智能体
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
    </div>
  );
}
