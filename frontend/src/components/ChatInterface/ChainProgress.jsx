import React from 'react';
import { Steps, Spin } from 'antd';
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

function ChainProgress({ chainName, chainSteps, isChainMode, isChainComplete }) {
  if (!chainSteps || chainSteps.length === 0) return null;

  const currentIdx = chainSteps.findIndex(s => s.status === 'running');
  const doneCount = chainSteps.filter(s => s.status === 'done').length;

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
            {doneCount}/{chainSteps.length} 步完成
          </span>
        )}
      </div>
      <div>
        <Steps
          direction="horizontal"
          size="small"
          current={currentIdx >= 0 ? currentIdx : chainSteps.length}
          items={chainSteps.map((step) => ({
            title: (
              <span style={{
                fontSize: '12px',
                fontWeight: step.status === 'running' ? 600 : 400,
                color: step.status === 'error' ? '#ff4d4f' : step.status === 'done' ? '#52c41a' : '#999',
              }}>
                {statusIcon(step.status)}
                <span style={{ marginLeft: '4px' }}>{step.description || step.step_id}</span>
              </span>
            ),
            description: step.status === 'error' ? (
              <span style={{ fontSize: '10px', color: '#ff4d4f' }}>
                {step.error || '执行失败'}
              </span>
            ) : step.status === 'running' ? (
              <span style={{ fontSize: '10px', color: '#6c5ce7' }}>
                由 {step.agent_name} 执行中...
              </span>
            ) : step.status === 'done' ? (
              <span style={{ fontSize: '10px', color: '#52c41a' }}>
                {step.agent_name} 已完成
              </span>
            ) : (
              <span style={{ fontSize: '10px', color: '#bbb' }}>
                {step.agent_name}
              </span>
            ),
            status: stepStatus(step.status),
          }))}
        />
      </div>
    </div>
  );
}

export default ChainProgress;
