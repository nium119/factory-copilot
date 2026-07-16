import React from 'react';
import { Avatar, Button, Tooltip, Typography, Spin, Tag, Dropdown, message } from 'antd';
import { UserOutlined, RobotOutlined, CopyOutlined, CheckOutlined, SyncOutlined, WarningOutlined, ToolOutlined, CodeOutlined, CheckCircleFilled, CloseCircleFilled, ClockCircleFilled, ThunderboltOutlined, FilterOutlined, ExportOutlined } from '@ant-design/icons';
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
        {isAgent && (item.isDynamic || (item.chainSteps && item.chainSteps.length > 0)) && (
          <ChainProgress
            chainName={item.chainName}
            chainSteps={item.chainSteps}
            isChainMode={item.isChainMode}
            isChainComplete={item.isChainComplete}
            isDynamic={item.isDynamic}
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

        {/* 执行链路面板 — 有横向步骤/动态规划时不显示，避免重复 */}
        {isAgent && item.executionSteps && item.executionSteps.length > 0
          && !item.isChainMode && !item.isDynamic
          && !(item.chainSteps && item.chainSteps.length > 0) && (
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
              {/* 报告类型消息：行内导出链接 */}
              {isAgent && !isStreaming && item.content && (item.message_type === 'report' || item.messageType === 'report') && (
                <div style={{
                  marginTop: '10px', paddingTop: '8px',
                  borderTop: '1px solid #f0f0f0',
                  fontSize: '12px', color: '#bbb',
                  display: 'flex', alignItems: 'center', gap: '4px',
                }}>
                  <ExportOutlined style={{ fontSize: '12px' }} />
                  <span>导出</span>
                  <a onClick={() => {
                    const msgId = item.backendId || item.id;
                    if (msgId) window.open(`/api/messages/reports/${msgId}/export?format=pdf`, '_blank');
                  }} style={{ color: '#6c5ce7', cursor: 'pointer', marginLeft: '2px' }}>PDF</a>
                  <span>·</span>
                  <a onClick={() => {
                    const msgId = item.backendId || item.id;
                    if (msgId) {
                      const a = document.createElement('a');
                      a.href = `/api/messages/reports/${msgId}/export?format=docx`;
                      a.download = '';
                      document.body.appendChild(a);
                      a.click();
                      document.body.removeChild(a);
                      message.success('正在下载 Word 文件');
                    }
                  }} style={{ color: '#6c5ce7', cursor: 'pointer' }}>Word</a>
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
  done:     { Icon: CheckCircleFilled, color: '#52c41a', bg: '#f6ffed', shadow: '0 0 0 2px rgba(82, 196, 26, 0.12)' },
  running:  { Icon: ThunderboltOutlined, color: '#1677ff', bg: '#e6f4ff', shadow: '0 0 0 3px rgba(22, 119, 255, 0.18)', pulse: true },
  error:    { Icon: CloseCircleFilled, color: '#ff4d4f', bg: '#fff2f0', shadow: '0 0 0 2px rgba(255, 77, 79, 0.12)' },
  pending:  { Icon: ClockCircleFilled, color: '#d9d9d9', bg: '#fafafa', shadow: 'none' },
};

const STEP_LABEL_MAP = {
  route_start: '路由分析',
  route_l2: '意图识别',
  route_match: '匹配工具',
  param_extract: '参数提取',
  filter_applied: '数据过滤',
  confirm_required: '等待确认',
  confirm_result: '确认结果',
  confirm_delegated: '委托审批',
  tool_start: '工具执行',
  tool_result: '查询结果',
  route_agent_fallback: 'Cypher 兜底',
  cypher_generation: 'Cypher 生成',
  format_start: 'LLM 格式化',
  execution_done: '执行完成',
  parallel_start: '多域协作',
  parallel_task: 'Agent 查询',
  parallel_done: '协作完成',
};

function ExecutionChain({ steps }) {
  const [expanded, setExpanded] = React.useState(true);
  const [hasCollapsed, setHasCollapsed] = React.useState(false);
  const [selectedIndex, setSelectedIndex] = React.useState(null);
  if (!steps || steps.length === 0) return null;

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
  const selectedStep = selectedIndex != null ? steps[selectedIndex] : null;
  const allDone = doneCount === steps.length;

  const formatDetailValue = (raw) => {
    if (!raw) return null;
    try {
      const parsed = JSON.parse(raw);
      if (typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed)) {
        return (
          <table style={{ width: '100%', fontSize: '12px', borderCollapse: 'collapse' }}>
            <tbody>
              {Object.entries(parsed).map(([k, v]) => (
                <tr key={k} style={{ borderBottom: '1px solid #f0f0f0' }}>
                  <td style={{ padding: '5px 10px', color: '#8c8c8c', fontWeight: 500, whiteSpace: 'nowrap', verticalAlign: 'top', width: '40%' }}>{k}</td>
                  <td style={{ padding: '5px 10px', color: '#262626', wordBreak: 'break-all', fontSize: '12px' }}>{typeof v === 'object' ? JSON.stringify(v) : String(v)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        );
      }
      return <span style={{ fontFamily: '"SF Mono", "Cascadia Code", "Fira Code", monospace', fontSize: '12px', color: '#434343' }}>{raw}</span>;
    } catch {
      return <span style={{ color: '#434343', fontSize: '12px' }}>{raw}</span>;
    }
  };

  const getStepLabel = (step) => step.label || STEP_LABEL_MAP[step.key] || step.key || '处理中';

  return (
    <div style={{ display: 'flex', gap: '10px', alignItems: 'flex-start', marginBottom: '8px' }}>
      {/* ── Main panel ── */}
      <div style={{
        background: '#fff',
        border: '1px solid #e8e8ec',
        borderRadius: '10px',
        overflow: 'hidden',
        width: selectedStep ? '320px' : 'fit-content',
        minWidth: '280px',
        maxWidth: selectedStep ? '320px' : '420px',
        boxShadow: '0 1px 4px rgba(0,0,0,0.04)',
        transition: 'width 0.25s ease, box-shadow 0.2s',
      }}>
        {/* Header */}
        <div
          onClick={() => setExpanded(!expanded)}
          style={{
            padding: '8px 12px',
            display: 'flex',
            alignItems: 'center',
            cursor: 'pointer',
            userSelect: 'none',
            gap: '8px',
            color: '#262626',
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
            width: '24px',
            height: '24px',
            borderRadius: '7px',
            background: allDone
              ? 'linear-gradient(135deg, #52c41a 0%, #73d13d 100%)'
              : runningStep
                ? 'linear-gradient(135deg, #1677ff 0%, #4096ff 100%)'
                : '#f5f5f5',
            color: (allDone || runningStep) ? '#fff' : '#8c8c8c',
            fontSize: '12px',
            flexShrink: 0,
            transition: 'all 0.35s ease',
          }}>
            {allDone ? <CheckCircleFilled style={{ fontSize: '13px' }} /> : <ThunderboltOutlined style={{ fontSize: '13px' }} />}
          </span>
          <span style={{ fontSize: '13px', letterSpacing: '0.3px' }}>执行链路</span>
          {runningStep && (
            <span style={{
              fontSize: '11px',
              color: '#1677ff',
              fontWeight: 400,
              background: '#e6f4ff',
              padding: '0 6px',
              borderRadius: '4px',
              lineHeight: '18px',
            }}>
              {getStepLabel(runningStep)}
            </span>
          )}
          <span style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: '6px' }}>
            <span style={{
              fontSize: '11px',
              fontWeight: 600,
              color: allDone ? '#52c41a' : '#8c8c8c',
              background: allDone ? '#f6ffed' : '#f5f5f5',
              padding: '1px 8px',
              borderRadius: '10px',
              border: `1px solid ${allDone ? '#b7eb8f' : '#e8e8e8'}`,
              letterSpacing: '0.2px',
            }}>
              {doneCount}/{steps.length}
            </span>
            <span style={{
              fontSize: '9px',
              color: '#bfbfbf',
              transition: 'transform 0.25s ease',
              transform: expanded ? 'rotate(180deg)' : 'rotate(0)',
              lineHeight: 1,
            }}>
              ▴
            </span>
          </span>
        </div>

        {/* Steps list */}
        {expanded && (
          <div style={{ padding: '4px 10px 8px', borderTop: '1px solid #f5f5f5' }}>
            {steps.map((step, i) => {
              const meta = STEP_META[step.status] || STEP_META.pending;
              const { Icon } = meta;
              const isLast = i === steps.length - 1;
              const isSelected = selectedIndex === i;
              const isDone = step.status === 'done';
              const isRunning = step.status === 'running';
              return (
                <div key={i}
                  onClick={() => setSelectedIndex(isSelected ? null : i)}
                  style={{
                    display: 'flex',
                    alignItems: 'flex-start',
                    gap: '8px',
                    position: 'relative',
                    padding: '3px 6px',
                    margin: '0 -6px',
                    borderRadius: '7px',
                    cursor: 'default',
                    background: isSelected ? '#f0f5ff' : 'transparent',
                    transition: 'background 0.18s ease',
                  }}
                  onMouseEnter={e => { if (!isSelected) e.currentTarget.style.background = '#fafafa'; }}
                  onMouseLeave={e => { if (!isSelected) e.currentTarget.style.background = 'transparent'; }}
                >
                  {/* Timeline */}
                  <div style={{
                    display: 'flex',
                    flexDirection: 'column',
                    alignItems: 'center',
                    width: '18px',
                    flexShrink: 0,
                    paddingTop: '5px',
                  }}>
                    <span style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      width: '18px',
                      height: '18px',
                      borderRadius: '50%',
                      background: isRunning ? meta.bg : (isDone ? '#f6ffed' : meta.bg),
                      color: meta.color,
                      fontSize: isRunning ? '14px' : (isDone ? '10px' : '10px'),
                      lineHeight: 1,
                      flexShrink: 0,
                      zIndex: 1,
                      boxShadow: meta.shadow,
                      animation: meta.pulse ? 'chain-dot-pulse 2s ease-in-out infinite' : 'none',
                      transition: 'all 0.3s ease',
                    }}>
                      {isRunning ? <SyncOutlined spin style={{ fontSize: '11px' }} /> : <Icon style={{ fontSize: '11px' }} />}
                    </span>
                    {!isLast && (
                      <div style={{
                        width: '1.5px',
                        flex: 1,
                        minHeight: '16px',
                        marginTop: '3px',
                        borderRadius: '1px',
                        background: isDone
                          ? 'linear-gradient(180deg, #b7eb8f 0%, #d9f7be 100%)'
                          : 'linear-gradient(180deg, #e8e8e8 0%, #f5f5f5 100%)',
                      }} />
                    )}
                  </div>
                  {/* Step content */}
                  <div style={{
                    flex: 1,
                    paddingTop: '3px',
                    fontSize: '13px',
                    lineHeight: '22px',
                    minWidth: 0,
                  }}>
                    <span style={{
                      color: isDone ? '#262626' : (isRunning ? '#1677ff' : meta.color),
                      fontWeight: isRunning ? 600 : 400,
                      letterSpacing: '0.2px',
                      transition: 'color 0.3s',
                    }}>
                      {getStepLabel(step)}
                    </span>
                    {step.detail && (
                      <span style={{
                        display: 'inline-block',
                        color: '#8c8c8c',
                        marginLeft: '6px',
                        fontSize: '11px',
                        maxWidth: '200px',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                        verticalAlign: 'middle',
                        letterSpacing: '0.1px',
                      }}>
                        {step.detail.length > 55 ? step.detail.substring(0, 55) + '…' : step.detail}
                      </span>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}

        <style>{`
          @keyframes chain-dot-pulse {
            0%, 100% { box-shadow: 0 0 0 0 rgba(22, 119, 255, 0.35); }
            50% { box-shadow: 0 0 0 5px rgba(22, 119, 255, 0.06); }
          }
        `}</style>
      </div>

      {/* ── Detail panel ── */}
      {selectedStep && (
        <div style={{
          background: '#fff',
          border: '1px solid #e8e8ec',
          borderRadius: '10px',
          overflow: 'hidden',
          minWidth: '260px',
          maxWidth: '380px',
          flex: 1,
          boxShadow: '0 2px 6px rgba(0,0,0,0.05)',
          animation: 'chain-detail-in 0.22s ease-out',
        }}>
          <div style={{
            padding: '8px 12px',
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            borderBottom: '1px solid #f0f0f0',
            background: '#fafafa',
          }}>
            <span style={{
              display: 'inline-block',
              width: '7px',
              height: '7px',
              borderRadius: '50%',
              background: (STEP_META[selectedStep.status] || STEP_META.pending).color,
              flexShrink: 0,
            }} />
            <span style={{ fontSize: '13px', fontWeight: 600, color: '#262626', letterSpacing: '0.2px' }}>
              步骤详情
            </span>
            <span
              onClick={e => { e.stopPropagation(); setSelectedIndex(null); }}
              style={{
                marginLeft: 'auto',
                cursor: 'pointer',
                fontSize: '16px',
                color: '#bfbfbf',
                lineHeight: 1,
                padding: '2px 6px',
                borderRadius: '4px',
                transition: 'all 0.15s',
              }}
              onMouseEnter={e => { e.currentTarget.style.color = '#595959'; e.currentTarget.style.background = '#f0f0f0'; }}
              onMouseLeave={e => { e.currentTarget.style.color = '#bfbfbf'; e.currentTarget.style.background = 'transparent'; }}
            >
              ×
            </span>
          </div>
          <div style={{ padding: '14px' }}>
            <div style={{ marginBottom: '12px' }}>
              <div style={{ fontSize: '11px', color: '#8c8c8c', marginBottom: '4px', fontWeight: 500, textTransform: 'uppercase', letterSpacing: '0.5px' }}>步骤名称</div>
              <div style={{ fontSize: '14px', fontWeight: 600, color: '#262626' }}>
                {getStepLabel(selectedStep)}
              </div>
            </div>
            <div style={{ marginBottom: '12px', display: 'flex', gap: '12px' }}>
              <div>
                <div style={{ fontSize: '11px', color: '#8c8c8c', marginBottom: '4px', fontWeight: 500, textTransform: 'uppercase', letterSpacing: '0.5px' }}>事件类型</div>
                <Tag style={{ fontSize: '11px', margin: 0 }} color={
                  selectedStep.status === 'done' ? 'success' :
                  selectedStep.status === 'running' ? 'processing' :
                  selectedStep.status === 'error' ? 'error' : 'default'
                }>
                  {selectedStep.key}
                </Tag>
              </div>
              <div>
                <div style={{ fontSize: '11px', color: '#8c8c8c', marginBottom: '4px', fontWeight: 500, textTransform: 'uppercase', letterSpacing: '0.5px' }}>状态</div>
                <Tag style={{ fontSize: '11px', margin: 0 }} color={
                  selectedStep.status === 'done' ? 'success' :
                  selectedStep.status === 'running' ? 'processing' :
                  selectedStep.status === 'error' ? 'error' : 'default'
                }>
                  {selectedStep.status === 'done' ? '已完成' :
                   selectedStep.status === 'running' ? '运行中' :
                   selectedStep.status === 'error' ? '失败' : '等待中'}
                </Tag>
              </div>
            </div>
            {selectedStep.detail && (
              <div>
                <div style={{ fontSize: '11px', color: '#8c8c8c', marginBottom: '6px', fontWeight: 500, textTransform: 'uppercase', letterSpacing: '0.5px' }}>详细数据</div>
                <div style={{
                  background: '#fafafa',
                  borderRadius: '8px',
                  padding: '2px 0',
                  border: '1px solid #f0f0f0',
                }}>
                  {formatDetailValue(selectedStep.detail)}
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      <style>{`
        @keyframes chain-detail-in {
          from { opacity: 0; transform: translateX(-10px) scale(0.97); }
          to { opacity: 1; transform: translateX(0) scale(1); }
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

function ComboField({ value, options, placeholder, hasError, onChange, entitySearch }) {
  const [open, setOpen] = React.useState(false);
  const [editValue, setEditValue] = React.useState(null);
  const [searchResults, setSearchResults] = React.useState(null); // null=未搜索, [] = 搜索中
  const [searchTimer, setSearchTimer] = React.useState(null);
  const wrapperRef = React.useRef(null);

  // 点击外部关闭下拉，重置编辑状态
  React.useEffect(() => {
    const handleClick = (e) => {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target)) {
        setOpen(false);
        setEditValue(null);
        setSearchResults(null);
      }
    };
    if (open) {
      document.addEventListener('mousedown', handleClick);
      return () => document.removeEventListener('mousedown', handleClick);
    }
  }, [open]);

  // 当前显示值：编辑中显示 editValue，否则显示选中值
  const displayValue = editValue !== null ? editValue : (value || '');

  // 服务端搜索逻辑
  const doServerSearch = React.useCallback(async (keyword) => {
    if (!keyword) {
      setSearchResults(null);
      return;
    }
    setSearchResults([]); // 标记搜索中
    try {
      const resp = await fetch('/api/ontology/entities/search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ concept: entitySearch, keyword }),
      });
      const data = await resp.json();
      setSearchResults(data.options || []);
    } catch {
      setSearchResults(null);
    }
  }, [entitySearch]);

  // 输入变更：服务端搜索走 debounce，否则即时客户端过滤
  const handleInputChange = React.useCallback((text) => {
    setEditValue(text);
    setOpen(true);
    if (entitySearch) {
      if (searchTimer) clearTimeout(searchTimer);
      const timer = setTimeout(() => doServerSearch(text), 300);
      setSearchTimer(timer);
    }
  }, [entitySearch, searchTimer, doServerSearch]);

  // 确定显示的选项列表
  let filtered;
  if (entitySearch) {
    if (searchResults === null) {
      // 未开始搜索：显示初始 options
      filtered = options;
    } else if (searchResults.length === 0 && editValue !== null && editValue !== '') {
      // 搜索中或空结果
      filtered = [];
    } else {
      filtered = searchResults;
    }
  } else {
    const filterText = editValue !== null ? editValue : '';
    filtered = filterText
      ? options.filter(o => o.value.toLowerCase().includes(filterText.toLowerCase()) || o.label.toLowerCase().includes(filterText.toLowerCase()))
      : options;
  }

  const isLoading = entitySearch && searchResults !== null && searchResults.length === 0 && editValue !== null && editValue !== '';

  const handleSelect = (optValue) => {
    onChange(optValue);
    setEditValue(null);
    setSearchResults(null);
    setOpen(false);
  };

  const toggleOpen = () => {
    setOpen(!open);
    setEditValue(null);
    setSearchResults(null);
  };

  const inputStyle = {
    width: '100%',
    padding: '6px 30px 6px 10px',
    borderRadius: '6px',
    fontSize: '13px',
    border: `1px solid ${hasError ? '#ff4d4f' : '#e8e8e8'}`,
    background: hasError ? '#fffbfb' : '#fff',
    boxSizing: 'border-box',
    color: '#333',
    outline: 'none',
  };

  return (
    <div ref={wrapperRef} style={{ position: 'relative' }}>
      <div style={{ position: 'relative' }}>
        <input
          type="text"
          value={displayValue}
          placeholder={placeholder}
          onChange={e => handleInputChange(e.target.value)}
          onFocus={() => setOpen(true)}
          onClick={() => setOpen(true)}
          style={inputStyle}
        />
        <button
          type="button"
          tabIndex={-1}
          onClick={toggleOpen}
          style={{
            position: 'absolute', right: '4px', top: '50%', transform: 'translateY(-50%)',
            background: 'none', border: 'none', cursor: 'pointer', padding: '4px',
            fontSize: '10px', color: '#999', lineHeight: 1,
          }}
        >
          {open ? '▲' : '▼'}
        </button>
      </div>
      {open && (
        <div style={{
          position: 'absolute', top: '100%', left: 0, right: 0, zIndex: 1000,
          maxHeight: '180px', overflowY: 'auto',
          background: '#fff', borderRadius: '6px',
          border: '1px solid #e8e8e8', boxShadow: '0 4px 12px rgba(0,0,0,0.12)',
          marginTop: '2px',
        }}>
          {isLoading ? (
            <div style={{ padding: '8px 12px', color: '#999', fontSize: '12px' }}>搜索中…</div>
          ) : filtered.length === 0 ? (
            <div style={{ padding: '8px 12px', color: '#999', fontSize: '12px' }}>无匹配选项</div>
          ) : (
            filtered.map(opt => (
              <div
                key={opt.value}
                onClick={() => handleSelect(opt.value)}
                style={{
                  padding: '7px 12px', fontSize: '13px', cursor: 'pointer',
                  background: opt.value === value ? '#e6f7ff' : 'transparent',
                  color: '#333',
                  borderBottom: '1px solid #f5f5f5',
                }}
                onMouseEnter={e => { e.target.style.background = '#f0f5ff'; }}
                onMouseLeave={e => { e.target.style.background = opt.value === value ? '#e6f7ff' : 'transparent'; }}
              >
                {opt.label}
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}

  function ConfirmCard({ confirm, onApprove, onReject }) {
    const [submitting, setSubmitting] = React.useState(false);
    const [cancelHover, setCancelHover] = React.useState(false);
    const [errors, setErrors] = React.useState({});
    const paramSchema = confirm?.param_schema || [];
    const prefillParams = confirm?.params || {};
    const ontologyContext = confirm?.context || {};
    const violationMsg = typeof ontologyContext.violation === 'string' ? ontologyContext.violation : '';
    const cleanContext = { ...ontologyContext };
    delete cleanContext.violation;
    const [autoFilled, setAutoFilled] = React.useState(() => {
      const filled = {};
      paramSchema.forEach(p => {
        const hasPrefill = p.name in prefillParams && prefillParams[p.name] != null && prefillParams[p.name] !== '';
        const hasSingleOption = p.entityOptions && p.entityOptions.length === 1;
        const hasDefault = p.defaultValue != null && p.defaultValue !== '';
        filled[p.name] = hasPrefill || (!hasPrefill && (hasSingleOption || hasDefault));
      });
      return filled;
    });
    const [formValues, setFormValues] = React.useState(() => {
      const init = {};
      paramSchema.forEach(p => {
        if (p.name in prefillParams && prefillParams[p.name] != null && prefillParams[p.name] !== '') {
          init[p.name] = prefillParams[p.name];
        } else if (p.entityOptions && p.entityOptions.length === 1) {
          init[p.name] = p.entityOptions[0].value;
        } else if (p.defaultValue != null && p.defaultValue !== '') {
          init[p.name] = p.defaultValue;
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
        // 如果字段有 entityOptions（下拉选项），值必须在选项中
        // 但有 entitySearch（服务端搜索）时放宽：允许搜索到的值不在预加载列表中
        if (p.entityOptions && p.entityOptions.length > 0 && !p.entitySearch) {
          const v = formValues[p.name];
          if (v != null && v !== '' && !p.entityOptions.some(o => o.value === v)) {
            newErrors[p.name] = `"${v}" 不在${p.label || p.name}可选范围内`;
          }
        }
        // 如果字段有 enumValues（枚举选项），值必须在选项中
        if (p.enumValues && p.enumValues.length > 0) {
          const v = formValues[p.name];
          if (v != null && v !== '' && !p.enumValues.includes(v)) {
            newErrors[p.name] = `"${v}" 不在${p.label || p.name}可选范围内`;
          }
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
      if ((p.entityOptions && p.entityOptions.length > 0) || p.entitySearch) {
        return (
          <ComboField
            value={formValues[p.name]}
            options={p.entityOptions || []}
            entitySearch={p.entitySearch || null}
            placeholder={`搜索${p.label}`}
            hasError={hasError}
            onChange={v => setField(p.name, v)}
          />
        );
      }
      if (p.enumValues && p.enumValues.length > 0) {
        const enumOpts = p.enumValues.map(v => ({ value: v, label: v }));
        return (
          <ComboField
            value={formValues[p.name]}
            options={enumOpts}
            placeholder={`选择${p.label}`}
            hasError={hasError}
            onChange={v => setField(p.name, v)}
          />
        );
      }
      const isRequired = p.required !== false;
      if (p.type === 'int' || p.type === 'float' || p.type === 'number') {
        return (
          <input
            className="confirm-field-input"
            type="number"
            required={isRequired}
            value={formValues[p.name] ?? ''}
            onChange={e => setField(p.name, e.target.value ? parseFloat(e.target.value) : undefined)}
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
            required={isRequired}
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
          required={isRequired}
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

  const hasContext = Object.keys(cleanContext).length > 0;

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
        <Tag style={{ marginLeft: 'auto', fontSize: '10px', border: 'none', background: confirm.risk === 'rule_approval' ? '#fff7e6' : '#fff1cc', color: confirm.risk === 'rule_approval' ? '#d46b08' : '#ad6800' }}>
          {confirm.risk === 'rule_approval' ? '规则审批' : '需确认'}
        </Tag>
      </div>

      <div style={{ padding: '14px 16px' }}>

        {/* 违规提示 */}
        {violationMsg && (
          <div style={{
            background: '#fff2f0', border: '1px solid #ffccc7', borderRadius: '8px',
            padding: '10px 12px', marginBottom: '14px', fontSize: '12px', color: '#cf1322',
          }}>
            <div style={{ fontWeight: 600, marginBottom: '4px', fontSize: '12px' }}>⚠️ 规则校验失败，请修正后重新提交：</div>
            <div style={{ whiteSpace: 'pre-wrap' }}>{violationMsg}</div>
          </div>
        )}

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
            {Object.entries(cleanContext).map(([key, ctxValue]) => {
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

        {/* Inference chain (推理链确认) */}
        {confirm.type === 'inference_chain' && confirm.inferences?.length > 0 && (
          <div style={{ marginBottom: '14px' }}>
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
                background: '#a78bfa',
              }} />
              推理链将自动执行以下操作：
            </div>
            {confirm.inferences.map((inf, idx) => (
              <div key={idx} style={{
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
                padding: '6px 10px',
                marginBottom: '4px',
                background: '#faf5ff',
                borderRadius: '6px',
                border: '1px solid #f0e0ff',
                fontSize: '12px',
              }}>
                <span style={{
                  color: '#7c3aed',
                  fontWeight: 600,
                  fontSize: '11px',
                  minWidth: '20px',
                }}>{idx + 1}.</span>
                <span style={{ color: '#5b3ea8', flex: 1 }}>{inf.description || inf.rule_label}</span>
                <span style={{
                  color: '#a78bfa',
                  fontSize: '10px',
                  fontFamily: 'monospace',
                  background: '#f5f0ff',
                  padding: '2px 6px',
                  borderRadius: '4px',
                }}>{inf.target}</span>
              </div>
            ))}
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
