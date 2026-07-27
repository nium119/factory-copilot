import React from 'react';
import { Empty } from 'antd';
import MessageItem from './MessageItem';

function MessageList({ messages, copiedId, onCopy, onToggleThinking, messagesEndRef, onConfirmApprove, onConfirmReject, onSaveChain, onRetry, onExecuteAction, conversationId, onOpenChainDrawer }) {
  return (
    <div style={{ flex: 1, overflow: 'auto', padding: '12px 12px 12px 0', width: '100%' }} className="chat-scroll-area">
      <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '0 16px' }}>
        {messages.length === 0 && (
          <Empty description="暂无消息" style={{ marginTop: '100px' }} />
        )}
        <div>
          {messages.map((item) => (
            <div key={item.id}>
              <MessageItem
                item={item}
                copiedId={copiedId}
                onCopy={onCopy}
                onToggleThinking={onToggleThinking}
                onConfirmApprove={onConfirmApprove}
                onConfirmReject={onConfirmReject}
                onSaveChain={onSaveChain}
                onRetry={onRetry}
                onExecuteAction={onExecuteAction}
                conversationId={conversationId}
                onOpenChainDrawer={onOpenChainDrawer}
              />
            </div>
          ))}
        </div>
        <div ref={messagesEndRef} />
      </div>
    </div>
  );
}

export default MessageList;
