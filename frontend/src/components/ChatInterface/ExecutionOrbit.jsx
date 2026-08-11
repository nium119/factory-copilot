import React, { useMemo, useState } from 'react';
import {
  CheckCircleOutlined, CloseCircleOutlined, LoadingOutlined, BulbOutlined,
  ThunderboltOutlined, SearchOutlined, ToolOutlined, TeamOutlined,
  BranchesOutlined, DownOutlined, RightOutlined,
} from '@ant-design/icons';
import MarkdownRenderer from '../MarkdownRenderer';
import './ExecutionOrbit.css';

// 类型 → 元信息（图标 + 中文标识）
const TYPE_META = {
  thinking: { icon: <BranchesOutlined />, label: '思考' },
  plan: { icon: <ThunderboltOutlined />, label: '规划' },
  tool: { icon: <ToolOutlined />, label: '工具' },
  chain: { icon: <SearchOutlined />, label: '查询' },
  reflect: { icon: <BulbOutlined />, label: '反思' },
  collab: { icon: <TeamOutlined />, label: '协作' },
  exec: { icon: <BranchesOutlined />, label: '执行' },
};

// 状态 → 颜色 + 文字（亮色主题风格）
const STATUS_META = {
  running: { color: '#6c5ce7', label: '执行中' },
  done: { color: '#52c41a', label: '完成' },
  error: { color: '#ff4d4f', label: '失败' },
  pending: { color: '#bbb', label: '等待中' },
  reflect: { color: '#faad14', label: '反思' },
};

// 辅助：把 item 的分散执行信息统一成一条时间线事件
function collectEvents(item) {
  const list = [];

  if (item.thinkingContent) {
    list.push({
      id: 'thinking', type: 'thinking',
      status: item.thinking ? 'running' : 'done',
      label: item.thinking ? '正在思考' : '已完成思考',
      detail: item.thinkingContent,
    });
  }

  (item.planSteps || []).forEach((s, i) => {
    list.push({
      id: `plan_${i}`, type: 'plan',
      status: s.status === 'success' ? 'done' : s.status === 'failed' ? 'error' : s.status === 'running' ? 'running' : 'pending',
      label: s.name || `步骤 ${i + 1}`,
      detail: s.data,
    });
  });

  (item.toolCalls || []).forEach((tc, i) => {
    list.push({
      id: `tool_${i}`, type: 'tool',
      status: tc.status === 'executing' ? 'running' : tc.status === 'error' ? 'error' : 'done',
      label: tc.name || tc.functionName || `工具 ${i + 1}`,
      detail: tc.arguments ? (typeof tc.arguments === 'string' ? tc.arguments : JSON.stringify(tc.arguments)) : (tc.result || tc.error || ''),
    });
  });

  (item.chainSteps || []).forEach((s, i) => {
    const isThink = s.status === 'think';
    list.push({
      id: `chain_${s.step_id || i}`, type: isThink ? 'reflect' : 'chain',
      status: isThink ? 'reflect'
        : s.status === 'done' ? 'done'
        : s.status === 'error' ? 'error'
        : s.status === 'running' ? 'running' : 'pending',
      label: s.concept_label || s.description || s.step_id || `步骤 ${i + 1}`,
      detail: s.output_preview || s.error || (isThink ? s.description : ''),
    });
  });

  (item.collabAgents || []).forEach((a) => {
    list.push({
      id: `collab_${a.name}`, type: 'collab',
      status: a.status === 'success' ? 'done'
        : (a.status === 'timeout' || a.status === 'error') ? 'error'
        : a.status === 'running' ? 'running'
        : a.status === 'empty' ? 'done' : 'pending',
      label: a.display_name || a.name,
      detail: a.data || a.error || '',
    });
  });

  if (item.reflectionReason) {
    list.push({
      id: 'reflection', type: 'reflect',
      status: item.isReflectionActive ? 'running' : 'done',
      label: item.isReflectionActive ? '正在自我修正' : '已自我修正',
      detail: item.reflectionReason,
    });
  }

  (item.executionSteps || []).forEach((s, i) => {
    list.push({
      id: `exec_${i}`, type: 'exec',
      status: (s.status === 'success' || s.status === 'done') ? 'done'
        : (s.status === 'error' || s.status === 'failed') ? 'error'
        : s.status === 'running' ? 'running' : 'pending',
      label: s.label || s.name || s.key || `步骤 ${i + 1}`,
      detail: s.detail || s.output || s.error || '',
    });
  });

  // 空详情且非运行/错误的节点不展示，减少噪音
  return list.filter(e => e.detail || e.status === 'running' || e.status === 'error');
}

function ExecutionOrbit({ item, isStreaming }) {
  const [expanded, setExpanded] = useState(null);

  const events = useMemo(() => collectEvents(item), [item]);
  if (!events.length) return null;

  const doneCount = events.filter(e => e.status === 'done').length;

  return (
    <div className="orbit" style={{ margin: '10px 0' }}>
      <div className="orbit-header">
        <ThunderboltOutlined className="orbit-header-icon" />
        <span className="orbit-title">执行轨道</span>
        {isStreaming && events.some(e => e.status === 'running') && (
          <LoadingOutlined className="orbit-spin" />
        )}
        <span className="orbit-count">{doneCount}/{events.length} 完成</span>
      </div>
      <div className="orbit-track">
        {events.map((ev, idx) => {
          const tMeta = TYPE_META[ev.type] || TYPE_META.chain;
          const sMeta = STATUS_META[ev.status] || STATUS_META.pending;
          const isOpen = expanded === ev.id;
          const isRunning = ev.status === 'running';
          return (
            <div key={ev.id} className={`orbit-node orbit-node--${ev.status}`}>
              <div className="orbit-rail">
                <div className="orbit-dot" style={{ color: sMeta.color, borderColor: sMeta.color, boxShadow: isRunning ? `0 0 6px 1px ${sMeta.color}55` : 'none' }}>
                  {isRunning ? <LoadingOutlined /> : tMeta.icon}
                </div>
                {idx < events.length - 1 && (
                  <div className={`orbit-line${isRunning ? ' orbit-line--active' : ''}`} />
                )}
              </div>
              <div
                className="orbit-body"
                onClick={() => setExpanded(isOpen ? null : ev.id)}
                style={{ cursor: ev.detail ? 'pointer' : 'default' }}
              >
                <div className="orbit-row">
                  <span className="orbit-type">{tMeta.label}</span>
                  <span className="orbit-label" style={{ color: isRunning ? sMeta.color : undefined }}>{ev.label}</span>
                  <span className="orbit-status" style={{ color: sMeta.color }}>{sMeta.label}</span>
                  {ev.detail && (isOpen ? <DownOutlined className="orbit-arrow" /> : <RightOutlined className="orbit-arrow" />)}
                </div>
                {isOpen && ev.detail && (
                  <div className="orbit-detail">
                    <MarkdownRenderer content={typeof ev.detail === 'string' ? ev.detail : JSON.stringify(ev.detail, null, 2)} />
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default ExecutionOrbit;
