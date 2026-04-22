import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Input, Button, List, Avatar, Space, message, Spin, Empty, Typography, Tooltip, Tag, Upload, Dropdown, Switch } from 'antd';
import { SendOutlined, UserOutlined, RobotOutlined, ClearOutlined, ReloadOutlined, CopyOutlined, CheckOutlined, AudioOutlined, PaperClipOutlined, PictureOutlined, ThunderboltOutlined, StopOutlined, SearchOutlined, SwapOutlined } from '@ant-design/icons';
import chatService from '../services/chatService';
import { sendMessageStream } from '../services/messageService';
import * as conversationService from '../services/conversationService';
import ToolCallDisplay from './ToolCallDisplay';
import MarkdownRenderer from './MarkdownRenderer';
import { useConversationStore } from '../stores/ConversationContext';
import { useConversation } from '../hooks/useConversation';

const { TextArea } = Input;
const { Text, Paragraph } = Typography;

function ChatInterface({ sessionId = 'default', initialMessage = null, initialDeepThinking = false, initialWebSearch = false }) {
  // 使用全局会话状态
  const { state, addMessage, setMessages, updateConversation } = useConversationStore();
  const { createConversation, currentConversation } = useConversation();
  
  const [inputValue, setInputValue] = useState('');
  const [loading, setLoading] = useState(false);
  const [sending, setSending] = useState(false);
  const [copiedId, setCopiedId] = useState(null);
  const [models, setModels] = useState([]);  // 动态模型列表
  const [currentModel, setCurrentModel] = useState('qwen3.6-plus');
  const [deepThinking, setDeepThinking] = useState(initialDeepThinking);  // 深度思考模式
  const [webSearch, setWebSearch] = useState(initialWebSearch);  // 联网搜索模式
  const messagesEndRef = useRef(null);
  const inputRef = useRef(null);
  const abortControllerRef = useRef(null); // 用于取消请求
  const messagesRef = useRef([]); // 用于存储最新消息的ref
  const initialMessageSentRef = useRef(false); // 标记初始消息是否已发送
  const isCreatingConversationRef = useRef(false); // 标记是否正在创建会话
  
  // 使用全局消息或本地消息
  const messages = Array.isArray(state.messages) ? state.messages : [];
  
  // 更新messagesRef
  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  // 根据URL参数更新深度思考和联网搜索状态
  useEffect(() => {
    if (initialDeepThinking) {
      setDeepThinking(true);
    }
    if (initialWebSearch) {
      setWebSearch(true);
    }
  }, [initialDeepThinking, initialWebSearch]);

  // 自动发送初始消息
  useEffect(() => {
    if (initialMessage && !initialMessageSentRef.current && !sending) {
      initialMessageSentRef.current = true;
      // 延迟发送,等待组件完全加载
      setTimeout(() => {
        // 直接传递深度思考和联网搜索参数
        sendMessage(initialMessage, initialDeepThinking, initialWebSearch);
      }, 500);
    }
  }, [initialMessage, sending, initialDeepThinking, initialWebSearch]);

  // 加载模型列表
  useEffect(() => {
    loadModels();
  }, []);

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
        const formattedMessages = response.messages.map((msg) => ({
          id: msg.id,
          content: msg.content,
          role: msg.role === 'user' ? 'user' : 'agent',
          timestamp: new Date(msg.created_at),
        }));
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

  const sendMessage = async (messageContent = null, forceDeepThinking = null, forceWebSearch = null) => {
    const contentToSend = messageContent || inputValue;
    if (!contentToSend.trim()) {
      message.warning('请输入消息');
      return;
    }

    // 使用强制参数或当前状态
    const useDeepThinking = forceDeepThinking !== null ? forceDeepThinking : deepThinking;
    const useWebSearch = forceWebSearch !== null ? forceWebSearch : webSearch;

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
    };

    addMessage(agentMessage);
    
    // 使用ref来累积内容，避免状态更新延迟问题
    const contentRef = { current: '' };
    const thinkingContentRef = { current: '' };
    const isThinkingActiveRef = { current: false }; // 追踪"正在思考"状态
    // 节流机制：用 requestAnimationFrame 控制渲染频率，避免每个chunk都触发重渲染
    const rafIdRef = { current: null };
    const pendingUpdateRef = { current: false };

    const scheduleUpdate = () => {
      if (pendingUpdateRef.current) return; // 已有待处理的更新，跳过
      pendingUpdateRef.current = true;
      rafIdRef.current = requestAnimationFrame(() => {
        pendingUpdateRef.current = false;
        const currentMessages = messagesRef.current;
        const msgIndex = currentMessages.findIndex(m => m.id === agentMessageId);
        if (msgIndex !== -1) {
          const newMessages = [...currentMessages];
          newMessages[msgIndex] = { 
            ...newMessages[msgIndex], 
            thinking: isThinkingActiveRef.current,
            thinkingContent: thinkingContentRef.current,
            content: contentRef.current,
          };
          setMessages(newMessages);
        }
      });
    };

    try {
      await sendMessageStream(
        {
          conversation_id: conversationId,
          content: currentInput,
          model_name: currentModel,
          use_agent: useDeepThinking,
          web_search: useWebSearch,
          enable_memory: true,
        },
        (type, content) => {
          if (type === 'thinking') {
            isThinkingActiveRef.current = true;
            thinkingContentRef.current += content;
            scheduleUpdate();
          } else if (type === 'content') {
            // 收到正文内容时，思考过程已结束
            isThinkingActiveRef.current = false;
            contentRef.current += content;
            scheduleUpdate();
          } else if (type === 'error') {
            // 错误消息立即更新，不走节流
            if (rafIdRef.current) cancelAnimationFrame(rafIdRef.current);
            pendingUpdateRef.current = false;
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
      
      // 清理节流定时器，确保最终状态更新
      if (rafIdRef.current) cancelAnimationFrame(rafIdRef.current);
      pendingUpdateRef.current = false;
      
      // 流式输出完成后，确保 thinking 设置为 false
      const finalMessages = messagesRef.current;
      const finalMsgIndex = finalMessages.findIndex(m => m.id === agentMessageId);
      if (finalMsgIndex !== -1) {
        const newMessages = [...finalMessages];
        newMessages[finalMsgIndex] = {
          ...newMessages[finalMsgIndex],
          thinking: false
        };
        setMessages(newMessages);
      }
    } catch (error) {
      // 如果是取消请求,不显示错误
      if (error.name === 'AbortError') {
        const currentMessages = messagesRef.current;
        const newMessages = [...currentMessages];
        const msgIndex = newMessages.findIndex(m => m.id === agentMessageId);
        if (msgIndex !== -1) {
          newMessages[msgIndex] = {
            ...newMessages[msgIndex],
            thinking: false,
            content: newMessages[msgIndex].content || '已停止生成',
            isStopped: true,
          };
        }
        setMessages(newMessages);
      } else {
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
          };
        }
        setMessages(newMessages);
      }
    } finally {
      setSending(false);
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

  const handleKeyPress = (e) => {
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
            backgroundColor: isUser ? '#6c5ce7' : '#00b894',
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
            <Text strong style={{ color: isUser ? '#6c5ce7' : '#00b894' }}>
              {isUser ? '用户' : 'AI助手'}
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
                  <MarkdownRenderer content={item.thinkingContent} streaming={sending} />
                </div>
              )}
            </div>
          )}
          
          {/* 工具调用显示 */}
          {item.toolCall && (
            <ToolCallDisplay toolCall={item.toolCall} />
          )}
          
          <div
            style={{
              background: item.isError ? '#fff2f0' : (isUser ? '#f0eeff' : '#f0fdf4'),
              border: `1px solid ${item.isError ? '#ffccc7' : (isUser ? '#d4cfff' : '#bbf7d0')}`,
              borderRadius: '8px',
              padding: '12px 16px',
              width: 'fit-content',
              maxWidth: '100%',
            }}
          >
            {/* AI正在回复时显示状态提示 */}
            {isAgent && !item.content && !item.isError && (
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: '#6c5ce7', fontSize: '13px' }}>
                <Spin size="small" />
                <span style={{ fontWeight: 500 }}>
                  {item.thinking ? '正在深度思考...' : '正在回复...'}
                </span>
              </div>
            )}
            {isAgent && !item.isError && item.content ? (
              <MarkdownRenderer content={item.content} streaming={sending} />
            ) : (
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
          {/* 欢迎标题 */}
          <div style={{ textAlign: 'center', marginBottom: '24px' }}>
            <div style={{ fontSize: '28px', fontWeight: 600, color: '#6c5ce7', marginBottom: '8px' }}>AI 智能助手</div>
            <Text type="secondary" style={{ fontSize: '14px' }}>输入消息开始对话，按 Enter 发送</Text>
          </div>
          {/* 输入框容器 */}
          <div style={{ 
            border: '1px solid rgba(108, 92, 231, 0.12)', 
            borderRadius: '12px', 
            overflow: 'hidden',
            boxShadow: '0 2px 12px rgba(108, 92, 231, 0.08)',
            transition: 'box-shadow 0.3s ease, border-color 0.3s ease',
          }}>
            <TextArea
              ref={inputRef}
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              onKeyPress={handleKeyPress}
              placeholder="输入消息... (Enter发送, Shift+Enter换行)"
              autoSize={{ minRows: 3, maxRows: 8 }}
              style={{ fontSize: '14px', border: 'none', resize: 'none', padding: '12px 16px' }}
              disabled={sending}
            />
            {/* 底部功能栏 */}
            <div style={{ 
              display: 'flex', 
              alignItems: 'center', 
              padding: '6px 12px', 
              borderTop: '1px solid rgba(108, 92, 231, 0.06)',
              background: '#fafaff',
              gap: '6px',
            }}>
              {/* 模型选择 */}
              <Dropdown
                menu={{
                  items: models,
                  onClick: (e) => {
                    setCurrentModel(e.key);
                    message.success(`已切换到 ${models.find(m => m.key === e.key)?.label}`);
                  },
                }}
              >
                <Button type="text" size="small" style={{ 
                  padding: '2px 8px', 
                  fontSize: '12px', 
                  color: '#6c5ce7', 
                  height: '26px', 
                  borderRadius: '6px',
                  fontWeight: 500,
                  display: 'flex',
                  alignItems: 'center',
                  gap: '4px',
                }}>
                  {models.find(m => m.key === currentModel)?.label || '选择模型'}
                  <SwapOutlined style={{ fontSize: '10px', opacity: 0.6 }} />
                </Button>
              </Dropdown>
              
              <div style={{ width: '1px', height: '16px', background: 'rgba(108, 92, 231, 0.12)' }} />
              
              {/* 深度思考开关 */}
              <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                <ThunderboltOutlined style={{ fontSize: '12px', color: deepThinking ? '#6c5ce7' : '#8e99a4' }} />
                <Switch 
                  size="small"
                  checked={deepThinking}
                  onChange={(checked) => setDeepThinking(checked)}
                  style={{ 
                    background: deepThinking ? '#6c5ce7' : undefined,
                  }}
                />
                <span style={{ fontSize: '12px', color: deepThinking ? '#6c5ce7' : '#8e99a4', fontWeight: deepThinking ? 500 : 400 }}>
                  深度思考
                </span>
              </div>
              
              {/* 联网搜索开关 */}
              <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                <SearchOutlined style={{ fontSize: '12px', color: webSearch ? '#6c5ce7' : '#8e99a4' }} />
                <Switch 
                  size="small"
                  checked={webSearch}
                  onChange={(checked) => setWebSearch(checked)}
                  style={{ 
                    background: webSearch ? '#6c5ce7' : undefined,
                  }}
                />
                <span style={{ fontSize: '12px', color: webSearch ? '#6c5ce7' : '#8e99a4', fontWeight: webSearch ? 500 : 400 }}>
                  联网搜索
                </span>
              </div>

              <div style={{ flex: 1 }} />

              {/* 发送按钮 */}
              <Button
                type="primary"
                icon={sending ? <StopOutlined /> : <SendOutlined />}
                onClick={sending ? stopGeneration : sendMessage}
                disabled={!inputValue.trim()}
                style={{ 
                  borderRadius: '8px', 
                  height: '32px', 
                  width: '32px', 
                  padding: 0, 
                  display: 'flex', 
                  alignItems: 'center', 
                  justifyContent: 'center',
                  background: sending ? '#ff6b6b' : undefined,
                  borderColor: sending ? '#ff6b6b' : undefined,
                  boxShadow: sending ? '0 2px 8px rgba(255, 107, 107, 0.2)' : '0 2px 8px rgba(108, 92, 231, 0.2)',
                }}
              />
            </div>
          </div>
          
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
        {/* 输入框容器 */}
        <div style={{ 
          border: '1px solid rgba(108, 92, 231, 0.15)', 
          borderRadius: '12px', 
          overflow: 'hidden',
          background: '#ffffff',
          boxShadow: '0 2px 12px rgba(108, 92, 231, 0.06)',
        }}>
          <TextArea
            ref={inputRef}
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onKeyPress={handleKeyPress}
            placeholder="输入消息... (Enter发送, Shift+Enter换行)"
            autoSize={{ minRows: 3, maxRows: 8 }}
            style={{ fontSize: '14px', border: 'none', resize: 'none' }}
            disabled={sending}
          />
          {/* 底部功能栏 */}
          <div style={{ 
            display: 'flex', 
            alignItems: 'center', 
            padding: '6px 12px', 
            borderTop: '1px solid rgba(108, 92, 231, 0.06)',
            background: 'rgba(108, 92, 231, 0.02)',
            gap: '4px',
          }}>
            {/* 模型选择 */}
            <Dropdown
              menu={{
                items: models,
                onClick: (e) => {
                  setCurrentModel(e.key);
                  message.success(`已切换到 ${models.find(m => m.key === e.key)?.label}`);
                },
              }}
            >
              <Button type="text" size="small" style={{ padding: '0 8px', fontSize: '12px', color: '#6c5ce7', height: '28px', borderRadius: '6px', fontWeight: 500, display: 'flex', alignItems: 'center', gap: '4px' }}>
                {models.find(m => m.key === currentModel)?.label || '选择模型'}
                <SwapOutlined style={{ fontSize: '10px', opacity: 0.6 }} />
              </Button>
            </Dropdown>
            
            <div style={{ width: '1px', height: '16px', background: 'rgba(108, 92, 231, 0.1)' }} />
            
            {/* 深度思考 */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
              <ThunderboltOutlined style={{ fontSize: '14px', color: deepThinking ? '#6c5ce7' : '#8e99a4' }} />
              <span style={{ fontSize: '12px', color: deepThinking ? '#6c5ce7' : '#8e99a4', fontWeight: deepThinking ? 500 : 400 }}>深度思考</span>
              <Switch 
                size="small"
                checked={deepThinking}
                onChange={(v) => setDeepThinking(v)}
                style={{ marginLeft: '2px' }}
              />
            </div>
            
            {/* 联网搜索 */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
              <SearchOutlined style={{ fontSize: '14px', color: webSearch ? '#6c5ce7' : '#8e99a4' }} />
              <span style={{ fontSize: '12px', color: webSearch ? '#6c5ce7' : '#8e99a4', fontWeight: webSearch ? 500 : 400 }}>联网搜索</span>
              <Switch 
                size="small"
                checked={webSearch}
                onChange={(v) => setWebSearch(v)}
                style={{ marginLeft: '2px' }}
              />
            </div>

            <div style={{ flex: 1 }} />

            {/* 消息数 */}
            {messages.length > 0 && (
              <Text type="secondary" style={{ fontSize: '11px' }}>
                {messages.length} 条
              </Text>
            )}
            
            {/* 清除 */}
            <Tooltip title="清除会话">
              <Button
                type="text"
                size="small"
                icon={<ClearOutlined />}
                onClick={clearChat}
                disabled={messages.length === 0}
                style={{ padding: '0 4px', fontSize: '12px', color: '#999', height: '28px', borderRadius: '6px' }}
              />
            </Tooltip>
            
            <div style={{ width: '1px', height: '16px', background: 'rgba(108, 92, 231, 0.1)' }} />
            
            {/* 发送按钮 */}
            <Button
              type="primary"
              icon={sending ? <StopOutlined /> : <SendOutlined />}
              onClick={sending ? stopGeneration : sendMessage}
              disabled={!sending && !inputValue.trim()}
              style={{ 
                borderRadius: '8px', 
                height: '32px', 
                width: '32px', 
                padding: 0, 
                display: 'flex', 
                alignItems: 'center', 
                justifyContent: 'center',
                background: sending ? '#ff6b6b' : undefined,
                borderColor: sending ? '#ff6b6b' : undefined,
                boxShadow: sending ? '0 2px 8px rgba(255, 107, 107, 0.2)' : '0 2px 8px rgba(108, 92, 231, 0.2)',
              }}
            />
          </div>
        </div>
        
        </div>
      </div>
    </div>
  );
}

export default ChatInterface;
