import React from 'react';
import { Typography } from 'antd';

const { Text } = Typography;

function WelcomeScreen({ chatInputBar }) {
  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column', justifyContent: 'center', alignItems: 'center', position: 'relative', overflow: 'hidden' }}>
      {/* 背景光晕 — 营造 AI 氛围，不遮挡内容 */}
      <div style={{
        position: 'absolute', top: '50%', left: '50%', transform: 'translate(-50%, -62%)',
        width: '520px', height: '520px', borderRadius: '50%',
        background: 'radial-gradient(circle, rgba(108,92,231,0.10) 0%, rgba(79,172,254,0.07) 42%, transparent 72%)',
        pointerEvents: 'none',
      }} />
      <div style={{ maxWidth: '800px', width: '100%', padding: '0 24px', position: 'relative', zIndex: 1 }}>
        <div style={{ textAlign: 'center', marginBottom: '24px' }}>
          <div style={{
            fontSize: '32px', fontWeight: 700, marginBottom: '10px', letterSpacing: '0.5px',
            background: 'linear-gradient(120deg, #6c5ce7 0%, #4facfe 100%)',
            WebkitBackgroundClip: 'text', backgroundClip: 'text', WebkitTextFillColor: 'transparent',
          }}>AI 智能助手</div>
          <Text type="secondary" style={{ fontSize: '14px' }}>输入消息开始对话，按 Enter 发送</Text>
        </div>
        {chatInputBar}
      </div>
    </div>
  );
}

export default WelcomeScreen;
