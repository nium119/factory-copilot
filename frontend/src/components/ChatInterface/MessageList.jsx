import React from 'react';
import { Spin, Empty } from 'antd';
import MessageItem from './MessageItem';

function MessageList({ messages, loading, copiedId, onCopy, onToggleThinking, messagesEndRef }) {
  return (
    <div style={{ flex: 1, overflow: 'auto', padding: '12px 12px 12px 0', width: '100%' }} className="chat-scroll-area">
      <div style={{ maxWidth: '800px', margin: '0 auto', padding: '0 12px' }}>
        <Spin spinning={loading} tip="加载中...">
          {messages.length === 0 ? (
            <Empty description="加载中..." style={{ marginTop: '100px' }} />
          ) : (
            <div>
              {messages.map((item) => (
                <div key={item.id}>
                  <MessageItem
                    item={item}
                    copiedId={copiedId}
                    onCopy={onCopy}
                    onToggleThinking={onToggleThinking}
                  />
                </div>
              ))}
            </div>
          )}
          <div ref={messagesEndRef} />
        </Spin>
      </div>
    </div>
  );
}

export default MessageList;
