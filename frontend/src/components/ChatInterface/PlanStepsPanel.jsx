/**
 * 任务分解步骤可视化组件
 *
 * 用于展示 Planner 动态规划的任务清单（如"工单综合检查"拆分为 6 个子任务）。
 * 视觉风格与 CollabStepsPanel 一致（紫色主题），但使用垂直布局以适应更长的任务列表。
 *
 * Props:
 *   planTitle   string  规划任务标题（如"工单综合检查"）
 *   planSteps   array   步骤数组，每项 {key, name, status, data?}
 *   isPlanMode  bool    是否正在执行中（显示 loading 动画）
 */
import React from 'react';
import { Steps, Spin } from 'antd';
import { ThunderboltOutlined, CheckOutlined, CloseOutlined } from '@ant-design/icons';
import MarkdownRenderer from '../MarkdownRenderer';

const statusIcon = (status, isActive) => {
  if (status === 'success') return <CheckOutlined style={{ color: '#52c41a' }} />;
  if (status === 'failed') return <CloseOutlined style={{ color: '#ff4d4f' }} />;
  if (isActive) return <Spin size="small" style={{ color: '#6c5ce7' }} />;
  return null;
};

function PlanStepsPanel({ planTitle, planSteps, isPlanMode }) {
  const [selectedKey, setSelectedKey] = React.useState(null);

  const selectedStep = planSteps.find(s => s.key === selectedKey);

  const stepItems = planSteps.map((step) => ({
    title: (
      <span
        style={{
          fontSize: '13px',
          fontWeight: selectedKey === step.key ? 600 : 500,
          cursor: 'pointer',
          color: selectedKey === step.key ? '#6c5ce7' : 'inherit',
        }}
        onClick={() => setSelectedKey(selectedKey === step.key ? null : step.key)}
      >
        {step.name}
      </span>
    ),
    icon: statusIcon(step.status, isPlanMode),
    description: (
      <span style={{ fontSize: '11px', color: '#999' }}>
        {step.status === 'success' ? '检查完成' : step.status === 'failed' ? '检查失败' : '检查中...'}
      </span>
    ),
  }));

  return (
    <div style={{
      background: '#f8f7ff',
      border: '1px solid rgba(108, 92, 231, 0.12)',
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
        <ThunderboltOutlined style={{ fontSize: '14px' }} />
        <span>{planTitle || '任务规划'}</span>
        {isPlanMode && <Spin size="small" />}
      </div>

      <Steps
        direction="vertical"
        current={-1}
        items={stepItems}
        size="small"
      />

      {/* 点击展开详情 */}
      {selectedKey !== null && selectedStep && (
        <div style={{
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
          <div style={{
            fontSize: '12px',
            fontWeight: 500,
            color: '#6c5ce7',
            marginBottom: '6px',
          }}>
            {selectedStep.name} 结果：
          </div>
          {selectedStep.data ? (
            (() => {
              const d = selectedStep.data;
              let text = '';
              if (typeof d === 'string') {
                text = d;
              } else if (typeof d === 'object') {
                const lines = [];
                for (const [key, val] of Object.entries(d)) {
                  if (typeof val === 'string') {
                    lines.push(`**${key}**: ${val}`);
                  } else if (typeof val === 'object' && val !== null) {
                    lines.push(`**${key}**: ${val.status || ''}`);
                    if (val.shortages) {
                      for (const s of val.shortages) {
                        lines.push(`  - ${s.name}: 需 ${s.required}, 可用 ${s.available}`);
                      }
                    }
                  }
                }
                text = lines.join('\n');
              }
              return <MarkdownRenderer content={text} streaming={false} />;
            })()
          ) : (
            <div style={{ color: '#bbb' }}>无匹配数据</div>
          )}
        </div>
      )}
    </div>
  );
}

export default PlanStepsPanel;
