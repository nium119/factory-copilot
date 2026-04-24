import React from 'react';
import { Typography } from 'antd';

const { Text } = Typography;

function WelcomeScreen({ chatInputBar }) {
  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column', justifyContent: 'center', alignItems: 'center' }}>
      <div style={{ maxWidth: '800px', width: '100%', padding: '0 24px' }}>
        <div style={{ textAlign: 'center', marginBottom: '24px' }}>
          <div style={{ fontSize: '28px', fontWeight: 600, color: '#6c5ce7', marginBottom: '8px' }}>AI 智能助手</div>
          <Text type="secondary" style={{ fontSize: '14px' }}>输入消息开始对话，按 Enter 发送</Text>
        </div>
        {chatInputBar}
      </div>
    </div>
  );
}

export default WelcomeScreen;
