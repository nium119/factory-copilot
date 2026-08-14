import React, { useRef, useState } from 'react';
import { Input, Button, Dropdown, Switch, Tooltip, Typography, Tag, message } from 'antd';
import { SendOutlined, ClearOutlined, SwapOutlined, BulbOutlined, SearchOutlined, StopOutlined, ThunderboltOutlined, AudioOutlined } from '@ant-design/icons';
import { transcribeAudio } from '../../services/voiceService';

const { TextArea } = Input;

// PCM 拼接 + 编码 WAV（16kHz 16bit 单声道，Paraformer 兼容）
function concatFloat32(chunks) {
  let len = 0;
  chunks.forEach((c) => { len += c.length; });
  const result = new Float32Array(len);
  let offset = 0;
  chunks.forEach((c) => { result.set(c, offset); offset += c.length; });
  return result;
}

function float32ToWav(pcm, sampleRate) {
  const buffer = new ArrayBuffer(44 + pcm.length * 2);
  const view = new DataView(buffer);
  const writeStr = (offset, str) => {
    for (let i = 0; i < str.length; i++) view.setUint8(offset + i, str.charCodeAt(i));
  };
  writeStr(0, 'RIFF');
  view.setUint32(4, 36 + pcm.length * 2, true);
  writeStr(8, 'WAVE');
  writeStr(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // 单声道
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true); // byte rate
  view.setUint16(32, 2, true); // block align
  view.setUint16(34, 16, true); // bit depth
  writeStr(36, 'data');
  view.setUint32(40, pcm.length * 2, true);
  let offset = 44;
  for (let i = 0; i < pcm.length; i++, offset += 2) {
    const s = Math.max(-1, Math.min(1, pcm[i]));
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return buffer;
}

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
  const [recording, setRecording] = useState(false);
  const audioCtxRef = useRef(null);
  const processorRef = useRef(null);
  const streamRef = useRef(null);
  const pcmChunksRef = useRef([]);

  const startRecording = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      const audioCtx = new AudioCtx({ sampleRate: 16000 });
      audioCtxRef.current = audioCtx;
      const source = audioCtx.createMediaStreamSource(stream);
      const processor = audioCtx.createScriptProcessor(4096, 1, 1);
      pcmChunksRef.current = [];
      processor.onaudioprocess = (e) => {
        pcmChunksRef.current.push(new Float32Array(e.inputBuffer.getChannelData(0)));
      };
      source.connect(processor);
      processor.connect(audioCtx.destination);
      processorRef.current = processor;
      setRecording(true);
    } catch (e) {
      console.error('无法访问麦克风', e);
      message.error('无法访问麦克风，请检查浏览器权限');
    }
  };

  const stopRecording = async () => {
    const processor = processorRef.current;
    const audioCtx = audioCtxRef.current;
    const stream = streamRef.current;
    if (processor) { processor.disconnect(); processor.onaudioprocess = null; }
    if (audioCtx) { audioCtx.close(); }
    if (stream) { stream.getTracks().forEach((t) => t.stop()); }
    processorRef.current = null;
    audioCtxRef.current = null;
    streamRef.current = null;
    setRecording(false);

    const pcm = concatFloat32(pcmChunksRef.current);
    if (!pcm.length) { message.info('未录到语音内容'); return; }
    const wav = float32ToWav(pcm, 16000);
    const blob = new Blob([wav], { type: 'audio/wav' });
    try {
      const res = await transcribeAudio(blob, 'recording.wav');
      const text = (res && res.text) || '';
      if (text) {
        onInputChange(inputValue ? `${inputValue}${text}` : text);
      } else {
        message.info('未识别到语音内容');
      }
    } catch (e) {
      console.error('语音识别失败', e);
      message.error(e && e.message ? e.message : '语音识别失败');
    }
  };

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
        {/* 语音输入 */}
        <Tooltip title={recording ? '停止录音' : '语音输入'}>
          <Button
            type="text"
            size="small"
            icon={recording ? <StopOutlined /> : <AudioOutlined />}
            onClick={recording ? stopRecording : startRecording}
            className="chat-toolbar-btn"
            style={recording ? { color: '#ff4d4f' } : undefined}
          />
        </Tooltip>
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
