import React, { useRef, useState } from 'react';
import { Input, Button, Dropdown, Switch, Tooltip, Typography, Tag, message } from 'antd';
import { SendOutlined, ClearOutlined, SwapOutlined, BulbOutlined, SearchOutlined, StopOutlined, ThunderboltOutlined, AudioOutlined, LoadingOutlined } from '@ant-design/icons';
import { transcribeAudio, createVoiceStream } from '../../services/voiceService';

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

// Float32 PCM → 16bit PCM 字节（流式实时推流用）
function float32ToPcm16(f32) {
  const buffer = new ArrayBuffer(f32.length * 2);
  const view = new DataView(buffer);
  for (let i = 0; i < f32.length; i++) {
    const s = Math.max(-1, Math.min(1, f32[i]));
    view.setInt16(i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
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
  onVoiceText,
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
  const [transcribing, setTranscribing] = useState(false);
  const [recordingSeconds, setRecordingSeconds] = useState(0);
  const [streamText, setStreamText] = useState('');
  const audioCtxRef = useRef(null);
  const processorRef = useRef(null);
  const streamRef = useRef(null);
  const pcmChunksRef = useRef([]);
  const timerRef = useRef(null);
  const voiceStreamRef = useRef(null);
  const streamTextRef = useRef('');
  const recordingRef = useRef(false);

  const startRecording = async () => {
    if (recordingRef.current || transcribing) return;
    try {
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        message.error('当前浏览器环境不支持麦克风：请使用 HTTPS 或 localhost 访问');
        return;
      }
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;

      // 建立流式转写连接（边说边出字）
      const vs = createVoiceStream({
        onPartial: (t) => { streamTextRef.current = t; setStreamText(t); },
        onFinal: (t) => { streamTextRef.current = t; setStreamText(t); },
      });
      try {
        await vs.connect();
      } catch (e) {
        stream.getTracks().forEach((t) => t.stop());
        streamRef.current = null;
        throw e;
      }
      voiceStreamRef.current = vs;

      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      const audioCtx = new AudioCtx({ sampleRate: 16000 });
      audioCtxRef.current = audioCtx;
      const source = audioCtx.createMediaStreamSource(stream);
      const processor = audioCtx.createScriptProcessor(2048, 1, 1);
      pcmChunksRef.current = [];
      streamTextRef.current = '';
      setStreamText('');
      processor.onaudioprocess = (e) => {
        const f32 = new Float32Array(e.inputBuffer.getChannelData(0));
        pcmChunksRef.current.push(f32);
        vs.sendAudio(float32ToPcm16(f32));
      };
      source.connect(processor);
      processor.connect(audioCtx.destination);
      processorRef.current = processor;
      setRecording(true);
      recordingRef.current = true;
      setRecordingSeconds(0);
      timerRef.current = setInterval(() => setRecordingSeconds((s) => s + 1), 1000);
    } catch (e) {
      console.error('无法访问麦克风', e);
      const name = e && e.name;
      if (name === 'NotAllowedError' || name === 'PermissionDeniedError') {
        message.error('麦克风权限被拒绝：请点击地址栏左侧的锁图标，将麦克风设为「允许」后重试');
      } else if (name === 'SecurityError') {
        message.error('当前不是安全连接（需 HTTPS 或 localhost），浏览器禁止访问麦克风');
      } else if (name === 'NotFoundError' || name === 'DevicesNotFoundError') {
        message.error('未检测到麦克风设备，请检查麦克风是否连接/启用');
      } else if (name === 'NotReadableError' || name === 'TrackStartError' || name === 'AbortError') {
        message.error('麦克风被占用或不可用，请关闭占用麦克风的其他程序后重试');
      } else {
        message.error(`无法打开麦克风：${name || (e && e.message) || '未知错误'}`);
      }
    }
  };

  const stopRecording = async () => {
    const processor = processorRef.current;
    const audioCtx = audioCtxRef.current;
    const stream = streamRef.current;
    const vs = voiceStreamRef.current;
    if (processor) { processor.disconnect(); processor.onaudioprocess = null; }
    if (audioCtx) { audioCtx.close(); }
    if (stream) { stream.getTracks().forEach((t) => t.stop()); }
    processorRef.current = null;
    audioCtxRef.current = null;
    streamRef.current = null;
    voiceStreamRef.current = null;
    if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null; }
    setRecording(false);
    recordingRef.current = false;

    if (!vs) return;
    setTranscribing(true);
    try {
      // 发 end，等待 DashScope 返回最终结果（onComplete 时 resolve）
      await vs.end();
      const text = (streamTextRef.current || '').trim();
      if (text) {
        onVoiceText(inputValue ? `${inputValue}${text}` : text);
      } else {
        message.info('未识别到语音内容');
      }
    } catch (e) {
      console.error('流式识别失败，回退文件识别', e);
      // 兜底：用已录 PCM 转 WAV 走文件识别
      const pcm = concatFloat32(pcmChunksRef.current);
      if (!pcm.length) { message.info('未录到语音内容'); return; }
      const wav = float32ToWav(pcm, 16000);
      const blob = new Blob([wav], { type: 'audio/wav' });
      try {
        const res = await transcribeAudio(blob, 'recording.wav');
        const text = (res && res.text) || '';
        if (text) {
          onVoiceText(inputValue ? `${inputValue}${text}` : text);
        } else {
          message.info('未识别到语音内容');
        }
      } catch (e2) {
        console.error('语音识别失败', e2);
        message.error(e2 && e2.message ? e2.message : '语音识别失败');
      }
    } finally {
      setTranscribing(false);
      setStreamText('');
      streamTextRef.current = '';
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
        {/* 语音输入（按住说话，松开结束） */}
        <Tooltip title={recording ? `松开结束（已录 ${recordingSeconds}s）` : transcribing ? '识别中，请稍候…' : '按住说话'}>
          <Button
            type="text"
            size="small"
            icon={recording ? <StopOutlined /> : transcribing ? <LoadingOutlined spin /> : <AudioOutlined />}
            onMouseDown={() => { if (!transcribing) startRecording(); }}
            onMouseUp={() => { if (recordingRef.current) stopRecording(); }}
            onMouseLeave={() => { if (recordingRef.current) stopRecording(); }}
            onTouchStart={(e) => { e.preventDefault(); if (!transcribing) startRecording(); }}
            onTouchEnd={(e) => { e.preventDefault(); if (recordingRef.current) stopRecording(); }}
            disabled={transcribing}
            className="chat-toolbar-btn"
            style={recording ? { color: '#ff4d4f' } : undefined}
          />
        </Tooltip>
        {(recording || transcribing) && (
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 12, color: '#ff4d4f', marginLeft: 2, whiteSpace: 'nowrap', maxWidth: 240, overflow: 'hidden' }}>
            <span style={{ width: 8, height: 8, borderRadius: '50%', background: '#ff4d4f', display: 'inline-block', animation: 'fc-blink 1s infinite', flexShrink: 0 }} />
            <span style={{ flexShrink: 0 }}>{recording ? `录音中 ${recordingSeconds}s` : '识别中'}</span>
            {streamText && <span style={{ color: '#666', overflow: 'hidden', textOverflow: 'ellipsis' }}>{streamText}</span>}
          </span>
        )}
        {/* 模型选择 */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
          <Dropdown menu={{ items: models, onClick: (e) => onModelChange(e.key) }}>
            <Button type="text" size="small" className="chat-toolbar-btn model-btn">
              {models.find(m => m.key === currentModel)?.label || '模型'}
              <SwapOutlined className="chat-swap-icon" />
            </Button>
          </Dropdown>
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
