import React, { useState } from 'react';
import { Spin } from 'antd';
import { NodeIndexOutlined, CheckCircleOutlined, CloseCircleOutlined, LoadingOutlined, DownOutlined, RightOutlined } from '@ant-design/icons';

const statusIcon = (status) => {
  switch (status) {
    case 'done': return <CheckCircleOutlined style={{ color: '#52c41a' }} />;
    case 'error': return <CloseCircleOutlined style={{ color: '#ff4d4f' }} />;
    case 'running': return <LoadingOutlined style={{ color: '#6c5ce7' }} />;
    default: return null;
  }
};

const stepStatus = (status) => {
  switch (status) {
    case 'done': return 'finish';
    case 'error': return 'error';
    case 'running': return 'process';
    default: return 'wait';
  }
};

function ChainProgress({ chainName, chainSteps, isChainMode, isChainComplete, isDynamic }) {
  const [expandedStep, setExpandedStep] = useState(null);
  const hasSteps = chainSteps && chainSteps.length > 0;
  if (!hasSteps && !isDynamic && !isChainMode) return null;

  const doneCount = hasSteps ? chainSteps.filter(s => s.status === 'done').length : 0;
  const totalCount = hasSteps ? chainSteps.length : (isDynamic ? '?' : 0);

  return (
    <div style={{
      background: 'linear-gradient(135deg, #f0f5ff 0%, #f5f3ff 100%)',
      border: '1px solid rgba(108, 92, 231, 0.15)',
      borderRadius: '10px',
      marginBottom: '8px',
      padding: '12px 16px',
      width: '100%',
      maxWidth: '100%',
      boxSizing: 'border-box',
      overflow: 'hidden',
    }}>
      <div style={{
        display: 'flex',
        alignItems: 'center',
        gap: '6px',
        marginBottom: '12px',
        fontSize: '13px',
        fontWeight: 500,
        color: '#6c5ce7',
      }}>
        <NodeIndexOutlined style={{ fontSize: '14px' }} />
        <span>{chainName || '提示链'}</span>
        {isChainMode && <Spin size="small" />}
        {isChainComplete && (
          <span style={{ fontSize: '11px', color: '#52c41a', marginLeft: 'auto' }}>
            {isDynamic ? `${doneCount} 步完成` : `${doneCount}/${totalCount} 步完成`}
          </span>
        )}
      </div>
      <div style={{
        display: 'flex',
        flexWrap: 'wrap',
        gap: '8px',
      }}>
        {chainSteps.map((step, idx) => {
          const isRunning = step.status === 'running';
          const isDone = step.status === 'done';
          const isError = step.status === 'error';
          const isExpanded = expandedStep === (step.step_id || idx);
          const borderColor = isExpanded ? '#6c5ce7' : (isDone ? '#52c41a' : isError ? '#ff4d4f' : isRunning ? '#6c5ce7' : '#e8e8e8');
          const bg = isExpanded ? '#f5f3ff' : (isDone ? '#f6ffed' : isError ? '#fff2f0' : isRunning ? '#f5f3ff' : '#fafafa');
          return (
            <div key={step.step_id || idx}
              onClick={() => setExpandedStep(isExpanded ? null : (step.step_id || idx))}
              style={{
                border: `1px solid ${borderColor}`,
                borderRadius: '6px',
                padding: '6px 8px',
                background: bg,
                width: '140px',
                height: '48px',
                cursor: 'pointer',
                overflow: 'hidden',
              }}>
              <div style={{
                fontSize: '11px',
                fontWeight: isRunning ? 600 : 400,
                color: isDone ? '#52c41a' : isError ? '#ff4d4f' : isRunning ? '#6c5ce7' : '#999',
                display: 'flex',
                alignItems: 'center',
                gap: '4px',
                whiteSpace: 'nowrap',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
              }}>
                {statusIcon(step.status)}
                <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis' }}>{step.description || step.step_id}</span>
                {isExpanded ? <DownOutlined style={{ fontSize: 10, flexShrink: 0 }} /> : <RightOutlined style={{ fontSize: 10, flexShrink: 0 }} />}
              </div>
              <div style={{
                fontSize: '10px',
                color: isError ? '#ff4d4f' : isDone ? '#52c41a' : isRunning ? '#6c5ce7' : '#bbb',
                whiteSpace: 'nowrap',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                marginTop: '2px',
              }}>
                {isError ? (step.error || '失败') :
                 isRunning ? '执行中...' :
                 isDone ? (step.agent_display_name || '') :
                 (step.agent_display_name || '')}
              </div>
            </div>
          );
        })}
      </div>
      {expandedStep != null && (() => {
        const step = chainSteps.find(s => (s.step_id || '') === expandedStep) ||
                     chainSteps[Number(expandedStep)];
        if (!step) return null;
        return (
          <div style={{
            marginTop: '8px', padding: '10px 12px',
            background: '#f9f9fb', borderRadius: '6px',
            border: '1px solid #e8e8ec', fontSize: '12px',
            color: '#555', lineHeight: 1.8,
          }}>
            <div><b>步骤描述：</b>{step.description || step.step_id}</div>
            {step.concept && <div><b>关联概念：</b>{step.concept}</div>}
            <div><b>状态：</b>{step.status === 'done' ? '已完成' : step.status === 'error' ? '失败' : step.status === 'running' ? '执行中' : '等待中'}</div>
            {step.status === 'error' && step.error && <div style={{ color: '#ff4d4f' }}><b>错误信息：</b>{step.error}</div>}
            {step.phase && <div><b>阶段：</b>{step.phase}</div>}
          </div>
        );
      })()}
    </div>
  );
}

export default ChainProgress;
