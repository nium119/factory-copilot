import React from 'react';
import { Input, Button, Dropdown, Switch, Tooltip, Typography, Tag } from 'antd';
import { SendOutlined, ClearOutlined, SwapOutlined, BulbOutlined, SearchOutlined, StopOutlined, ThunderboltOutlined } from '@ant-design/icons';

const { TextArea } = Input;

function ChatInputBar({
  inputRef,
  inputValue,
  sending,
  mentionVisible,
  filteredAgents,
  models,
  currentModel,
  enableThinking,
  modelSupportsThinking,
  webSearch,
  messageCount,
  agents,
  hasNoAgents,
  onInputChange,
  onKeyPress,
  onSend,
  onStop,
  onMentionSelect,
  onModelChange,
  onEnableThinkingChange,
  onWebSearchChange,
  onClear,
}) {
  return (
    <div className="chat-input-wrapper">
      {/* @ 提及面板 — absolute 定位在输入框上方，wrapper overflow:visible 允许溢出 */}
      {mentionVisible && (
        <div className="chat-mention-panel">
          <div className="chat-mention-title">选择 Agent</div>
          {filteredAgents.map(a => (
            <div
              key={a.name}
              className="chat-mention-item"
              onClick={() => onMentionSelect(a)}
            >
              <span className="chat-mention-icon">{a.icon}</span>
              <div>
                <div className="chat-mention-name" style={{ color: a.color }}>{a.display_name}</div>
                <div className="chat-mention-desc">{a.description}</div>
              </div>
            </div>
          ))}
          {filteredAgents.length === 0 && (
            <div className="chat-mention-empty">无匹配结果</div>
          )}
        </div>
      )}
      <TextArea
        ref={inputRef}
        value={inputValue}
        onChange={onInputChange}
        onKeyPress={onKeyPress}
        placeholder={hasNoAgents ? "暂无业务域配置，请先点击左下角「配置」完成业务域推导" : "输入消息... (Enter发送, Shift+Enter换行)"}
        autoSize={{ minRows: 3, maxRows: 8 }}
        className="chat-input-textarea"
        disabled={sending || hasNoAgents}
      />
      {/* 内部浮动工具栏 */}
      <div className="chat-toolbar">
        {/* 模型选择 */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
          <Dropdown menu={{ items: models, onClick: (e) => onModelChange(e.key) }}>
            <Button type="text" size="small" className="chat-toolbar-btn model-btn">
              {models.find(m => m.key === currentModel)?.label || '模型'}
              <SwapOutlined className="chat-swap-icon" />
            </Button>
          </Dropdown>
        </div>

        {/* 深度思考 */}
        <Tooltip title={modelSupportsThinking ? '切换模型思考模式' : '当前模型不支持思考模式'}>
          <div className={`chat-toggle-group${!modelSupportsThinking ? ' disabled-toggle' : ''}`}>
            <BulbOutlined className={`chat-toggle-icon ${enableThinking && modelSupportsThinking ? 'active' : 'inactive'}`} />
            <span className={`chat-toggle-label ${enableThinking && modelSupportsThinking ? 'active' : 'inactive'}`}>深度思考</span>
            <Switch size="small" checked={enableThinking && modelSupportsThinking} onChange={onEnableThinkingChange} disabled={!modelSupportsThinking} />
          </div>
        </Tooltip>

        {/* 联网搜索 */}
        <div className="chat-toggle-group">
          <SearchOutlined className={`chat-toggle-icon ${webSearch ? 'active' : 'inactive'}`} />
          <span className={`chat-toggle-label ${webSearch ? 'active' : 'inactive'}`}>联网搜索</span>
          <Switch size="small" checked={webSearch} onChange={onWebSearchChange} />
        </div>

        <div style={{ flex: 1 }} />

        {/* 消息数 */}
        {messageCount > 0 && (
          <Typography.Text type="secondary" className="chat-msg-count">
            {messageCount} 条
          </Typography.Text>
        )}

        {/* 清除 */}
        {onClear && (
          <Tooltip title="清除会话">
            <Button type="text" size="small" icon={<ClearOutlined />} onClick={onClear} disabled={messageCount === 0}
              className="chat-toolbar-btn clear-btn" />
          </Tooltip>
        )}

        {/* 发送/停止按钮 */}
        <Button type="primary"
          className={`chat-toolbar-btn send-btn${sending ? ' stop-btn' : ''}`}
          icon={sending ? <StopOutlined /> : <SendOutlined />}
          onClick={sending ? onStop : onSend}
          disabled={hasNoAgents || (!sending && !inputValue.trim())}
        />
      </div>
    </div>
  );
}

export default ChatInputBar;
