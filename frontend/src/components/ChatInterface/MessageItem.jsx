import React from 'react';
import { Avatar, Button, Tooltip, Typography, Spin } from 'antd';
import { UserOutlined, RobotOutlined, CopyOutlined, CheckOutlined, SyncOutlined, WarningOutlined } from '@ant-design/icons';
import MarkdownRenderer from '../MarkdownRenderer';
import PlanStepsPanel from './PlanStepsPanel';
import ChainProgress from './ChainProgress';
import FeedbackBar from './FeedbackBar';
import CollabStepsPanel from './CollabStepsPanel';

function MessageItem({ item, copiedId, onCopy, onToggleThinking }) {
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
              {isStreaming && (
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

export default MessageItem;
