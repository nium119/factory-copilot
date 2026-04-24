import React from 'react';
import { Avatar, Button, Tooltip, Typography, Spin } from 'antd';
import { UserOutlined, RobotOutlined, CopyOutlined, CheckOutlined, ThunderboltOutlined } from '@ant-design/icons';
import MarkdownRenderer from '../MarkdownRenderer';

/* 协作查询步骤面板 */
function CollabStepsPanel({ collabAgents, isCollabMode }) {
  const [selectedIdx, setSelectedIdx] = React.useState(null);

  return (
    <div style={{
      background: '#f8f7ff',
      border: '1px solid rgba(108, 92, 231, 0.12)',
      borderRadius: '10px',
      marginBottom: '8px',
      padding: '12px 16px',
      maxWidth: '100%',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '12px', fontSize: '13px', fontWeight: 500, color: '#6c5ce7' }}>
        <ThunderboltOutlined style={{ fontSize: '14px' }} />
        <span>协作查询</span>
        {isCollabMode && <Spin size="small" />}
      </div>
      {/* Steps component from antd — rendered inline to avoid import bloat */}
      <div style={{ display: 'flex', gap: '8px', overflowX: 'auto', paddingBottom: '8px' }}>
        {collabAgents.map((agent, idx) => (
          <div
            key={agent.name || idx}
            style={{
              minWidth: '80px',
              textAlign: 'center',
              cursor: 'pointer',
              fontSize: '13px',
              fontWeight: selectedIdx === idx ? 600 : 500,
              color: selectedIdx === idx ? '#6c5ce7' : 'inherit',
              background: selectedIdx === idx ? 'rgba(108, 92, 231, 0.12)' : 'transparent',
              padding: '4px 6px',
              borderRadius: '4px',
            }}
            onClick={() => setSelectedIdx(selectedIdx === idx ? null : idx)}
          >
            <div>{agent.display_name}</div>
            <div style={{ fontSize: '11px', color: '#999' }}>
              {agent.status === 'success' ? '查询完成' : '无匹配数据'}
            </div>
          </div>
        ))}
      </div>
      {/* 点击展开详情 */}
      {selectedIdx !== null && collabAgents[selectedIdx]?.data && (
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
          <div style={{ fontSize: '12px', fontWeight: 500, color: '#6c5ce7', marginBottom: '6px' }}>
            {collabAgents[selectedIdx].display_name} 查询结果：
          </div>
          <MarkdownRenderer content={collabAgents[selectedIdx].data} streaming={false} />
        </div>
      )}
      {selectedIdx !== null && !collabAgents[selectedIdx]?.data && (
        <div style={{
          marginTop: '12px',
          padding: '8px 12px',
          background: '#fff',
          borderRadius: '8px',
          border: '1px solid rgba(108, 92, 231, 0.08)',
          fontSize: '12px',
          color: '#bbb',
        }}>
          该 Agent 无匹配数据
        </div>
      )}
    </div>
  );
}

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
      </div>
    </div>
  );
}

export default MessageItem;
