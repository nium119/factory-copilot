import React, { useState, useCallback, useRef } from 'react';
import AgentSidebar from '../AgentSidebar';
import ChatInterface from '../ChatInterface';

/**
 * 对话视图：左侧 AgentSidebar + 右侧 ChatInterface
 * AgentSidebar 只保留 Agent 列表、新建对话、历史入口，
 * 待审批/配置等功能由外部菜单承载。
 */
export default function ChatView({
  sessionId,
  initialMessage,
  initialWebSearch,
  agents,
  selectedAgent,
  onSelectAgent,
  onToggleHistory,
  onToggleChainManager,
  chainManagerActive,
  explorerAnomalies,
  onToggleExplorer,
}) {
  const [siderWidth, setSiderWidth] = useState(280);
  const isDraggingRef = useRef(false);
  const startXRef = useRef(0);
  const startWidthRef = useRef(0);

  const handleMouseDown = useCallback((e) => {
    isDraggingRef.current = true;
    startXRef.current = e.clientX;
    startWidthRef.current = siderWidth;
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    e.preventDefault();
  }, [siderWidth]);

  React.useEffect(() => {
    const handleMouseMove = (e) => {
      if (!isDraggingRef.current) return;
      const delta = e.clientX - startXRef.current;
      const newWidth = Math.min(Math.max(startWidthRef.current + delta, 200), 500);
      setSiderWidth(newWidth);
    };
    const handleMouseUp = () => {
      if (!isDraggingRef.current) return;
      isDraggingRef.current = false;
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };
    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);
    return () => {
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
    };
  }, []);

  return (
    <div style={{ display: 'flex', height: '100%', overflow: 'hidden' }}>
      {/* Agent 侧边栏 */}
      <div style={{
        width: siderWidth, minWidth: 200, maxWidth: 500,
        height: '100%', background: '#ffffff',
        borderRight: '1px solid #e8e8ec',
        position: 'relative', overflow: 'hidden', flexShrink: 0,
      }}>
        <AgentSidebar
          onSelectAgent={onSelectAgent}
          onToggleHistory={onToggleHistory}
          onToggleChainManager={onToggleChainManager}
          chainManagerActive={chainManagerActive}
          currentAgentName={selectedAgent?.name}
          agents={agents}
          explorerAnomalies={explorerAnomalies}
          onToggleExplorer={onToggleExplorer}
          hidePendingPanel={true}
        />
        {/* 拖拽手柄 */}
        <div
          onMouseDown={handleMouseDown}
          style={{
            position: 'absolute', right: 0, top: 0, bottom: 0,
            width: 6, cursor: 'col-resize', zIndex: 10,
          }}
          onMouseEnter={(e) => { e.currentTarget.style.background = 'rgba(108, 92, 231, 0.15)'; }}
          onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; }}
        />
      </div>

      {/* 对话内容区 */}
      <div style={{ flex: 1, minWidth: 0, height: '100%', overflow: 'hidden' }}>
        <ChatInterface
          sessionId={sessionId}
          initialMessage={initialMessage}
          initialWebSearch={initialWebSearch}
          agents={agents}
          selectedAgent={selectedAgent}
        />
      </div>
    </div>
  );
}
