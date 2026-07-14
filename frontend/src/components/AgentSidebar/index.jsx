import React, { useState, useEffect, useCallback } from 'react';
import { Button, Spin, Empty, Popover } from 'antd';
import { PlusOutlined, ClockCircleOutlined, ThunderboltOutlined, SettingOutlined, InfoCircleOutlined } from '@ant-design/icons';
import { getAgents, getPendingConfirmations, approveConfirmation, rejectConfirmation } from '../../services/messageService';
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

function AgentConceptsDisplay({ concepts, color }) {
  const [expanded, setExpanded] = useState(false);
  const show = expanded ? concepts : concepts.slice(0, 5);
  if (!concepts || concepts.length === 0) return null;
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 2, marginTop: 4 }}>
      {show.map(cn => (
        <span key={cn} style={{ fontSize: 10, color: '#8c8c8c', background: '#f0f0f0', padding: '0 4px', borderRadius: 3, lineHeight: '18px' }}>{cn}</span>
      ))}
      {concepts.length > 5 && (
        <span onClick={e => { e.stopPropagation(); setExpanded(!expanded); }}
          style={{ fontSize: 10, color: color || '#6c5ce7', cursor: 'pointer', lineHeight: '18px', padding: '0 4px' }}>
          {expanded ? '收起' : `+${concepts.length - 5}`}
        </span>
      )}
    </div>
  );
}

export default function AgentSidebar({ onSelectAgent, onToggleHistory, onToggleChainManager, chainManagerActive, currentAgentName, agents: propAgents, explorerAnomalies = [], onToggleExplorer }) {
  const [agents, setAgents] = useState([]);
  const [agentConcepts, setAgentConcepts] = useState({});
  const [loading, setLoading] = useState(false);
  const [resourceState, setResourceState] = useState(null);
  const [pendingList, setPendingList] = useState([]);
  const [pendingLoading, setPendingLoading] = useState(false);

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

  // ── 待审批轮询 ──
  const refreshPending = useCallback(async () => {
    setPendingLoading(true);
    try {
      // 从 localStorage 读取用户信息
      const userId = localStorage.getItem('user_id') || '';
      const userRoles = localStorage.getItem('user_roles') || '';
      const data = await getPendingConfirmations(userId, userRoles);
      setPendingList(data.pending || []);
    } catch {
      // 静默降级
    } finally {
      setPendingLoading(false);
    }
  }, []);

  useEffect(() => {
    refreshPending();
    const interval = setInterval(refreshPending, 15000); // 每15秒轮询
    return () => clearInterval(interval);
  }, [refreshPending]);

  const handleApprove = async (msgId) => {
    try {
      const userId = localStorage.getItem('user_id') || '';
      await approveConfirmation(msgId, userId, '');
      refreshPending();
    } catch (e) {
      console.error('审批失败:', e);
    }
  };

  const handleReject = async (msgId) => {
    try {
      const userId = localStorage.getItem('user_id') || '';
      await rejectConfirmation(msgId, userId, '');
      refreshPending();
    } catch (e) {
      console.error('拒绝失败:', e);
    }
  };

  const loadAgentList = useCallback(async () => {
    if (propAgents && propAgents.length > 0) {
      setAgents(propAgents);
      return;
    }
    setLoading(true);
    try {
      const [agentList, statusRes] = await Promise.all([
        getAgents(),
        fetch('/api/chains/compile/status').then(r => r.json()).catch(() => ({})),
      ]);
      setAgents(Array.isArray(agentList) ? agentList : []);
      // 构建 agent → 概念标签映射
      const skills = statusRes.skills || [];
      const acMap = {};
      skills.forEach(s => {
        const agentName = s.agent;
        if (agentName) {
          if (!acMap[agentName]) acMap[agentName] = [];
          acMap[agentName].push(s.concept_label || s.concept);
        }
      });
      setAgentConcepts(acMap);
    } catch (error) {
      console.error('加载 Agent 列表失败:', error);
    } finally {
      setLoading(false);
    }
  }, [propAgents]);

  useEffect(() => { loadAgentList(); }, [loadAgentList]);

  // 监听配置变更事件, 强制刷新（不依赖 propAgents 短路）
  useEffect(() => {
    const handler = async () => {
      setLoading(true);
      try {
        const [agentList, statusRes] = await Promise.all([
          getAgents(),
          fetch('/api/chains/compile/status').then(r => r.json()).catch(() => ({})),
        ]);
        setAgents(Array.isArray(agentList) ? agentList : []);
        const skills = statusRes.skills || [];
        const acMap = {};
        skills.forEach(s => {
          const an = s.agent;
          if (an) { if (!acMap[an]) acMap[an] = []; acMap[an].push(s.concept_label || s.concept); }
        });
        setAgentConcepts(acMap);
      } catch { /* silent */ }
      finally { setLoading(false); }
    };
    window.addEventListener('agents-changed', handler);
    return () => window.removeEventListener('agents-changed', handler);
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

  const displayAgents = propAgents?.length ? propAgents : agents;

  return (
    <div className="agent-sidebar">
      {/* 品牌标题区域 */}
      <div className="sidebar-brand">
        <ThunderboltOutlined style={{ fontSize: '20px', color: '#6c5ce7' }} />
        <div style={{ fontSize: '16px', fontWeight: 600, color: '#1a1a2e', letterSpacing: '0.5px' }}>
        璟岩AI智能体
        </div>
        <div className="sidebar-brand-version">v1.0</div>
        {displayAgents.length > 0 && displayAgents[0].project_description && (
          <Popover
            placement="right"
            content={
              <div style={{ maxWidth: 280, fontSize: 13, lineHeight: 1.8, color: '#555' }}>
                {displayAgents[0].project_description}
              </div>
            }
            title="行业知识图谱"
          >
            <InfoCircleOutlined style={{ fontSize: 13, color: '#bbb', cursor: 'pointer', marginLeft: 4 }} />
          </Popover>
        )}
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
          <div style={{ padding: '24px 16px', textAlign: 'center' }}>
            <Empty description="暂无业务域配置" />
            <div style={{ marginTop: '12px', padding: '12px 16px', background: '#f6f8fa', borderRadius: '8px', fontSize: '13px', color: '#65676b', lineHeight: '1.8' }}>
              <div style={{ fontWeight: 600, marginBottom: '4px', color: '#444' }}>📋 需要配置业务域</div>
              <div>当前行业尚未配置业务域，请先完成域配置。</div>
              <div style={{ marginTop: '8px' }}>
                点击下方 <span style={{ color: '#6c5ce7', fontWeight: 600 }}>「配置」</span> 进入业务域管理，
                使用 <span style={{ color: '#1677ff', fontWeight: 600 }}>「规则推导」</span> 或 <span style={{ color: '#1677ff', fontWeight: 600 }}>「LLM推导」</span> 自动生成。
              </div>
            </div>
          </div>
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
                  {agentConcepts[agent.display_name]?.length > 0 && (
                    <AgentConceptsDisplay
                      concepts={agentConcepts[agent.display_name]}
                      color={agent.color}
                    />
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* ── 待审批面板 ── */}
      {pendingList.length > 0 && (
        <div style={{
          margin: '12px 8px', padding: '8px 10px',
          background: '#fff7e6', borderRadius: 6, border: '1px solid #ffd591',
        }}>
          <div style={{ fontSize: 12, fontWeight: 600, color: '#d46b08', marginBottom: 6, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span>⏳ 待审批 ({pendingList.length})</span>
            <span onClick={refreshPending} style={{ fontSize: 10, color: '#8c8c8c', cursor: 'pointer' }}>
              {pendingLoading ? '刷新中...' : '刷新'}
            </span>
          </div>
          <div style={{ maxHeight: 200, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 6 }}>
            {pendingList.map(item => (
              <div key={item.id} style={{
                padding: '6px 8px', background: '#fff', borderRadius: 4,
                border: '1px solid #f0e0c0', fontSize: 11,
              }}>
                <div style={{ fontWeight: 500, color: '#333', marginBottom: 2 }}>
                  {item.action_label || item.tool} → {item.concept_label}
                </div>
                {item.params && Object.keys(item.params).length > 0 && (
                  <div style={{ color: '#8c8c8c', fontSize: 10, marginBottom: 4 }}>
                    {Object.entries(item.params).slice(0, 3).map(([k, v]) => (
                      <span key={k} style={{ marginRight: 8 }}>{k}: {String(v)}</span>
                    ))}
                  </div>
                )}
                <div style={{ display: 'flex', gap: 4, marginTop: 2 }}>
                  <Button size="small" type="primary" ghost
                    style={{ fontSize: 10, padding: '0 8px', height: 22 }}
                    onClick={() => handleApprove(item.id)}>通过</Button>
                  <Button size="small" danger ghost
                    style={{ fontSize: 10, padding: '0 8px', height: 22 }}
                    onClick={() => handleReject(item.id)}>拒绝</Button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

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
