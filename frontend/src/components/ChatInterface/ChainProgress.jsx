import React from 'react';
import { Spin } from 'antd';
import { NodeIndexOutlined, CheckCircleOutlined, CloseCircleOutlined, LoadingOutlined } from '@ant-design/icons';

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
        display: 'grid',
        gridTemplateColumns: 'repeat(5, 1fr)',
        gap: '8px',
      }}>
        {chainSteps.map((step, idx) => {
          const isRunning = step.status === 'running';
          const isDone = step.status === 'done';
          const isError = step.status === 'error';
          const borderColor = isDone ? '#52c41a' : isError ? '#ff4d4f' : isRunning ? '#6c5ce7' : '#e8e8e8';
          const bg = isDone ? '#f6ffed' : isError ? '#fff2f0' : isRunning ? '#f5f3ff' : '#fafafa';
          return (
            <div key={step.step_id || idx} style={{
              border: `1px solid ${borderColor}`,
              borderRadius: '6px',
              padding: '6px 8px',
              background: bg,
              minWidth: 0,
              overflow: 'hidden',
            }}>
              <div style={{
                fontSize: '11px',
                fontWeight: isRunning ? 600 : 400,
                color: isDone ? '#52c41a' : isError ? '#ff4d4f' : isRunning ? '#6c5ce7' : '#999',
                whiteSpace: 'nowrap',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                display: 'flex',
                alignItems: 'center',
                gap: '4px',
              }}>
                {statusIcon(step.status)}
                <span>{step.description || step.step_id}</span>
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
                 isRunning ? `执行中...` :
                 isDone ? (step.agent_display_name || '') :
                 (step.agent_display_name || '')}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default ChainProgress;
