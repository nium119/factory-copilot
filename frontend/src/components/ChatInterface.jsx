import React, { useState, useEffect, useRef, useCallback } from 'react';
import { App } from 'antd';
import chatService from '../services/chatService';
import { sendMessageStream, getAgents } from '../services/messageService';
import * as conversationService from '../services/conversationService';
import { useConversationStore } from '../stores/ConversationContext';
import { useConversation } from '../hooks/useConversation';
import './ChatInterface.css';
import ChatInputBar from './ChatInterface/ChatInputBar';
import MessageList from './ChatInterface/MessageList';
import WelcomeScreen from './ChatInterface/WelcomeScreen';

function ChatInterface({ sessionId = 'default', initialMessage = null, initialUseAgent = false /* 已废弃，后端自动路由 */, initialWebSearch = false, selectedAgent = null }) {
  const { message } = App.useApp();
  // 使用全局会话状态
  const { state, addMessage, setMessages, updateConversation } = useConversationStore();
  const { createConversation, restoreConversation, currentConversation } = useConversation();

  const [inputValue, setInputValue] = useState('');
  const [loading, setLoading] = useState(false);
  const [sending, setSending] = useState(false);
  const [copiedId, setCopiedId] = useState(null);
  const [models, setModels] = useState([]);
  const [currentModel, setCurrentModel] = useState('qwen3.6-plus');
  const [agents, setAgents] = useState([]);
  const [currentAgent, setCurrentAgent] = useState(null);
  const [selectedAgentName, setSelectedAgentName] = useState(null);
  // const [useAgent, setUseAgent] = useState(initialUseAgent); // 已废弃，后端自动路由协作
  const useAgent = false; // 固定为 false，由后端 router 自动判断是否触发协作
  const [enableThinking, setEnableThinking] = useState(false);
  const [webSearch, setWebSearch] = useState(initialWebSearch);
  const messagesEndRef = useRef(null);
  const [mentionVisible, setMentionVisible] = useState(false);
  const [mentionFilter, setMentionFilter] = useState('');
  const inputRef = useRef(null);
  const abortControllerRef = useRef(null);
  const messagesRef = useRef([]);
  const initialMessageSentRef = useRef(false);
  const isCreatingConversationRef = useRef(false);
  const agentInfoRef = useRef(null);
  const streamingMessageIdRef = useRef(null);

  // 使用全局消息或本地消息
  const messages = Array.isArray(state.messages) ? state.messages : [];

  // 更新messagesRef
  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  // 根据URL参数更新协作和联网搜索状态
  useEffect(() => {
    if (initialUseAgent) setUseAgent(true);
    if (initialWebSearch) setWebSearch(true);
  }, [initialUseAgent, initialWebSearch]);

  // 同步外部传入的 Agent 选择
  useEffect(() => {
    if (selectedAgent) setSelectedAgentName(selectedAgent.name);
  }, [selectedAgent]);

  // 自动发送初始消息
  useEffect(() => {
    if (initialMessage && !initialMessageSentRef.current && !sending) {
      initialMessageSentRef.current = true;
      setTimeout(() => {
        sendMessage(initialMessage, initialUseAgent, initialWebSearch, false);
      }, 500);
    }
  }, [initialMessage, sending, initialUseAgent, initialWebSearch]);

  // 加载模型列表和 Agent 列表
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

  // 加载会话历史
  useEffect(() => {
    loadHistory();
  }, [state.currentConversation?.id]);

  // 自动滚动到底部
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

  const stopGeneration = () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      setSending(false);
      message.info('已停止生成');
    }
  };

  const loadHistory = async () => {
    if (isCreatingConversationRef.current) return;

    setLoading(true);
    try {
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

  const loadModels = async () => {
    try {
      const modelList = await chatService.getModels();
      const menuItems = modelList.map(m => ({ key: m.key, label: m.label }));
      setModels(menuItems);
    } catch (error) {
      console.error('加载模型列表失败:', error);
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

  const sendMessage = async (messageContent = null, forceUseAgent = null, forceWebSearch = null, forceEnableThinking = null) => {
    const contentToSend = messageContent || inputValue;
    if (!contentToSend.trim()) {
      message.warning('请输入消息');
      return;
    }

    const finalUseAgent = forceUseAgent !== null ? forceUseAgent : useAgent;
    const finalEnableThinking = forceEnableThinking !== null ? forceEnableThinking : enableThinking;
    const finalWebSearch = forceWebSearch !== null ? forceWebSearch : webSearch;

    let conversationId = state.currentConversation?.id;
    if (!conversationId) {
      try {
        isCreatingConversationRef.current = true;
        const title = contentToSend.trim().substring(0, 20) + (contentToSend.trim().length > 20 ? '...' : '');
        const newConversation = await createConversation(title);
        conversationId = newConversation.id;
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

    abortControllerRef.current = new AbortController();

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

    streamingMessageIdRef.current = agentMessageId;
    const isStreamingRef = { current: true };

    const contentRef = { current: '' };
    const thinkingContentRef = { current: '' };
    const isThinkingActiveRef = { current: false };
    const isCollabModeRef = { current: false };
    const collabAgentsRef = { current: [] };
    const isCollabCompleteRef = { current: false };

    let lastUpdateTime = 0;
    const THROTTLE_MS = 100;

    const scheduleUpdate = () => {
      const now = Date.now();
      const elapsed = now - lastUpdateTime;
      if (elapsed < THROTTLE_MS) {
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
          enable_thinking: finalEnableThinking,
        },
        (type, content) => {
          if (type === 'agent_info') {
            const info = typeof content === 'string' ? JSON.parse(content) : content;
            setCurrentAgent(info);
            agentInfoRef.current = info;
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
            isThinkingActiveRef.current = false;
            contentRef.current += content;
            scheduleUpdate();
          } else if (type === 'collab_start') {
            isCollabModeRef.current = true;
            collabAgentsRef.current = [];
            scheduleUpdate();
          } else if (type === 'collab_agent') {
            try {
              const agent = typeof content === 'string' ? JSON.parse(content) : content;
              collabAgentsRef.current.push(agent);
              scheduleUpdate();
            } catch (e) {
              console.error('解析 collab_agent 数据失败:', e);
            }
          } else if (type === 'collab_done') {
            isCollabModeRef.current = false;
            isCollabCompleteRef.current = true;
            scheduleUpdate();
          } else if (type === 'metadata') {
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
      if (conversationId) {
        try {
          const updatedConv = await conversationService.getById(conversationId);
          if (updatedConv) updateConversation(updatedConv);
        } catch (error) {
          console.error('更新会话标题失败:', error);
        }
      }
    }
  };

  const handleInputChange = (e) => {
    const value = e.target.value;
    setInputValue(value);

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
    const cursorPos = inputRef.current?.resizableTextArea?.textArea?.selectionStart || inputValue.length;
    const textBefore = inputValue.slice(0, cursorPos);
    const textAfter = inputValue.slice(cursorPos);
    const newText = textBefore.replace(/@[\w一-龥]*$/, `@${agent.display_name} `) + textAfter;
    setInputValue(newText);
    setSelectedAgentName(agent.name);
    setMentionVisible(false);
    setMentionFilter('');
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

  const handleToggleThinking = (id) => {
    const currentMessages = messagesRef.current;
    const newMessages = currentMessages.map(m =>
      m.id === id ? { ...m, thinkingExpanded: !m.thinkingExpanded } : m
    );
    setMessages(newMessages);
  };

  const handleModelChange = (key) => {
    setCurrentModel(key);
    message.success(`已切换到 ${models.find(m => m.key === key)?.label}`);
  };

  // 构建 ChatInputBar 元素（复用）
  const renderChatInputBar = () => (
    <ChatInputBar
      inputRef={inputRef}
      inputValue={inputValue}
      sending={sending}
      mentionVisible={mentionVisible}
      filteredAgents={getFilteredAgents()}
      models={models}
      currentModel={currentModel}
      selectedAgentName={selectedAgentName}
      useAgent={useAgent}
      enableThinking={enableThinking}
      webSearch={webSearch}
      messageCount={messages.length}
      agents={agents}
      onInputChange={handleInputChange}
      onKeyPress={handleKeyPress}
      onSend={() => sendMessage()}
      onStop={stopGeneration}
      onMentionSelect={handleMentionSelect}
      onModelChange={handleModelChange}
      onAgentChange={(key) => setSelectedAgentName(key || null)}
      // onUseAgentChange={setUseAgent} // 已废弃，后端自动路由
      onEnableThinkingChange={setEnableThinking}
      onWebSearchChange={setWebSearch}
      onClear={clearChat}
    />
  );

  // 新对话时输入框居中
  if (messages.length === 0) {
    return (
      <WelcomeScreen
        chatInputBar={renderChatInputBar()}
      />
    );
  }

  // 有消息后输入框靠底部
  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      <MessageList
        messages={messages}
        copiedId={copiedId}
        onCopy={copyToClipboard}
        onToggleThinking={handleToggleThinking}
        messagesEndRef={messagesEndRef}
      />

      {/* 输入区域 */}
      <div style={{ padding: '16px', background: '#ffffff', borderTop: '1px solid rgba(108, 92, 231, 0.08)', width: '100%' }}>
        <div style={{ maxWidth: '800px', margin: '0 auto' }}>
          {renderChatInputBar()}
        </div>
      </div>
    </div>
  );
}

export default ChatInterface;
