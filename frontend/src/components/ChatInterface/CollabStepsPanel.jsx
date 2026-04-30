import React from 'react';
import { Spin } from 'antd';
import { ThunderboltOutlined, CheckCircleOutlined, ClockCircleOutlined, CloseCircleOutlined, LoadingOutlined } from '@ant-design/icons';
import MarkdownRenderer from '../MarkdownRenderer';

const STATUS_META = {
  success: { Icon: CheckCircleOutlined, color: '#52c41a', border: 'rgba(0,184,148,0.3)', text: '查询完成' },
  timeout: { Icon: ClockCircleOutlined, color: '#faad14', border: 'rgba(255,165,0,0.3)',   text: '超时' },
  error:   { Icon: CloseCircleOutlined, color: '#ff4d4f', border: 'rgba(255,77,79,0.3)',   text: '执行失败' },
  running: { Icon: LoadingOutlined,    color: '#6c5ce7', border: 'rgba(108,92,231,0.3)',   text: '查询中...' },
  pending: { Icon: null,              color: '#bbb',    border: 'rgba(0,0,0,0.08)',        text: '等待中' },
  empty:   { Icon: null,              color: '#bbb',    border: 'rgba(0,0,0,0.08)',        text: '无匹配数据' },
};

export default function CollabStepsPanel({ collabAgents, isCollabMode }) {
  const [selectedIdx, setSelectedIdx] = React.useState(null);

  const doneCount = collabAgents.filter(a => a.status === 'success').length;

  return (
    <div style={{
      background: 'linear-gradient(135deg, #f8f7ff 0%, #f5f3ff 100%)',
      border: '1px solid rgba(108, 92, 231, 0.12)',
      borderRadius: '10px',
      marginBottom: '8px',
      padding: '12px 16px',
      width: '100%',
      maxWidth: '100%',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '10px', fontSize: '13px', fontWeight: 500, color: '#6c5ce7' }}>
        <ThunderboltOutlined style={{ fontSize: '14px' }} />
        <span>协作查询</span>
        {isCollabMode && <Spin size="small" />}
        {!isCollabMode && (
          <span style={{ fontSize: '11px', color: '#52c41a', marginLeft: 'auto' }}>
            {doneCount}/{collabAgents.length} 完成
          </span>
        )}
      </div>
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))',
        gap: '8px',
      }}>
        {collabAgents.map((agent, idx) => {
          const meta = STATUS_META[agent.status] || STATUS_META.empty;
          const StatusIcon = meta.Icon;
          const isSelected = selectedIdx === idx;
          return (
            <div
              key={agent.name || idx}
              onClick={() => setSelectedIdx(isSelected ? null : idx)}
              title={agent.error || ''}
              style={{
                cursor: 'pointer',
                background: '#fff',
                borderRadius: '8px',
                border: `1px solid ${isSelected ? '#6c5ce7' : meta.border}`,
                boxShadow: isSelected ? '0 0 0 2px rgba(108,92,231,0.2)' : 'none',
                padding: '8px 10px',
                fontSize: '12px',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: '4px', marginBottom: '4px' }}>
                {StatusIcon && <StatusIcon style={{ color: meta.color, fontSize: '14px' }} />}
                {!StatusIcon && agent.status === 'pending' && (
                  <span style={{ width: '14px', height: '14px', borderRadius: '50%', background: '#d9d9d9', display: 'inline-block' }} />
                )}
                <span style={{
                  fontWeight: 500,
                  color: agent.status === 'error' ? '#ff4d4f' : agent.status === 'timeout' ? '#faad14' : '#333',
                }}>
                  {agent.display_name || agent.name}
                </span>
                {agent.priority === 'high' && (
                  <span style={{ fontSize: '10px', padding: '0 4px', borderRadius: '3px', background: '#fff1f0', color: '#cf1322', border: '1px solid #ffa39e', flexShrink: 0 }}>紧急</span>
                )}
                {agent.priority === 'medium' && (
                  <span style={{ fontSize: '10px', padding: '0 4px', borderRadius: '3px', background: '#fff7e6', color: '#d46b08', border: '1px solid #ffd591', flexShrink: 0 }}>重要</span>
                )}
                {agent.elapsed > 0 && (
                  <span style={{ marginLeft: 'auto', fontSize: '10px', color: '#bbb' }}>
                    {agent.elapsed.toFixed(1)}s
                  </span>
                )}
              </div>
              <div style={{ color: '#999', fontSize: '11px' }}>
                {agent.status === 'timeout' || agent.status === 'error' ? (agent.error || meta.text) : meta.text}
              </div>
            </div>
          );
        })}
      </div>
      {selectedIdx !== null && collabAgents[selectedIdx]?.data && (
        <div style={{
          maxWidth: '100%',
          overflow: 'hidden',
          marginTop: '12px',
          padding: '10px 12px',
          background: '#fff',
          borderRadius: '8px',
          border: '1px solid rgba(108, 92, 231, 0.08)',
          fontSize: '12px',
          lineHeight: '1.6',
          color: '#555',
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
        }}>
          <div style={{ fontSize: '12px', fontWeight: 500, color: '#6c5ce7', marginBottom: '6px' }}>
            {collabAgents[selectedIdx].display_name} 查询结果：
          </div>
          <MarkdownRenderer content={collabAgents[selectedIdx].data} streaming={false} />
        </div>
      )}
      {selectedIdx !== null && !collabAgents[selectedIdx]?.data && (
        <div style={{
          maxWidth: '100%',
          overflow: 'hidden',
          marginTop: '12px',
          padding: '8px 12px',
          background: '#fff',
          borderRadius: '8px',
          border: '1px solid rgba(108, 92, 231, 0.08)',
          fontSize: '12px',
          color: '#bbb',
        }}>
          {collabAgents[selectedIdx].status === 'timeout' ? '查询超时' :
           collabAgents[selectedIdx].status === 'error' ? (collabAgents[selectedIdx].error || '执行失败') :
           '该助手无匹配数据'}
        </div>
      )}
    </div>
  );
}
