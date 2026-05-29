import React from 'react';
import { Avatar, Button, Tooltip, Typography, Spin, Tag } from 'antd';
import { UserOutlined, RobotOutlined, CopyOutlined, CheckOutlined, SyncOutlined, WarningOutlined, ToolOutlined, CodeOutlined } from '@ant-design/icons';
import MarkdownRenderer from '../MarkdownRenderer';
import PlanStepsPanel from './PlanStepsPanel';
import ChainProgress from './ChainProgress';
import FeedbackBar from './FeedbackBar';
import CollabStepsPanel from './CollabStepsPanel';

function MessageItem({ item, copiedId, onCopy, onToggleThinking, onConfirmApprove, onConfirmReject }) {
  const isUser = item.role === 'user';
  const isAgent = item.role === 'agent';
  const agentInfo = item.agentInfo || null;
  const avatarColor = isUser ? '#6c5ce7' : (agentInfo?.color || '#00b894');
  const agentName = isUser ? '用户' : (agentInfo?.display_name || 'AI助手');
  const agentIcon = agentInfo?.icon || '';
  const nameColor = isUser ? '#6c5ce7' : (agentInfo?.color || '#00b894');
  const isStreaming = item.streaming === true;

  const formatTime = (timestamp) => {
    return new Date(timestamp).toLocaleTimeString('zh-CN', {
      hour: '2-digit',
      minute: '2-digit',
    });
  };

  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'flex-start',
        marginBottom: '12px',
        flexDirection: isUser ? 'row-reverse' : 'row',
      }}
    >
      <Avatar
        icon={isUser ? <UserOutlined /> : <RobotOutlined />}
        style={{
          backgroundColor: avatarColor,
          margin: isUser ? '0 0 0 12px' : '0 12px 0 0',
          flexShrink: 0,
        }}
      />
      <div
        style={{
          flex: 1,
          minWidth: 0,
          maxWidth: '100%',
          display: 'flex',
          flexDirection: 'column',
          alignItems: isUser ? 'flex-end' : 'flex-start',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', marginBottom: '4px' }}>
          <Typography.Text strong style={{ color: nameColor }}>
            {isUser ? '用户' : `${agentIcon} ${agentName}`}
          </Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: '12px', marginLeft: '8px' }}>
            {formatTime(item.timestamp)}
          </Typography.Text>
          {isAgent && item.dataSource && (
            <Tooltip title={item.dataSourceHint || (item.dataSource === 'mes' ? '来自 MES 实时数据' : '使用本地缓存数据')}>
              <span style={{
                fontSize: '10px',
                marginLeft: '8px',
                padding: '0 6px',
                borderRadius: '4px',
                background: item.dataSource === 'mes' ? 'rgba(0, 184, 148, 0.12)' : 'rgba(255, 165, 0, 0.12)',
                color: item.dataSource === 'mes' ? '#00b894' : '#e67e22',
                border: `1px solid ${item.dataSource === 'mes' ? 'rgba(0, 184, 148, 0.3)' : 'rgba(255, 165, 0, 0.3)'}`,
                cursor: 'help',
              }}>
                {item.dataSource === 'mes' ? '真实数据' : '模拟数据'}
              </span>
            </Tooltip>
          )}
        </div>

        {/* 思考过程 */}
        {item.thinkingContent && (
          <div
            style={{
              background: 'linear-gradient(135deg, #f0f0ff 0%, #f5f3ff 100%)',
              border: '1px solid rgba(108, 92, 231, 0.12)',
              borderRadius: '10px',
              marginBottom: '8px',
              overflow: 'hidden',
              maxWidth: '100%',
              width: 'fit-content',
            }}
          >
            <div
              onClick={() => onToggleThinking(item.id)}
              style={{
                padding: '8px 12px',
                display: 'flex',
                alignItems: 'center',
                cursor: 'pointer',
                userSelect: 'none',
                gap: '8px',
                color: '#666',
                fontSize: '13px',
              }}
            >
              {item.thinking ? (
                <Spin size="small" />
              ) : (
                <span style={{ color: '#52c41a', fontSize: '14px' }}>✓</span>
              )}
              <span style={{ fontWeight: 500 }}>
                {item.thinking ? '正在思考...' : '思考过程'}
              </span>
              <span style={{ marginLeft: 'auto', fontSize: '12px', color: '#999' }}>
                {(item.thinkingExpanded || item.thinking) ? '▲' : '▼'}
              </span>
            </div>
            {(item.thinkingExpanded || item.thinking) && (
              <div style={{
                padding: '8px 12px',
                fontSize: '12px',
                color: '#888',
                lineHeight: '1.8',
                borderTop: '1px solid #e8e8e8',
                wordBreak: 'break-word',
                overflowWrap: 'break-word',
              }}>
                <MarkdownRenderer content={item.thinkingContent} streaming={isStreaming} />
              </div>
            )}
          </div>
        )}

        {/* 协作过程显示 */}
        {isAgent && item.collabAgents && item.collabAgents.length > 0 && (
          <CollabStepsPanel collabAgents={item.collabAgents} isCollabMode={item.isCollabMode} />
        )}

        {/* 任务分解步骤显示 */}
        {isAgent && item.planSteps && item.planSteps.length > 0 && (
          <PlanStepsPanel
            planTitle={item.planTitle}
            planSteps={item.planSteps}
            isPlanMode={item.isPlanMode}
          />
        )}

        {/* Prompt Chaining 步骤进度 */}
        {isAgent && item.chainSteps && item.chainSteps.length > 0 && (
          <ChainProgress
            chainName={item.chainName}
            chainSteps={item.chainSteps}
            isChainMode={item.isChainMode}
            isChainComplete={item.isChainComplete}
          />
        )}

        {/* Tool Calls — 显示本体工具调用 */}
        {isAgent && item.toolCalls && item.toolCalls.length > 0 && (
          <div style={{ marginBottom: '8px', width: 'fit-content' }}>
            {item.toolCalls.map((tc) => (
              <div key={tc.id} style={{
                background: 'linear-gradient(135deg, #f8faff 0%, #eef2ff 100%)',
                border: '1px solid #d6e4ff',
                borderRadius: '10px',
                padding: '8px 14px',
                fontSize: '12px',
                display: 'flex',
                alignItems: 'center',
                gap: '10px',
                marginBottom: '6px',
                boxShadow: '0 1px 2px rgba(89, 126, 247, 0.05)',
              }}>
                <span style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  width: '26px',
                  height: '26px',
                  borderRadius: '7px',
                  background: 'linear-gradient(135deg, #597ef7 0%, #85a5ff 100%)',
                  color: '#fff',
                  fontSize: '12px',
                }}>
                  <ToolOutlined style={{ fontSize: '13px' }} />
                </span>
                <span style={{ fontWeight: 600, color: '#2f54eb', fontSize: '12px' }}>{tc.name}</span>
                {tc.arguments && (
                  <Tag style={{
                    margin: 0,
                    fontSize: '10px',
                    fontFamily: '"SF Mono", "Cascadia Code", "Fira Code", monospace',
                    maxWidth: '220px',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    background: '#fff',
                    border: '1px solid #e8e8e8',
                    borderRadius: '5px',
                    color: '#595959',
                  }}>
                    {typeof tc.arguments === 'string'
                      ? tc.arguments.substring(0, 60)
                      : JSON.stringify(tc.arguments).substring(0, 60)}
                  </Tag>
                )}
                {tc.rowCount != null && (
                  <span style={{
                    fontSize: '10px',
                    fontWeight: 600,
                    color: '#389e0d',
                    background: '#f6ffed',
                    padding: '1px 7px',
                    borderRadius: '10px',
                    border: '1px solid #b7eb8f',
                  }}>
                    {tc.rowCount} 条结果
                  </span>
                )}
                <span style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center' }}>
                  {tc.status === 'executing' ? (
                    <Spin size="small" />
                  ) : (
                    <span style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      width: '18px',
                      height: '18px',
                      borderRadius: '50%',
                      background: '#f6ffed',
                      color: '#52c41a',
                      fontSize: '10px',
                    }}>
                      <CheckOutlined style={{ fontSize: '10px' }} />
                    </span>
                  )}
                </span>
              </div>
            ))}
          </div>
        )}

        {/* Reflection 自我修正指示器 */}
        {isAgent && item.reflectionReason && (
          <div style={{
            background: '#fff7e6',
            border: '1px solid #ffd591',
            borderRadius: '8px',
            padding: '6px 12px',
            marginBottom: '8px',
            fontSize: '12px',
            color: '#ad6800',
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
            width: 'fit-content',
          }}>
            <SyncOutlined spin={item.isReflectionActive} />
            <span>
              {item.isReflectionActive ? '正在自我修正...' : `已自我修正：${item.reflectionReason}`}
            </span>
          </div>
        )}

        {/* Guardrails 护栏错误显示（区别于普通错误） */}
        {isAgent && item.isGuardrailError && (
          <div style={{
            background: '#fff1f0',
            border: '1px solid #ffccc7',
            borderRadius: '8px',
            padding: '10px 14px',
            marginBottom: '8px',
            fontSize: '13px',
            color: '#cf1322',
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
            width: 'fit-content',
          }}>
            <WarningOutlined style={{ fontSize: '16px' }} />
            <span>{item.content}</span>
          </div>
        )}

        {/* 执行链路面板 */}
        {isAgent && item.executionSteps && item.executionSteps.length > 0 && (
          <ExecutionChain steps={item.executionSteps} />
        )}

        {/* 写操作确认卡片 */}
        {isAgent && item.confirmRequired && !item.confirmResolved && (
          <ConfirmCard
            confirm={item.confirmRequired}
            onApprove={onConfirmApprove}
            onReject={onConfirmReject}
          />
        )}

        {/* 排产优化评估结果 — 由 ChatInterface 层级渲染 */}

        <div
          style={{
            background: item.isError ? '#fff2f0' : (isUser ? '#f0eeff' : '#f0fdf4'),
            border: `1px solid ${item.isError ? '#ffccc7' : (isUser ? '#d4cfff' : '#bbf7d0')}`,
            borderRadius: '8px',
            padding: '12px 16px',
            width: 'fit-content',
            maxWidth: '100%',
            overflow: 'hidden',
          }}
        >
          {/* AI正在回复时显示状态提示 */}
          {isAgent && !item.isError && (
            <>
              {item.content && <MarkdownRenderer content={item.content} streaming={isStreaming} />}
              {isStreaming && !(item.confirmRequired && !item.confirmResolved) && (
                <div style={{
                  marginTop: item.content ? '12px' : '0',
                  padding: '8px 12px',
                  background: item.content ? '#f5f5f5' : 'transparent',
                  borderRadius: '6px',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '8px',
                  color: '#999',
                  fontSize: '13px',
                }}>
                  <Spin size="small" />
                  <span>正在生成中...</span>
                </div>
              )}
              {!isStreaming && agentInfo && (
                <div style={{
                  marginTop: '8px',
                  paddingTop: '6px',
                  borderTop: `1px solid ${agentInfo.color}22`,
                  fontSize: '11px',
                  color: agentInfo.color,
                  display: 'flex',
                  alignItems: 'center',
                  gap: '4px',
                }}>
                  <span>{agentInfo.icon}</span>
                  <span>由 {agentInfo.display_name} 响应</span>
                </div>
              )}
            </>
          )}
          {isUser && (
            <p
              style={{
                margin: 0,
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
                color: item.isError ? '#ff4d4f' : 'inherit',
              }}
            >
              {item.content}
            </p>
          )}
        </div>
        {isAgent && !item.isError && (
          <Tooltip title={copiedId === item.id ? '已复制' : '复制'}>
            <Button
              type="text"
              size="small"
              icon={copiedId === item.id ? <CheckOutlined /> : <CopyOutlined />}
              onClick={() => onCopy(item.content, item.id)}
              style={{ marginTop: '4px', padding: '0 4px' }}
            />
          </Tooltip>
        )}
        {/* 反馈工具栏：仅在已完成、非错误、非停止的 Agent 消息下显示 */}
        {isAgent && !item.isError && !item.isStopped && !item.streaming && (
          <FeedbackBar messageId={item.backendId || item.id} metadata={item.metadata} agentName={agentInfo?.name} />
        )}
      </div>
    </div>
  );
}

// ── ExecutionChain 执行链路面板 ──

const STEP_META = {
  done:     { icon: '✓', color: '#52c41a', bg: '#f6ffed', border: '#b7eb8f' },
  running:  { icon: '●', color: '#1890ff', bg: '#e6f7ff', border: '#91d5ff', pulse: true },
  error:    { icon: '✗', color: '#ff4d4f', bg: '#fff2f0', border: '#ffccc7' },
  pending:  { icon: '○', color: '#d9d9d9', bg: '#fafafa', border: '#e8e8e8' },
};

const STEP_LABEL_MAP = {
  route_start: '路由分析',
  route_l2: 'L2 LLM 分类',
  route_match: '匹配工具',
  param_extract: '参数提取',
  confirm_required: '等待确认',
  confirm_result: '确认结果',
  tool_start: '工具执行',
  tool_result: '查询结果',
  format_start: 'LLM 格式化',
  execution_done: '执行完成',
  parallel_start: '多域协作',
  parallel_task: 'Agent 查询',
  parallel_done: '协作完成',
};

function ExecutionChain({ steps }) {
  const [expanded, setExpanded] = React.useState(true);
  const [hasCollapsed, setHasCollapsed] = React.useState(false);
  if (!steps || steps.length === 0) return null;

  // Auto-collapse when all steps are done and we haven't collapsed yet
  React.useEffect(() => {
    if (!hasCollapsed && steps.length > 0 && steps.every(s => s.status === 'done')) {
      const timer = setTimeout(() => {
        setExpanded(false);
        setHasCollapsed(true);
      }, 3000);
      return () => clearTimeout(timer);
    }
  }, [steps, hasCollapsed]);

  const doneCount = steps.filter(s => s.status === 'done').length;
  const runningStep = steps.find(s => s.status === 'running');

  return (
    <div style={{
      background: '#fff',
      border: '1px solid #e8e8ec',
      borderRadius: '10px',
      marginBottom: '8px',
      overflow: 'hidden',
      width: 'fit-content',
      minWidth: '300px',
      boxShadow: '0 1px 3px rgba(0,0,0,0.03)',
    }}>
      {/* Header */}
      <div
        onClick={() => setExpanded(!expanded)}
        style={{
          padding: '8px 14px',
          display: 'flex',
          alignItems: 'center',
          cursor: 'pointer',
          userSelect: 'none',
          gap: '8px',
          color: '#595959',
          fontSize: '12px',
          fontWeight: 600,
          background: expanded ? '#fafafa' : '#fff',
          transition: 'background 0.2s',
        }}
      >
        <span style={{
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
          width: '22px',
          height: '22px',
          borderRadius: '6px',
          background: runningStep
            ? 'linear-gradient(135deg, #1890ff 0%, #40a9ff 100%)'
            : '#f0f0f0',
          color: runningStep ? '#fff' : '#8c8c8c',
          fontSize: '11px',
        }}>
          <ToolOutlined style={{ fontSize: '12px' }} />
        </span>
        <span>执行链路</span>
        {runningStep && (
          <span style={{ fontSize: '10px', color: '#1890ff', fontWeight: 400 }}>
            {runningStep.label || '处理中...'}
          </span>
        )}
        <span style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: '8px' }}>
          {doneCount > 0 && (
            <span style={{
              fontSize: '10px',
              fontWeight: 500,
              color: '#52c41a',
              background: '#f6ffed',
              padding: '1px 7px',
              borderRadius: '8px',
              border: '1px solid #b7eb8f',
            }}>
              {doneCount}/{steps.length}
            </span>
          )}
          <span style={{ fontSize: '10px', color: '#bfbfbf', transition: 'transform 0.2s', transform: expanded ? 'rotate(180deg)' : 'rotate(0)' }}>
            {'▲'}
          </span>
        </span>
      </div>

      {/* Steps list with timeline bar */}
      {expanded && (
        <div style={{ padding: '6px 14px 10px', borderTop: '1px solid #f0f0f0', position: 'relative' }}>
          {steps.map((step, i) => {
            const meta = STEP_META[step.status] || STEP_META.pending;
            const isLast = i === steps.length - 1;
            return (
              <div key={i} style={{
                display: 'flex',
                alignItems: 'flex-start',
                gap: '10px',
                position: 'relative',
                paddingBottom: isLast ? '0' : '2px',
              }}>
                {/* Timeline column */}
                <div style={{
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'center',
                  width: '16px',
                  flexShrink: 0,
                  paddingTop: '4px',
                }}>
                  {/* Dot */}
                  <span style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    width: '16px',
                    height: '16px',
                    borderRadius: '50%',
                    background: meta.bg,
                    border: `2px solid ${meta.border}`,
                    color: meta.color,
                    fontSize: '9px',
                    fontWeight: 'bold',
                    lineHeight: 1,
                    flexShrink: 0,
                    animation: meta.pulse ? 'exec-pulse 1.5s ease-in-out infinite' : 'none',
                    zIndex: 1,
                  }}>
                    {meta.icon}
                  </span>
                  {/* Timeline line */}
                  {!isLast && (
                    <div style={{
                      width: '2px',
                      flex: 1,
                      minHeight: '14px',
                      background: step.status === 'done' ? '#b7eb8f' : '#f0f0f0',
                      marginTop: '2px',
                    }} />
                  )}
                </div>
                {/* Content */}
                <div style={{
                  flex: 1,
                  paddingTop: '2px',
                  fontSize: '11px',
                  lineHeight: '18px',
                }}>
                  <span style={{
                    color: meta.color,
                    fontWeight: step.status === 'running' ? 600 : 500,
                  }}>
                    {step.label || STEP_LABEL_MAP[step.key] || step.key || '处理中'}
                  </span>
                  {step.detail && (
                    <span style={{
                      color: '#8c8c8c',
                      marginLeft: '8px',
                      fontFamily: '"SF Mono", "Cascadia Code", "Fira Code", monospace',
                      fontSize: '10px',
                      wordBreak: 'break-all',
                    }}>
                      {step.detail.length > 50 ? step.detail.substring(0, 50) + '…' : step.detail}
                    </span>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      <style>{`
        @keyframes exec-pulse {
          0%, 100% { box-shadow: 0 0 0 0 rgba(24, 144, 255, 0.3); }
          50% { box-shadow: 0 0 0 4px rgba(24, 144, 255, 0.1); }
        }
      `}</style>
    </div>
  );
}

// ── ConfirmCard 确认卡片 ──

const inputFocusStyle = `
  .confirm-field-input:focus,
  .confirm-field-select:focus {
    outline: none;
    border-color: #fa8c16 !important;
    box-shadow: 0 0 0 2px rgba(250, 140, 22, 0.12);
  }
  .confirm-field-input:hover,
  .confirm-field-select:hover {
    border-color: #ffa940;
  }
`;

function isEmpty(val, type) {
    if (val === undefined || val === null) return true;
    if (type === 'int') return isNaN(val) || val === '';
    return val === '';
  }

  function ConfirmCard({ confirm, onApprove, onReject }) {
    const [submitting, setSubmitting] = React.useState(false);
    const [cancelHover, setCancelHover] = React.useState(false);
    const [errors, setErrors] = React.useState({});
    const paramSchema = confirm?.param_schema || [];
    const prefillParams = confirm?.params || {};
    const ontologyContext = confirm?.context || {};
    const [autoFilled, setAutoFilled] = React.useState(() => {
      const filled = {};
      paramSchema.forEach(p => { filled[p.name] = p.name in prefillParams; });
      return filled;
    });
    const [formValues, setFormValues] = React.useState(() => {
      const init = {};
      paramSchema.forEach(p => {
        if (p.name in prefillParams && prefillParams[p.name] != null && prefillParams[p.name] !== '') {
          init[p.name] = prefillParams[p.name];
        } else {
          init[p.name] = p.type === 'int' ? undefined : '';
        }
      });
      return init;
    });

    if (!confirm) return null;

    const setField = (name, value) => {
      setFormValues(prev => ({ ...prev, [name]: value }));
      if (autoFilled[name]) {
        setAutoFilled(prev => ({ ...prev, [name]: false }));
      }
      if (errors[name]) {
        setErrors(prev => { const n = { ...prev }; delete n[name]; return n; });
      }
    };

    const validate = () => {
      const newErrors = {};
      paramSchema.forEach(p => {
        if (p.required !== false && isEmpty(formValues[p.name], p.type)) {
          newErrors[p.name] = `${p.label || p.name} 不能为空`;
        }
      });
      setErrors(newErrors);
      return Object.keys(newErrors).length === 0;
    };

    const handleApprove = async () => {
      if (!validate()) return;
      setSubmitting(true);
      try {
        await onApprove?.(formValues);
      } finally {
        setSubmitting(false);
      }
    };

    const handleReject = async () => {
      setSubmitting(true);
      try {
        await onReject?.();
      } finally {
        setSubmitting(false);
      }
    };

    const renderField = (p) => {
      const hasError = !!errors[p.name];
      const baseStyle = {
        width: '100%',
        padding: '6px 10px',
        borderRadius: '6px',
        fontSize: '13px',
        border: `1px solid ${hasError ? '#ff4d4f' : '#e8e8e8'}`,
        background: hasError ? '#fffbfb' : '#fff',
        transition: 'border-color 0.2s, box-shadow 0.2s',
        boxSizing: 'border-box',
        color: '#333',
      };
      if (p.enumValues && p.enumValues.length > 0) {
        return (
          <select
            className="confirm-field-select"
            value={formValues[p.name] || ''}
            onChange={e => setField(p.name, e.target.value)}
            style={{ ...baseStyle, cursor: 'pointer', appearance: 'auto' }}
          >
            <option value="">-- 请选择 --</option>
            {p.enumValues.map(v => (
              <option key={v} value={v}>{v}</option>
            ))}
          </select>
        );
      }
      if (p.type === 'int') {
        return (
          <input
            className="confirm-field-input"
            type="number"
            value={formValues[p.name] ?? ''}
            onChange={e => setField(p.name, e.target.value ? parseInt(e.target.value) : undefined)}
            placeholder={p.label}
            style={baseStyle}
          />
        );
      }
      if (p.type === 'date' || p.type === 'datetime' || p.name.toLowerCase().includes('date') || p.label.includes('日期')) {
        return (
          <input
            className="confirm-field-input"
            type="date"
            value={formValues[p.name] || ''}
            onChange={e => setField(p.name, e.target.value)}
            style={{ ...baseStyle, colorScheme: 'light' }}
          />
        );
      }
      return (
        <input
          className="confirm-field-input"
          type="text"
          value={formValues[p.name] || ''}
          onChange={e => setField(p.name, e.target.value)}
          placeholder={p.label}
          style={baseStyle}
        />
      );
    };

  const formatContextValue = (entity) => {
    if (!entity) return '';
    const name = entity.name || entity.id || '';
    const extra = [];
    if (entity.status) extra.push(entity.status);
    if (entity.quantity) extra.push(`x${entity.quantity}件`);
    if (entity.price) extra.push(`¥${entity.price}`);
    return extra.length > 0 ? `${name} (${extra.join(', ')})` : name;
  };

  const hasContext = Object.keys(ontologyContext).length > 0;

  return (
    <div style={{
      background: '#fffbf0',
      border: '1px solid #ffd591',
      borderRadius: '12px',
      marginBottom: '8px',
      width: 'fit-content',
      minWidth: '340px',
      maxWidth: '440px',
      overflow: 'hidden',
      boxShadow: '0 1px 4px rgba(250, 140, 22, 0.06)',
    }}>
      <style>{inputFocusStyle}</style>

      {/* Header */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        gap: '8px',
        padding: '12px 16px',
        background: 'linear-gradient(135deg, #fff7e6 0%, #fff3d9 100%)',
        borderBottom: '1px solid #ffe7ba',
      }}>
        <span style={{
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
          width: '22px',
          height: '22px',
          borderRadius: '50%',
          background: '#fa8c16',
          color: '#fff',
          fontSize: '12px',
        }}>
          <WarningOutlined style={{ fontSize: '11px' }} />
        </span>
        <div>
          <div style={{ fontSize: '13px', fontWeight: 700, color: '#ad4e00', lineHeight: 1.3 }}>
            操作确认
          </div>
          <div style={{ fontSize: '11px', color: '#d48806', marginTop: '1px' }}>
            {confirm.action_label}
          </div>
        </div>
        <Tag style={{ marginLeft: 'auto', fontSize: '10px', border: 'none', background: '#fff1cc', color: '#ad6800' }}>
          需确认
        </Tag>
      </div>

      <div style={{ padding: '14px 16px' }}>

        {/* Ontology context */}
        {hasContext && (
          <div style={{
            background: '#f6ffed',
            border: '1px solid #b7eb8f',
            borderRadius: '8px',
            padding: '10px 12px',
            marginBottom: '14px',
            fontSize: '12px',
            color: '#389e0d',
          }}>
            <div style={{ fontWeight: 600, marginBottom: '6px', fontSize: '12px', color: '#237804' }}>
              已识别以下关联信息：
            </div>
            {Object.entries(ontologyContext).map(([key, ctxValue]) => {
              const entity = ctxValue.entity || ctxValue;
              const label = ctxValue.label || key;
              return (
                <div key={key} style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '8px',
                  padding: '3px 0',
                }}>
                  <span style={{
                    display: 'inline-block',
                    minWidth: label.length <= 4 ? '52px' : 'auto',
                    fontSize: '11px',
                    fontWeight: 500,
                    color: '#52c41a',
                    background: '#f0fff0',
                    padding: '1px 6px',
                    borderRadius: '4px',
                    textAlign: 'center',
                  }}>{label}</span>
                  <span style={{ color: '#135200' }}>{formatContextValue(entity)}</span>
                </div>
              );
            })}
          </div>
        )}

        {/* Form fields */}
        {paramSchema.length > 0 && (
          <div style={{ marginBottom: '16px' }}>
            <div style={{
              fontSize: '12px',
              fontWeight: 600,
              color: '#595959',
              marginBottom: '10px',
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
            }}>
              <span style={{
                display: 'inline-block',
                width: '3px',
                height: '14px',
                borderRadius: '2px',
                background: '#fa8c16',
              }} />
              填写参数
            </div>
            {paramSchema.map(p => {
              const hasError = !!errors[p.name];
              return (
                <div key={p.name} style={{ marginBottom: '12px' }}>
                  <div style={{
                    display: 'flex',
                    alignItems: 'center',
                    marginBottom: '4px',
                    fontSize: '12px',
                    color: hasError ? '#ff4d4f' : '#8c8c8c',
                    fontWeight: 500,
                  }}>
                    {p.label || p.name}
                    {p.required !== false && (
                      <span style={{ color: '#ff4d4f', marginLeft: '2px' }}>*</span>
                    )}
                    {autoFilled[p.name] && (
                      <span style={{
                        marginLeft: '8px',
                        fontSize: '10px',
                        padding: '0 5px',
                        borderRadius: '4px',
                        background: '#e6f7ff',
                        color: '#1890ff',
                        fontWeight: 400,
                      }}>已识别</span>
                    )}
                    {p.description && (
                      <Tooltip title={p.description}>
                        <span style={{
                          marginLeft: '4px',
                          cursor: 'help',
                          color: '#bfbfbf',
                          fontSize: '11px',
                        }}>?</span>
                      </Tooltip>
                    )}
                  </div>
                  {renderField(p)}
                  {errors[p.name] && (
                    <div style={{
                      fontSize: '11px',
                      color: '#ff4d4f',
                      marginTop: '3px',
                      display: 'flex',
                      alignItems: 'center',
                      gap: '4px',
                    }}>
                      <span style={{
                        display: 'inline-block',
                        width: '5px',
                        height: '5px',
                        borderRadius: '50%',
                        background: '#ff4d4f',
                      }} />
                      {errors[p.name]}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}

        {/* No params fallback */}
        {paramSchema.length === 0 && (
          <div style={{
            fontSize: '13px',
            color: '#8c8c8c',
            marginBottom: '14px',
            padding: '10px 12px',
            background: '#fffbe6',
            borderRadius: '6px',
            border: '1px dashed #ffe58f',
          }}>
            <WarningOutlined style={{ color: '#faad14', marginRight: '6px' }} />
            此操作将修改数据库，是否继续？
          </div>
        )}

        {/* Actions */}
        <div style={{
          display: 'flex',
          gap: '10px',
          justifyContent: 'flex-end',
          paddingTop: hasContext || paramSchema.length > 0 ? '4px' : '0',
          borderTop: hasContext || paramSchema.length > 0 ? '1px solid #f5f5f5' : 'none',
        }}>
          <button
            onClick={handleReject}
            disabled={submitting}
            onMouseEnter={() => !submitting && setCancelHover(true)}
            onMouseLeave={() => setCancelHover(false)}
            style={{
              padding: '6px 18px',
              borderRadius: '6px',
              border: `1px solid ${cancelHover ? '#fa8c16' : '#d9d9d9'}`,
              background: '#fff',
              color: cancelHover ? '#fa8c16' : '#666',
              fontSize: '13px',
              cursor: submitting ? 'not-allowed' : 'pointer',
              fontWeight: 500,
              transition: 'all 0.2s',
            }}
          >
            取消
          </button>
          <button
            onClick={handleApprove}
            disabled={submitting}
            style={{
              padding: '6px 20px',
              borderRadius: '6px',
              border: 'none',
              background: submitting
                ? '#ffc069'
                : 'linear-gradient(135deg, #fa8c16 0%, #faad14 100%)',
              color: '#fff',
              fontSize: '13px',
              cursor: submitting ? 'not-allowed' : 'pointer',
              fontWeight: 600,
              transition: 'all 0.2s',
              boxShadow: submitting ? 'none' : '0 2px 4px rgba(250, 140, 22, 0.3)',
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
            }}
            onMouseEnter={e => {
              if (!submitting) {
                e.target.style.transform = 'translateY(-1px)';
                e.target.style.boxShadow = '0 3px 8px rgba(250, 140, 22, 0.4)';
              }
            }}
            onMouseLeave={e => {
              e.target.style.transform = 'translateY(0)';
              e.target.style.boxShadow = '0 2px 4px rgba(250, 140, 22, 0.3)';
            }}
          >
            {submitting ? (
              <>
                <Spin size="small" style={{ color: '#fff' }} />
                处理中...
              </>
            ) : (
              '确认执行'
            )}
          </button>
        </div>
      </div>
    </div>
  );
}

export default MessageItem;
