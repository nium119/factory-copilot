import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Input, Button, List, Avatar, Space, Spin, Empty, Typography, Tooltip, Tag, Upload, Dropdown, Switch, Steps, App } from 'antd';
import { SendOutlined, UserOutlined, RobotOutlined, ClearOutlined, ReloadOutlined, CopyOutlined, CheckOutlined, AudioOutlined, PaperClipOutlined, PictureOutlined, ThunderboltOutlined, BulbOutlined, StopOutlined, SearchOutlined, SwapOutlined } from '@ant-design/icons';
import chatService from '../services/chatService';
import { sendMessageStream, getAgents } from '../services/messageService';
import * as conversationService from '../services/conversationService';
import MarkdownRenderer from './MarkdownRenderer';
import { useConversationStore } from '../stores/ConversationContext';
import { useConversation } from '../hooks/useConversation';
import './ChatInterface.css';

const { TextArea } = Input;
const { Text, Paragraph } = Typography;

/* ─── 复用组件：输入框 + 内部浮动按钮 ─── */
function ChatInputBar({
  inputRef,
  inputValue,
  sending,
  mentionVisible,
  agents,
  filteredAgents,
  models,
  currentModel,
  selectedAgentName,
  useAgent,
  enableThinking,
  webSearch,
  messageCount,
  showExtras,
  onInputChange,
  onKeyPress,
  onSend,
  onStop,
  onMentionSelect,
  onModelChange,
  onAgentChange,
  onUseAgentChange,
  onEnableThinkingChange,
  onWebSearchChange,
  onClear,
}) {
  const agentLabel = (() => {
    if (!selectedAgentName) return '🤖 智能助手';
    if (selectedAgentName === 'auto') return '🧠 自动识别';
    const a = agents.find(x => x.name === selectedAgentName);
    return a ? `${a.icon} ${a.display_name}` : '选择 Agent';
  })();

  return (
    <div className="chat-input-wrapper">
      <TextArea
        ref={inputRef}
        value={inputValue}
        onChange={onInputChange}
        onKeyPress={onKeyPress}
        placeholder="输入消息... (Enter发送, Shift+Enter换行)"
        autoSize={{ minRows: 3, maxRows: 8 }}
        className="chat-input-textarea"
        disabled={sending}
      />
      {/* @ 提及面板 */}
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
      {/* 内部浮动工具栏 */}
      {showExtras && (
        <div className="chat-toolbar">
          {/* 模型选择 */}
          <Dropdown menu={{ items: models, onClick: (e) => onModelChange(e.key) }}>
            <Button type="text" size="small" className="chat-toolbar-btn model-btn">
              {models.find(m => m.key === currentModel)?.label || '模型'}
              <SwapOutlined className="chat-swap-icon" />
            </Button>
          </Dropdown>

          {/* Agent 选择 */}
          <Dropdown menu={{
            items: [
              { key: '', label: '🤖 智能助手（默认）' },
              { key: 'auto', label: '🧠 自动识别' },
              { type: 'divider' },
              ...agents.map(a => ({ key: a.name, label: `${a.icon} ${a.display_name}` })),
            ],
            onClick: (e) => onAgentChange(e.key || null),
          }}>
            <Button type="text" size="small" className={`chat-toolbar-btn agent-btn${selectedAgentName === 'auto' ? ' active' : ''}`}>
              {agentLabel}
              <SwapOutlined className="chat-swap-icon" />
            </Button>
          </Dropdown>

          {/* 协作模式 */}
          <div className="chat-toggle-group">
            <ThunderboltOutlined className={`chat-toggle-icon ${useAgent ? 'active' : 'inactive'}`} />
            <span className={`chat-toggle-label ${useAgent ? 'active' : 'inactive'}`}>协作模式</span>
            <Switch size="small" checked={useAgent} onChange={onUseAgentChange} />
          </div>

          {/* 深度思考 */}
          <div className="chat-toggle-group">
            <BulbOutlined className={`chat-toggle-icon ${enableThinking ? 'active' : 'inactive'}`} />
            <span className={`chat-toggle-label ${enableThinking ? 'active' : 'inactive'}`}>深度思考</span>
            <Switch size="small" checked={enableThinking} onChange={onEnableThinkingChange} />
          </div>

          {/* 联网搜索 */}
          <div className="chat-toggle-group">
            <SearchOutlined className={`chat-toggle-icon ${webSearch ? 'active' : 'inactive'}`} />
            <span className={`chat-toggle-label ${webSearch ? 'active' : 'inactive'}`}>联网搜索</span>
            <Switch size="small" checked={webSearch} onChange={onWebSearchChange} />
          </div>

          <div style={{ flex: 1 }} />

          {/* 消息数 */}
          {messageCount > 0 && (
            <Text type="secondary" className="chat-msg-count">
              {messageCount} 条
            </Text>
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
            disabled={!sending && !inputValue.trim()}
          />
        </div>
      )}
    </div>
  );
}

/* ─── 协作查询步骤面板 ─── */
function CollabStepsPanel({ collabAgents, isCollabMode }) {
  const [selectedIdx, setSelectedIdx] = useState(null);

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
      <Steps
        direction="horizontal"
        current={selectedIdx !== null ? selectedIdx : -1}
        items={collabAgents.map((agent, idx) => ({
          title: (
            <span
              style={{
                fontSize: '13px',
                fontWeight: selectedIdx === idx ? 600 : 500,
                cursor: 'pointer',
                color: selectedIdx === idx ? '#6c5ce7' : 'inherit',
                background: selectedIdx === idx ? 'rgba(108, 92, 231, 0.12)' : 'transparent',
                padding: selectedIdx === idx ? '2px 6px' : '2px 0',
                borderRadius: '4px',
              }}
              onClick={() => setSelectedIdx(selectedIdx === idx ? null : idx)}
            >
              {agent.display_name}
            </span>
          ),
          description: (
            <span style={{ fontSize: '11px', color: selectedIdx === idx ? '#6c5ce7' : '#999' }}>
              {selectedIdx === idx ? agent.status === 'success' ? '点击查看结果' : '无匹配数据' : agent.status === 'success' ? '查询完成' : '无匹配数据'}
            </span>
          ),
          status: agent.status === 'success' ? 'finish' : 'error',
        }))}
      />
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

function ChatInterface({ sessionId = 'default', initialMessage = null, initialUseAgent = false, initialWebSearch = false, selectedAgent = null }) {
  const { message } = App.useApp();
  // 使用全局会话状态
  const { state, addMessage, setMessages, updateConversation } = useConversationStore();
  const { createConversation, restoreConversation, currentConversation } = useConversation();
  
  const [inputValue, setInputValue] = useState('');
  const [loading, setLoading] = useState(false);
  const [sending, setSending] = useState(false);
  const [copiedId, setCopiedId] = useState(null);
  const [models, setModels] = useState([]);  // 动态模型列表
  const [currentModel, setCurrentModel] = useState('qwen3.6-plus');
  const [agents, setAgents] = useState([]);  // Agent 列表
  const [currentAgent, setCurrentAgent] = useState(null);  // 当前响应 Agent（从 SSE agent_info 获取）
  const [selectedAgentName, setSelectedAgentName] = useState(null);  // 用户选择的 Agent（null=默认通用）
  const [useAgent, setUseAgent] = useState(initialUseAgent);  // 协作模式
  const [enableThinking, setEnableThinking] = useState(false);  // 深度思考
  const [webSearch, setWebSearch] = useState(initialWebSearch);  // 联网搜索模式
  const messagesEndRef = useRef(null);
  const [mentionVisible, setMentionVisible] = useState(false);  // @ 提及面板可见性
  const [mentionFilter, setMentionFilter] = useState('');  // @ 提及过滤文本
  const inputRef = useRef(null);
  const abortControllerRef = useRef(null); // 用于取消请求
  const messagesRef = useRef([]); // 用于存储最新消息的ref
  const initialMessageSentRef = useRef(false); // 标记初始消息是否已发送
  const isCreatingConversationRef = useRef(false); // 标记是否正在创建会话
  const agentInfoRef = useRef(null); // 用于存储当前 Agent 信息
  const streamingMessageIdRef = useRef(null); // 用于追踪当前流式消息的 ID
  
  // 使用全局消息或本地消息
  const messages = Array.isArray(state.messages) ? state.messages : [];
  
  // 更新messagesRef
  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  // 根据URL参数更新协作和联网搜索状态
  useEffect(() => {
    if (initialUseAgent) {
      setUseAgent(true);
    }
    if (initialWebSearch) {
      setWebSearch(true);
    }
  }, [initialUseAgent, initialWebSearch]);

  // 同步外部传入的 Agent 选择
  useEffect(() => {
    if (selectedAgent) {
      setSelectedAgentName(selectedAgent.name);
    }
  }, [selectedAgent]);

  // 自动发送初始消息
  useEffect(() => {
    if (initialMessage && !initialMessageSentRef.current && !sending) {
      initialMessageSentRef.current = true;
      // 延迟发送,等待组件完全加载
      setTimeout(() => {
        sendMessage(initialMessage, initialUseAgent, initialWebSearch, false);
      }, 500);
    }
  }, [initialMessage, sending, initialUseAgent, initialWebSearch]);

  // 加载模型列表
  useEffect(() => {
    loadModels();
    loadAgents();
  }, []);

  // 页面刷新后恢复上次会话
  useEffect(() => {
    if (!state.currentConversation?.id && !initialMessage) {
      restoreConversation();
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const loadModels = async () => {
    try {
      const modelList = await chatService.getModels();
      // 只提取 key 和 label，避免后端返回的布尔属性（如 enable_thinking）被传到 DOM
      const menuItems = modelList.map(m => ({ key: m.key, label: m.label }));
      setModels(menuItems);
    } catch (error) {
      console.error('加载模型列表失败:', error);
      // 使用默认模型列表
      setModels([
        { key: 'qwen3.6-plus', label: 'Qwen 3.6 Plus' },
        { key: 'deepseek-reasoner', label: 'DeepSeek R1' },
      ]);
    }
  };

  const loadAgents = async () => {
    try {
      const agentList = await getAgents();
      setAgents(Array.isArray(agentList) ? agentList : []);
    } catch (error) {
      console.error('加载 Agent 列表失败:', error);
    }
  };

  // 加载会话历史
  useEffect(() => {
    loadHistory();
  }, [state.currentConversation?.id]);

  // 自动滚动到底部(消息数量增加或正在发送时滚动,展开/折叠不触发)
  const prevMessageCountRef = useRef(0);
  useEffect(() => {
    const currentCount = messages.length;
    if (currentCount > prevMessageCountRef.current || sending) {
      scrollToBottom();
    }
    prevMessageCountRef.current = currentCount;
  }, [messages, sending]);

  // 自动聚焦输入框
  useEffect(() => {
    if (!sending && inputRef.current) {
      inputRef.current.focus();
    }
  }, [sending]);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  // 停止生成
  const stopGeneration = () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      setSending(false);
      message.info('已停止生成');
    }
  };

  const loadHistory = async () => {
    // 如果正在创建会话，不加载历史（避免清空刚添加的消息）
    if (isCreatingConversationRef.current) {
      return;
    }
    
    setLoading(true);
    try {
      // 使用新的会话ID加载历史
      const conversationId = state.currentConversation?.id;
      if (!conversationId) {
        setMessages([]);
        return;
      }
      
      const response = await conversationService.getMessages(conversationId);
      if (response && response.messages && response.messages.length > 0) {
        const formattedMessages = response.messages.map((msg) => {
          const meta = msg.metadata || {};
          return {
            id: msg.id,
            content: msg.content,
            role: msg.role === 'user' ? 'user' : 'agent',
            timestamp: new Date(msg.created_at),
            collabAgents: meta.collab_agents || [],
            isCollabComplete: !!meta.collab_agents,
            agentInfo: meta.agent_info || null,
          };
        });
        setMessages(formattedMessages);
      } else {
        setMessages([]);
      }
    } catch (error) {
      console.log('无历史记录或加载失败:', error.message);
      setMessages([]);
    } finally {
      setLoading(false);
    }
  };

  const sendMessage = async (messageContent = null, forceUseAgent = null, forceWebSearch = null, forceEnableThinking = null) => {
    const contentToSend = messageContent || inputValue;
    if (!contentToSend.trim()) {
      message.warning('请输入消息');
      return;
    }

    // 使用强制参数或当前状态
    const finalUseAgent = forceUseAgent !== null ? forceUseAgent : useAgent;
    const finalEnableThinking = forceEnableThinking !== null ? forceEnableThinking : enableThinking;
    const finalWebSearch = forceWebSearch !== null ? forceWebSearch : webSearch;

    // 如果没有当前对话,先创建一个
    let conversationId = state.currentConversation?.id;
    if (!conversationId) {
      try {
        // 标记正在创建会话，避免 loadHistory 清空消息
        isCreatingConversationRef.current = true;
        // 截取第一段文本作为标题(最多20个字符)
        const title = contentToSend.trim().substring(0, 20) + (contentToSend.trim().length > 20 ? '...' : '');
        const newConversation = await createConversation(title);
        conversationId = newConversation.id;
        // 创建完成后，重置标志
        setTimeout(() => {
          isCreatingConversationRef.current = false;
        }, 1000);
      } catch (error) {
        isCreatingConversationRef.current = false;
        message.error('创建对话失败');
        return;
      }
    }

    const userMessage = {
      id: Date.now(),
      content: contentToSend,
      role: 'user',
      timestamp: new Date(),
    };

    addMessage(userMessage);
    const currentInput = contentToSend;
    setInputValue('');
    setSending(true);

    // 创建AbortController用于取消请求
    abortControllerRef.current = new AbortController();

    // 创建AI消息占位符
    const agentMessageId = Date.now() + 1;
    const agentMessage = {
      id: agentMessageId,
      content: '',
      role: 'agent',
      timestamp: new Date(),
      thinking: false,
      thinkingContent: '',
      streaming: true,
    };

    addMessage(agentMessage);

    // 追踪当前流式消息 ID，只有该消息使用流式渲染
    streamingMessageIdRef.current = agentMessageId;
    const isStreamingRef = { current: true };

    // 使用ref来累积内容，避免状态更新延迟问题
    const contentRef = { current: '' };
    const thinkingContentRef = { current: '' };
    const isThinkingActiveRef = { current: false }; // 追踪"正在思考"状态
    const isCollabModeRef = { current: false }; // 追踪"协作模式"状态
    const collabAgentsRef = { current: [] }; // 协作 Agent 列表
    const isCollabCompleteRef = { current: false }; // 协作完成（内容需以 Markdown 渲染）
    // 节流机制：用 setTimeout 控制渲染频率，每 100ms 至少一次渲染
    let lastUpdateTime = 0;
    const THROTTLE_MS = 100;

    const scheduleUpdate = () => {
      const now = Date.now();
      const elapsed = now - lastUpdateTime;
      if (elapsed < THROTTLE_MS) {
        // 延迟到剩余时间后渲染
        setTimeout(() => {
          lastUpdateTime = Date.now();
          flushUpdate();
        }, THROTTLE_MS - elapsed);
      } else {
        lastUpdateTime = Date.now();
        flushUpdate();
      }
    };

    const flushUpdate = () => {
      const currentMessages = messagesRef.current;
      const msgIndex = currentMessages.findIndex(m => m.id === agentMessageId);
      if (msgIndex !== -1) {
        const newMessages = [...currentMessages];
        newMessages[msgIndex] = {
          ...newMessages[msgIndex],
          thinking: isThinkingActiveRef.current,
          thinkingContent: thinkingContentRef.current,
          content: contentRef.current,
          agentInfo: agentInfoRef.current,
          isCollabMode: isCollabModeRef.current,
          isCollabComplete: isCollabCompleteRef.current,
          collabAgents: [...collabAgentsRef.current],
          streaming: isStreamingRef.current,
        };
        setMessages(newMessages);
      } else {
        console.warn('[scheduleUpdate] message not found! agentMessageId:', agentMessageId, 'message_ids:', currentMessages.map(m => m.id));
      }
    };

    try {
      // 重置当前 Agent 信息
      setCurrentAgent(null);

      await sendMessageStream(
        {
          conversation_id: conversationId,
          content: currentInput,
          model_name: currentModel,
          agent_name: selectedAgentName,
          use_agent: finalUseAgent,
          web_search: finalWebSearch,
          enable_memory: true,
          enable_thinking: finalEnableThinking || null,
        },
        (type, content) => {
          if (type === 'agent_info') {
            // content is already the parsed object from messageService
            const info = typeof content === 'string' ? JSON.parse(content) : content;
            setCurrentAgent(info);
            agentInfoRef.current = info;
            // 立即更新消息，让 agentInfo 显示出来
            const currentMessages = messagesRef.current;
            const msgIndex = currentMessages.findIndex(m => m.id === agentMessageId);
            if (msgIndex !== -1) {
              const newMessages = [...currentMessages];
              newMessages[msgIndex] = { ...newMessages[msgIndex], agentInfo: info };
              setMessages(newMessages);
            }
          } else if (type === 'thinking') {
            isThinkingActiveRef.current = true;
            thinkingContentRef.current += content;
            scheduleUpdate();
          } else if (type === 'content') {
            // 收到正文内容时，思考过程已结束
            isThinkingActiveRef.current = false;
            contentRef.current += content;
            scheduleUpdate();
          } else if (type === 'collab_start') {
            // 协作模式开始
            isCollabModeRef.current = true;
            collabAgentsRef.current = [];
            scheduleUpdate();
          } else if (type === 'collab_agent') {
            // 单个 Agent 协作结果
            try {
              const agent = typeof content === 'string' ? JSON.parse(content) : content;
              collabAgentsRef.current.push(agent);
              scheduleUpdate();
            } catch (e) {
              console.error('解析 collab_agent 数据失败:', e);
            }
          } else if (type === 'collab_done') {
            // 协作完成，content 立即以 Markdown 渲染
            isCollabModeRef.current = false;
            isCollabCompleteRef.current = true;
            scheduleUpdate();
          } else if (type === 'metadata') {
            // 元数据（如协作 Agent 列表）
            try {
              const meta = typeof content === 'string' ? JSON.parse(content) : content;
              if (meta.collab_agents) {
                collabAgentsRef.current = meta.collab_agents;
                scheduleUpdate();
              }
            } catch (e) {
              console.error('解析 metadata 失败:', e);
            }
          } else if (type === 'error') {
            // 错误消息立即更新，不走节流
            const currentMessages = messagesRef.current;
            const msgIndex = currentMessages.findIndex(m => m.id === agentMessageId);
            if (msgIndex !== -1) {
              const newMessages = [...currentMessages];
              newMessages[msgIndex] = {
                ...newMessages[msgIndex],
                thinking: false,
                content: '错误: ' + content,
                isError: true
              };
              setMessages(newMessages);
            }
          }
        },
        abortControllerRef.current.signal
      );

      // 流式输出完成后，清除 streaming 标志
      isStreamingRef.current = false;
      const finalMessages = messagesRef.current;
      const finalMsgIndex = finalMessages.findIndex(m => m.id === agentMessageId);
      if (finalMsgIndex !== -1) {
        const newMessages = [...finalMessages];
        newMessages[finalMsgIndex] = {
          ...newMessages[finalMsgIndex],
          thinking: false,
          thinkingContent: thinkingContentRef.current,
          content: contentRef.current,
          agentInfo: agentInfoRef.current,
          isCollabMode: isCollabModeRef.current,
          isCollabComplete: isCollabCompleteRef.current,
          collabAgents: [...collabAgentsRef.current],
          streaming: false,
        };
        setMessages(newMessages);
      }
      streamingMessageIdRef.current = null;
    } catch (error) {
      // 如果是取消请求,不显示错误
      if (error.name === 'AbortError') {
        isStreamingRef.current = false;
        streamingMessageIdRef.current = null;
        const currentMessages = messagesRef.current;
        const newMessages = [...currentMessages];
        const msgIndex = newMessages.findIndex(m => m.id === agentMessageId);
        if (msgIndex !== -1) {
          newMessages[msgIndex] = {
            ...newMessages[msgIndex],
            thinking: false,
            content: newMessages[msgIndex].content || '已停止生成',
            isStopped: true,
            streaming: false,
          };
        }
        setMessages(newMessages);
      } else {
        isStreamingRef.current = false;
        streamingMessageIdRef.current = null;
        // 更新错误消息
        const currentMessages = messagesRef.current;
        const newMessages = [...currentMessages];
        const msgIndex = newMessages.findIndex(m => m.id === agentMessageId);
        if (msgIndex !== -1) {
          newMessages[msgIndex] = {
            ...newMessages[msgIndex],
            thinking: false,
            content: '发送消息失败: ' + error.message,
            isError: true,
            streaming: false,
          };
        }
        setMessages(newMessages);
      }
    } finally {
      setSending(false);
      streamingMessageIdRef.current = null;
      // 发送完成后静默更新当前会话标题，不刷新整个列表避免闪烁
      if (conversationId) {
        try {
          const updatedConv = await conversationService.getById(conversationId);
          if (updatedConv) {
            updateConversation(updatedConv);
          }
        } catch (error) {
          console.error('更新会话标题失败:', error);
        }
      }
    }
  };

  const handleInputChange = (e) => {
    const value = e.target.value;
    setInputValue(value);

    // 检测 @ 提及
    const cursorPos = e.target.selectionStart;
    const textBeforeCursor = value.slice(0, cursorPos);
    const mentionMatch = textBeforeCursor.match(/@([\w一-龥]*)$/);
    if (mentionMatch) {
      setMentionFilter(mentionMatch[1]);
      setMentionVisible(true);
    } else {
      setMentionVisible(false);
      setMentionFilter('');
    }
  };

  const handleMentionSelect = (agent) => {
    // 替换输入框中最后一个 @xxx 为 @Agent名称
    const cursorPos = inputRef.current?.resizableTextArea?.textArea?.selectionStart || inputValue.length;
    const textBefore = inputValue.slice(0, cursorPos);
    const textAfter = inputValue.slice(cursorPos);
    const newText = textBefore.replace(/@[\w一-龥]*$/, `@${agent.display_name} `) + textAfter;
    setInputValue(newText);
    setSelectedAgentName(agent.name);
    setMentionVisible(false);
    setMentionFilter('');
    // 延迟聚焦避免失焦
    setTimeout(() => inputRef.current?.focus(), 50);
  };

  const getFilteredAgents = () => {
    if (!mentionFilter) return agents;
    return agents.filter(a =>
      a.display_name.includes(mentionFilter) ||
      a.name.includes(mentionFilter.toLowerCase()) ||
      a.description.includes(mentionFilter)
    );
  };

  const handleKeyPress = (e) => {
    if (mentionVisible && agents.length > 0) {
      if (e.key === 'Enter') {
        e.preventDefault();
        const filtered = getFilteredAgents();
        if (filtered.length > 0) {
          handleMentionSelect(filtered[0]);
          return;
        }
      }
      if (e.key === 'Escape' || e.key === 'Tab') {
        setMentionVisible(false);
        return;
      }
    }
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  const clearChat = async () => {
    try {
      await chatService.clearSession(sessionId);
      setMessages([]);
      message.success('会话已清除');
    } catch (error) {
      message.error('清除会话失败');
    }
  };

  const copyToClipboard = (text, id) => {
    navigator.clipboard.writeText(text).then(() => {
      setCopiedId(id);
      message.success('已复制到剪贴板');
      setTimeout(() => setCopiedId(null), 2000);
    }).catch(() => {
      message.error('复制失败');
    });
  };

  const formatTime = (timestamp) => {
    return new Date(timestamp).toLocaleTimeString('zh-CN', {
      hour: '2-digit',
      minute: '2-digit',
    });
  };

  const renderMessage = (item) => {
    const isUser = item.role === 'user';
    const isAgent = item.role === 'agent';
    const agentInfo = item.agentInfo || null;
    const avatarColor = isUser ? '#6c5ce7' : (agentInfo?.color || '#00b894');
    const agentName = isUser ? '用户' : (agentInfo?.display_name || 'AI助手');
    const agentIcon = agentInfo?.icon || '';
    const nameColor = isUser ? '#6c5ce7' : (agentInfo?.color || '#00b894');
    // 只对当前正在流式输出的消息使用 streaming=true，历史消息始终用完整 markdown 格式
    // 协作模式下，协作完成后 content 需要立即以 Markdown 渲染
    const isStreaming = item.streaming === true;

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
            <Text strong style={{ color: nameColor }}>
              {isUser ? '用户' : `${agentIcon} ${agentName}`}
            </Text>
            <Text type="secondary" style={{ fontSize: '12px', marginLeft: '8px' }}>
              {formatTime(item.timestamp)}
            </Text>
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
                onClick={() => {
                  const currentMessages = messagesRef.current;
                  const newMessages = currentMessages.map(m => 
                    m.id === item.id ? { ...m, thinkingExpanded: !m.thinkingExpanded } : m
                  );
                  setMessages(newMessages);
                }}
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
              <Paragraph
                style={{
                  margin: 0,
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                  color: item.isError ? '#ff4d4f' : 'inherit',
                }}
              >
                {item.content}
              </Paragraph>
            )}
          </div>
          {isAgent && !item.isError && (
            <Tooltip title={copiedId === item.id ? '已复制' : '复制'}>
              <Button
                type="text"
                size="small"
                icon={copiedId === item.id ? <CheckOutlined /> : <CopyOutlined />}
                onClick={() => copyToClipboard(item.content, item.id)}
                style={{ marginTop: '4px', padding: '0 4px' }}
              />
            </Tooltip>
          )}
        </div>
      </div>
    );
  };

  // 新对话时输入框居中，有消息后输入框靠底部
  if (messages.length === 0) {
    if (loading) {
      return (
        <div style={{ height: '100%', display: 'flex', justifyContent: 'center', alignItems: 'center' }}>
          <Spin tip="加载中..."><div /></Spin>
        </div>
      );
    }
    return (
      <div style={{ height: '100%', display: 'flex', flexDirection: 'column', justifyContent: 'center', alignItems: 'center' }}>
        <div style={{ maxWidth: '800px', width: '100%', padding: '0 24px' }}>
          <div style={{ textAlign: 'center', marginBottom: '24px' }}>
            <div style={{ fontSize: '28px', fontWeight: 600, color: '#6c5ce7', marginBottom: '8px' }}>AI 智能助手</div>
            <Text type="secondary" style={{ fontSize: '14px' }}>输入消息开始对话，按 Enter 发送</Text>
          </div>
          <ChatInputBar
            inputRef={inputRef}
            inputValue={inputValue}
            sending={sending}
            mentionVisible={false}
            mentionFilter=""
            agents={agents}
            filteredAgents={[]}
            models={models}
            currentModel={currentModel}
            selectedAgentName={selectedAgentName}
            useAgent={useAgent}
            enableThinking={enableThinking}
            webSearch={webSearch}
            messageCount={0}
            showExtras={true}
            onInputChange={handleInputChange}
            onKeyPress={handleKeyPress}
            onSend={() => sendMessage()}
            onStop={stopGeneration}
            onMentionSelect={handleMentionSelect}
            onModelChange={(key) => { setCurrentModel(key); message.success(`已切换到 ${models.find(m => m.key === key)?.label}`); }}
            onAgentChange={(key) => setSelectedAgentName(key || null)}
            onUseAgentChange={setUseAgent}
            onEnableThinkingChange={setEnableThinking}
            onWebSearchChange={setWebSearch}
            onClear={clearChat}
          />
        </div>
      </div>
    );
  }

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      {/* 消息列表 - 滚动容器占满宽度,滚动条在最右侧 */}
      <div style={{ flex: 1, overflow: 'auto', padding: '12px 12px 12px 0', width: '100%' }} className="chat-scroll-area">
        <div style={{ maxWidth: '800px', margin: '0 auto', padding: '0 12px' }}>
        <Spin spinning={loading} tip="加载中...">
          {messages.length === 0 ? (
            <Empty
              description="加载中..."
              style={{ marginTop: '100px' }}
            />
          ) : (
            <div>
              {messages.map((item) => (
                <div key={item.id}>{renderMessage(item)}</div>
              ))}
            </div>
          )}
          <div ref={messagesEndRef} />
        </Spin>
        </div>
      </div>

      {/* 输入区域 */}
      <div style={{ padding: '16px', background: '#ffffff', borderTop: '1px solid rgba(108, 92, 231, 0.08)', width: '100%' }}>
        <div style={{ maxWidth: '800px', margin: '0 auto' }}>
          <ChatInputBar
            inputRef={inputRef}
            inputValue={inputValue}
            sending={sending}
            mentionVisible={mentionVisible}
            mentionFilter={mentionFilter}
            agents={agents}
            filteredAgents={getFilteredAgents()}
            models={models}
            currentModel={currentModel}
            selectedAgentName={selectedAgentName}
            useAgent={useAgent}
            enableThinking={enableThinking}
            webSearch={webSearch}
            messageCount={messages.length}
            showExtras={true}
            onInputChange={handleInputChange}
            onKeyPress={handleKeyPress}
            onSend={() => sendMessage()}
            onStop={stopGeneration}
            onMentionSelect={handleMentionSelect}
            onModelChange={(key) => { setCurrentModel(key); message.success(`已切换到 ${models.find(m => m.key === key)?.label}`); }}
            onAgentChange={(key) => setSelectedAgentName(key || null)}
            onUseAgentChange={setUseAgent}
            onEnableThinkingChange={setEnableThinking}
            onWebSearchChange={setWebSearch}
            onClear={clearChat}
          />
        </div>
      </div>
    </div>
  );
}

export default ChatInterface;
