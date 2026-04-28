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
import ApprovalModal from './ChatInterface/ApprovalModal';
import EvalPanel from './ChatInterface/EvalPanel';

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
  const [dataSource, setDataSource] = useState('mock');
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

  // 审批流状态
  const [pendingApproval, setPendingApproval] = useState(null);
  const [approvalModalVisible, setApprovalModalVisible] = useState(false);

  // EvalPanel 评估结果
  const [evalResult, setEvalResult] = useState(null);

  // 自动选择模型指示器（后端 router 复杂度评分结果）
  const [autoSelectedModel, setAutoSelectedModel] = useState(null);

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
            id: Date.now() + Math.random(),
            backendId: msg.id,
            content: msg.content,
            role: msg.role === 'user' ? 'user' : 'agent',
            timestamp: new Date(msg.created_at),
            collabAgents: meta.collab_agents || [],
            isCollabComplete: !!meta.collab_agents,
            agentInfo: meta.agent_info || null,
            // Planning 任务分解
            isPlanMode: meta.is_plan_mode || false,
            planSteps: meta.plan_steps || [],
            planTitle: meta.plan_title || '',
            isPlanComplete: !!meta.plan_steps?.length,
            // Reflection 自我反思
            isReflectionActive: false,
            reflectionReason: meta.reflection_reason || '',
            isReflectionComplete: !!meta.reflection_reason,
            // Prompt Chaining
            isChainMode: false,
            chainId: meta.chain_id || null,
            chainName: meta.chain_name || '',
            chainSteps: [],
            isChainComplete: !!meta.chain_id,
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

    // Planning 任务分解相关 refs
    const isPlanModeRef = { current: false };
    const planStepsRef = { current: [] };
    const planTitleRef = { current: '' };
    const isPlanCompleteRef = { current: false };

    // Reflection 自我反思相关 refs
    const isReflectionActiveRef = { current: false };
    const reflectionReasonRef = { current: '' };
    const isReflectionCompleteRef = { current: false };

    // Prompt Chaining 相关 refs
    const isChainModeRef = { current: false };
    const chainIdRef = { current: null };
    const chainNameRef = { current: '' };
    const chainStepsRef = { current: [] };
    const isChainCompleteRef = { current: false };

    // Parallelization 并行执行相关 refs
    const isParallelModeRef = { current: false };
    const parallelBatchIdRef = { current: null };
    const parallelTasksRef = { current: [] };
    const isParallelCompleteRef = { current: false };

    // 数据源标识相关 refs（避免 flushUpdate 竞态覆盖）
    const dataSourceRef = { current: 'mock' };
    const dataSourceHintRef = { current: null };
    const backendIdRef = { current: null };

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
          // Planning 任务分解
          isPlanMode: isPlanModeRef.current,
          planSteps: [...planStepsRef.current],
          planTitle: planTitleRef.current,
          isPlanComplete: isPlanCompleteRef.current,
          // Reflection 自我反思
          isReflectionActive: isReflectionActiveRef.current,
          reflectionReason: reflectionReasonRef.current,
          isReflectionComplete: isReflectionCompleteRef.current,
          // Prompt Chaining
          isChainMode: isChainModeRef.current,
          chainId: chainIdRef.current,
          chainName: chainNameRef.current,
          chainSteps: [...chainStepsRef.current],
          isChainComplete: isChainCompleteRef.current,
          // Parallelization
          isParallelMode: isParallelModeRef.current,
          parallelBatchId: parallelBatchIdRef.current,
          parallelTasks: [...parallelTasksRef.current],
          isParallelComplete: isParallelCompleteRef.current,
          // 数据源标识
          dataSource: dataSourceRef.current,
          dataSourceHint: dataSourceHintRef.current,
          backendId: backendIdRef.current,
          streaming: isStreamingRef.current,
        };
        setMessages(newMessages);
      } else {
        console.warn('[scheduleUpdate] message not found! agentMessageId:', agentMessageId, 'message_ids:', currentMessages.map(m => m.id));
      }
    };

    try {
      setCurrentAgent(null);
      setDataSource('mock');

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
          } else if (type === 'data_source') {
            const ds = typeof content === 'string' ? JSON.parse(content) : content;
            setDataSource(ds.source || 'mock');
            dataSourceRef.current = ds.source || 'mock';
            dataSourceHintRef.current = ds.hint || null;
            const dsCurrentMessages = messagesRef.current;
            const dsMsgIndex = dsCurrentMessages.findIndex(m => m.id === agentMessageId);
            if (dsMsgIndex !== -1) {
              const dsNewMessages = [...dsCurrentMessages];
              dsNewMessages[dsMsgIndex] = {
                ...dsNewMessages[dsMsgIndex],
                dataSource: ds.source,
                dataSourceHint: ds.hint || null,
              };
              setMessages(dsNewMessages);
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
          } else if (type === 'parallel_start') {
            const p = typeof content === 'string' ? JSON.parse(content) : content;
            isParallelModeRef.current = true;
            parallelBatchIdRef.current = p.batch_id;
            parallelTasksRef.current = (p.tasks || []).map(t => ({ ...t, status: 'pending' }));
            isParallelCompleteRef.current = false;
            scheduleUpdate();
          } else if (type === 'parallel_task') {
            const t = typeof content === 'string' ? JSON.parse(content) : content;
            const idx = parallelTasksRef.current.findIndex(pt => pt.task_id === t.task_id);
            if (idx >= 0) {
              parallelTasksRef.current[idx] = { ...parallelTasksRef.current[idx], ...t };
            } else {
              parallelTasksRef.current.push(t);
            }
            scheduleUpdate();
          } else if (type === 'parallel_done') {
            isParallelModeRef.current = false;
            isParallelCompleteRef.current = true;
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
          } else if (type === 'plan_start') {
            isPlanModeRef.current = true;
            planStepsRef.current = [];
            const p = typeof content === 'string' ? JSON.parse(content) : content;
            planTitleRef.current = p.title || '任务规划';
            scheduleUpdate();
          } else if (type === 'plan_step') {
            const step = typeof content === 'string' ? JSON.parse(content) : content;
            const existing = planStepsRef.current.findIndex(s => s.key === step.key);
            if (existing >= 0) {
              planStepsRef.current[existing] = { ...planStepsRef.current[existing], ...step };
            } else {
              planStepsRef.current.push(step);
            }
            scheduleUpdate();
          } else if (type === 'plan_done') {
            isPlanModeRef.current = false;
            isPlanCompleteRef.current = true;
            scheduleUpdate();
          } else if (type === 'reflection_start') {
            isReflectionActiveRef.current = true;
            const r = typeof content === 'string' ? JSON.parse(content) : content;
            reflectionReasonRef.current = r.reason || '';
            scheduleUpdate();
          } else if (type === 'reflection_done') {
            isReflectionActiveRef.current = false;
            isReflectionCompleteRef.current = true;
            scheduleUpdate();
          } else if (type === 'chain_start') {
            isChainModeRef.current = true;
            const c = typeof content === 'string' ? JSON.parse(content) : content;
            chainIdRef.current = c.chain_id;
            chainNameRef.current = c.chain_name || '';
            chainStepsRef.current = (c.steps || []).map(s => ({ ...s, status: 'pending' }));
            isChainCompleteRef.current = false;
            scheduleUpdate();
          } else if (type === 'chain_step') {
            const step = typeof content === 'string' ? JSON.parse(content) : content;
            const idx = chainStepsRef.current.findIndex(s => s.step_id === step.step_id);
            if (idx >= 0) {
              chainStepsRef.current[idx] = { ...chainStepsRef.current[idx], ...step };
            } else {
              chainStepsRef.current.push(step);
            }
            scheduleUpdate();
          } else if (type === 'chain_summary') {
            scheduleUpdate();
          } else if (type === 'chain_done') {
            isChainModeRef.current = false;
            isChainCompleteRef.current = true;
            scheduleUpdate();
          } else if (type === 'error') {
            const isGuardrail = content.startsWith('输入不合规') || content.startsWith('系统处理异常');
            const currentMessages = messagesRef.current;
            const msgIndex = currentMessages.findIndex(m => m.id === agentMessageId);
            if (msgIndex !== -1) {
              const newMessages = [...currentMessages];
              newMessages[msgIndex] = {
                ...newMessages[msgIndex],
                thinking: false,
                content: isGuardrail ? content : '错误: ' + content,
                isError: true,
                isGuardrailError: isGuardrail,
              };
              setMessages(newMessages);
            }
          } else if (type === 'message_id') {
            const msgData = typeof content === 'string' ? JSON.parse(content) : content;
            backendIdRef.current = msgData.id;
            const currentMessages = messagesRef.current;
            const msgIndex = currentMessages.findIndex(m => m.id === agentMessageId);
            if (msgIndex !== -1) {
              const newMessages = [...currentMessages];
              newMessages[msgIndex] = { ...newMessages[msgIndex], backendId: msgData.id };
              setMessages(newMessages);
            }
          } else if (type === 'approval_request') {
            const approval = typeof content === 'string' ? JSON.parse(content) : content;
            setPendingApproval(approval);
            setApprovalModalVisible(true);
          } else if (type === 'approval_executed') {
            const result = typeof content === 'string' ? JSON.parse(content) : content;
            contentRef.current += `\n\n审批执行结果: ${result.message || '操作已完成'}`;
            scheduleUpdate();
          } else if (type === 'eval_result') {
            const evalData = typeof content === 'string' ? JSON.parse(content) : content;
            setEvalResult(evalData);
          } else if (type === 'optimization_done') {
            const optData = typeof content === 'string' ? JSON.parse(content) : content;
            if (evalResult) {
              setEvalResult({ ...evalResult, optimizationChanges: optData.changes || [] });
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
          // Planning 任务分解
          isPlanMode: isPlanModeRef.current,
          planSteps: [...planStepsRef.current],
          planTitle: planTitleRef.current,
          isPlanComplete: isPlanCompleteRef.current,
          // Reflection 自我反思
          isReflectionActive: isReflectionActiveRef.current,
          reflectionReason: reflectionReasonRef.current,
          isReflectionComplete: isReflectionCompleteRef.current,
          // Prompt Chaining
          isChainMode: isChainModeRef.current,
          chainId: chainIdRef.current,
          chainName: chainNameRef.current,
          chainSteps: [...chainStepsRef.current],
          isChainComplete: isChainCompleteRef.current,
          // Parallelization
          isParallelMode: isParallelModeRef.current,
          parallelBatchId: parallelBatchIdRef.current,
          parallelTasks: [...parallelTasksRef.current],
          isParallelComplete: isParallelCompleteRef.current,
          // 数据源标识
          dataSource: dataSourceRef.current,
          dataSourceHint: dataSourceHintRef.current,
          backendId: backendIdRef.current,
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

  // 审批流处理
  const handleApprove = async (approval) => {
    try {
      const { approveRequest, executeApproved } = await import('../services/approvalService');
      await approveRequest(approval.approval_id);
      const result = await executeApproved(approval.approval_id);
      message.success('审批通过，操作已执行');
      setApprovalModalVisible(false);
      setPendingApproval(null);
      // 将审批结果追加到当前消息内容
      const currentMessages = messagesRef.current;
      const msgIndex = currentMessages.findIndex(m => m.id === streamingMessageIdRef.current);
      if (msgIndex !== -1) {
        const newMessages = [...currentMessages];
        newMessages[msgIndex] = {
          ...newMessages[msgIndex],
          content: newMessages[msgIndex].content + `\n\n✅ 审批通过：${approval.action_name} 已执行`,
        };
        setMessages(newMessages);
      }
    } catch (error) {
      message.error('审批执行失败: ' + error.message);
    }
  };

  const handleReject = async (approval, rejectReason) => {
    try {
      const { rejectRequest } = await import('../services/approvalService');
      await rejectRequest(approval.approval_id, rejectReason);
      message.info('审批已拒绝');
      setApprovalModalVisible(false);
      setPendingApproval(null);
    } catch (error) {
      message.error('拒绝失败: ' + error.message);
    }
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

      {/* 排产优化评估面板（Nice-to-have） */}
      {evalResult && (
        <div style={{ padding: '0 16px', maxWidth: '800px', margin: '0 auto', width: '100%' }}>
          <EvalPanel evalResult={evalResult} />
        </div>
      )}

      {/* 输入区域 */}
      <div style={{ padding: '16px', background: '#ffffff', borderTop: '1px solid rgba(108, 92, 231, 0.08)', width: '100%' }}>
        <div style={{ maxWidth: '800px', margin: '0 auto' }}>
          {renderChatInputBar()}
        </div>
      </div>

      {/* 审批弹窗 */}
      <ApprovalModal
        approval={pendingApproval}
        visible={approvalModalVisible}
        onApprove={handleApprove}
        onReject={handleReject}
        onCancel={() => { setApprovalModalVisible(false); setPendingApproval(null); }}
      />
    </div>
  );
}

export default ChatInterface;
