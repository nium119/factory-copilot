import React, { useState, useEffect, useRef, useCallback } from 'react';
import { App, message, Drawer } from 'antd';
import ChainForm from './ChainEditor';
import * as chatService from '../services/chatService';
import { sendMessageStream, getAgents } from '../services/messageService';
import * as conversationService from '../services/conversationService';
import { useConversationStore } from '../stores/ConversationContext';
import { useConversation } from '../hooks/useConversation';
import './ChatInterface.css';
import ChatInputBar from './ChatInterface/ChatInputBar';
import MessageList from './ChatInterface/MessageList';
import ErrorBoundary from './ErrorBoundary';
import WelcomeScreen from './ChatInterface/WelcomeScreen';
import ApprovalModal from './ChatInterface/ApprovalModal';
import EvalPanel from './ChatInterface/EvalPanel';

function ChatInterface({ sessionId = 'default', initialMessage = null, initialWebSearch = false, agents: initialAgents = [], selectedAgent = null }) {
  const { message } = App.useApp();
  // 使用全局会话状态
  const { state, addMessage, setMessages, updateConversation } = useConversationStore();
  const { createConversation, restoreConversation, currentConversation } = useConversation();

  const [inputValue, setInputValue] = useState('');
  const [loading, setLoading] = useState(false);
  const [sending, setSending] = useState(false);
  const [copiedId, setCopiedId] = useState(null);
  const [chainDrawerOpen, setChainDrawerOpen] = useState(false);
  const [chainPrefillRecord, setChainPrefillRecord] = useState(null);
  const [models, setModels] = useState([]);
  const [currentModel, setCurrentModel] = useState('qwen3.6-plus');
  const [agents, setAgents] = useState([]);
  const [currentAgent, setCurrentAgent] = useState(null);
  const [selectedAgentName, setSelectedAgentName] = useState(null);
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

  // 使用全局消息或本地消息
  const messages = Array.isArray(state.messages) ? state.messages : [];

  // 更新messagesRef
  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  // 根据URL参数更新联网搜索状态
  useEffect(() => {
    if (initialWebSearch) setWebSearch(true);
  }, [initialWebSearch]);

  // 同步外部传入的 Agent 选择
  useEffect(() => {
    if (selectedAgent) setSelectedAgentName(selectedAgent.name);
  }, [selectedAgent]);

  // 自动发送初始消息
  useEffect(() => {
    if (initialMessage && !initialMessageSentRef.current && !sending) {
      initialMessageSentRef.current = true;
      setTimeout(() => {
        sendMessage(initialMessage, false, initialWebSearch, false);
      }, 500);
    }
  }, [initialMessage, sending, initialWebSearch]);

  // 加载模型列表；Agent 列表由 App 提供
  useEffect(() => {
    loadModels();
    if (initialAgents.length > 0) setAgents(initialAgents);
    else loadAgents();
  }, [initialAgents]);

  // 页面刷新后恢复上次会话
  useEffect(() => {
    if (!state.currentConversation?.id && !initialMessage) {
      restoreConversation();
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // SSE 监听审批完成 → 实时刷新对话消息（思考链更新）
  useEffect(() => {
    const es = new EventSource('/api/messages/events/stream');
    es.addEventListener('approval_done', (e) => {
      try {
        const data = JSON.parse(e.data);
        if (data.conversation_id === state.currentConversation?.id) {
          loadHistory();
        }
      } catch {}
    });
    return () => es.close();
  }, [state.currentConversation?.id]);

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
        // 过滤掉 confirm 类型消息（审批数据，不走对话展示）
        const displayMessages = response.messages.filter(m => m.message_type !== 'confirm');
        const formattedMessages = displayMessages.map((msg) => {
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
            // Prompt Chaining（从 metadata 恢复链步骤，链已完成不转圈）
            isChainMode: false,  // 历史消息不在运行中
            chainId: meta.chain_id || null,
            chainName: meta.chain_name || '',
            chainSteps: meta.chain_steps || [],
            isChainComplete: !!(meta.chain_steps && meta.chain_steps.length > 0),
            isDynamic: meta.is_dynamic || false,
            // 完整 metadata（用于 FeedbackBar 等组件读取已有反馈状态）
            metadata: meta,
            // 数据源
            dataSource: meta.data_source || null,
            dataSourceHint: meta.data_source_hint || null,
            // 执行链路
            executionSteps: meta.execution_steps || [],
            // 消息类型（用于导出按钮等）
            message_type: msg.message_type || 'info',
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
      toolCalls: [],
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
    const chainModeRef = { current: 'merged' };
    const chainStepsRef = { current: [] };
    const isChainCompleteRef = { current: false };
    const isDynamicRef = { current: false };

    // 数据源标识相关 refs（避免 flushUpdate 竞态覆盖）
    const dataSourceRef = { current: 'mock' };
    const dataSourceHintRef = { current: null };
    const backendIdRef = { current: null };
    const messageTypeRef = { current: '' };
    const toolCallsRef = { current: [] };

    // 执行链路相关 refs
    const executionStepsRef = { current: [] };
    const confirmRequiredRef = { current: null };
    const confirmResolvedRef = { current: false };

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
          collabAgents: collabAgentsRef.current.map(a => ({ ...a })),
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
          chainMode: chainModeRef.current,
          chainSteps: [...chainStepsRef.current],
          isChainComplete: isChainCompleteRef.current,
          isDynamic: isDynamicRef.current,
          // 数据源标识
          dataSource: dataSourceRef.current,
          dataSourceHint: dataSourceHintRef.current,
          backendId: backendIdRef.current,
          message_type: messageTypeRef.current,
          toolCalls: toolCallsRef.current.map(t => ({ ...t })),
          streaming: isStreamingRef.current,
          // 执行链路
          executionSteps: [...executionStepsRef.current],
          confirmRequired: confirmRequiredRef.current,
          confirmResolved: confirmResolvedRef.current,
        };
        setMessages(newMessages);
      } else {
        console.warn('[scheduleUpdate] message not found! agentMessageId:', agentMessageId, 'message_ids:', currentMessages.map(m => m.id));
      }
    };

    // 将 params 的 key 从属性名映射为中文 label，用于执行链详情展示
    const buildLabeledParams = (params, paramSchema) => {
      if (!paramSchema || !params) return params;
      const labelMap = {};
      paramSchema.forEach(p => { labelMap[p.name] = p.label || p.name; });
      const labeled = {};
      Object.entries(params).forEach(([k, v]) => {
        labeled[labelMap[k] || k] = v;
      });
      return labeled;
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
          } else if (type === 'data_source') {
            const ds = typeof content === 'string' ? JSON.parse(content) : content;
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
          } else if (type === 'tool_call') {
            try {
              const tc = typeof content === 'string' ? JSON.parse(content) : content;
              const existingIdx = toolCallsRef.current.findIndex(t => t.id === tc.id);
              if (existingIdx >= 0) {
                toolCallsRef.current[existingIdx] = { ...toolCallsRef.current[existingIdx], status: 'done' };
              } else {
                toolCallsRef.current.push({
                  id: tc.id,
                  name: tc.name,
                  arguments: tc.arguments,
                  status: 'executing',
                });
              }
              scheduleUpdate();
            } catch (e) { /* ignore parse errors */ }
          } else if (type === 'parallel_start') {
            const p = typeof content === 'string' ? JSON.parse(content) : content;
            isCollabModeRef.current = true;
            isCollabCompleteRef.current = false;
            collabAgentsRef.current = (p.tasks || []).map(t => ({
              name: t.agent_name,
              display_name: t.display_name,
              status: 'pending',
              data: null,
              elapsed: 0,
              priority: 'low',
            }));
            scheduleUpdate();
          } else if (type === 'parallel_task') {
            const t = typeof content === 'string' ? JSON.parse(content) : content;
            const collabStatus = t.status === 'success' ? 'success' : (t.status === 'timeout' || t.status === 'error' ? t.status : 'empty');
            const existing = collabAgentsRef.current.find(a => a.name === t.agent_name);
            if (existing) {
              Object.assign(existing, {
                name: t.agent_name,
                display_name: t.display_name,
                status: collabStatus,
                data: t.data,
                elapsed: t.elapsed || 0,
                error: t.error,
                priority: t.priority || 'low',
              });
            } else {
              collabAgentsRef.current.push({
                name: t.agent_name,
                display_name: t.display_name,
                status: collabStatus,
                data: t.data,
                elapsed: t.elapsed || 0,
                error: t.error,
                priority: t.priority || 'low',
              });
            }
            scheduleUpdate();
          } else if (type === 'parallel_done') {
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
            chainModeRef.current = c.mode || 'merged';
            chainStepsRef.current = (c.steps || []).map(s => ({ ...s, status: 'pending' }));
            isDynamicRef.current = !!c.dynamic;
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
            messageTypeRef.current = msgData.message_type || '';
            const currentMessages = messagesRef.current;
            const msgIndex = currentMessages.findIndex(m => m.id === agentMessageId);
            if (msgIndex !== -1) {
              const newMessages = [...currentMessages];
              newMessages[msgIndex] = { ...newMessages[msgIndex], backendId: msgData.id, message_type: msgData.message_type || '' };
              setMessages(newMessages);
            }
          } else if (type === 'approval_request') {
            const approval = typeof content === 'string' ? JSON.parse(content) : content;
            setPendingApproval(approval);
            setApprovalModalVisible(true);
          } else if (type === 'route_start') {
            const rs = typeof content === 'string' ? JSON.parse(content) : content;
            executionStepsRef.current.push({
              key: 'route_start', label: '路由分析', status: 'done',
              detail: `Agent: ${rs.display_name || rs.agent}`,
            });
            scheduleUpdate();
          } else if (type === 'route_match') {
            const rm = typeof content === 'string' ? JSON.parse(content) : content;
            const l2Step = executionStepsRef.current.find(s => s.key === 'route_l2' && s.status === 'running');
            if (l2Step) l2Step.status = 'done';
            const rmLabel = rm.action_label || rm.concept_label || rm.tool;
            executionStepsRef.current.push({
              key: 'route_match', label: `匹配工具: ${rmLabel}`, status: 'done',
              detail: rm.method === 'keyword' ? '关键词匹配' : `置信度 ${(rm.confidence * 100).toFixed(0)}%`,
            });
            scheduleUpdate();
          } else if (type === 'route_l2') {
            const rl2 = typeof content === 'string' ? JSON.parse(content) : content;
            const concepts = (rl2.concepts || []).slice(0, 3).join(', ');
            executionStepsRef.current.push({
              key: 'route_l2', label: `意图识别 (${rl2.candidateCount} 个候选)`, status: 'running',
              detail: concepts ? `候选: ${concepts}` : undefined,
            });
            scheduleUpdate();
          } else if (type === 'route_agent_fallback') {
            const rf = typeof content === 'string' ? JSON.parse(content) : content;
            const l2StepFb = executionStepsRef.current.find(s => s.key === 'route_l2' && s.status === 'running');
            if (l2StepFb) l2StepFb.status = 'done';
            const concepts = (rf.concepts || []).slice(0, 3).join(', ');
            executionStepsRef.current.push({
              key: 'route_agent_fallback',
              label: `Cypher 生成兜底${concepts ? ` (${concepts})` : ''}`,
              status: 'done',
              detail: '语义无精确匹配，使用本体 Schema 生成 Cypher 查询',
            });
            scheduleUpdate();
          } else if (type === 'cypher_generation') {
            const cg = typeof content === 'string' ? JSON.parse(content) : content;
            executionStepsRef.current.push({
              key: 'cypher_generation', label: 'Cypher 生成', status: 'done',
              detail: cg.cypher ? (cg.cypher.length > 100 ? cg.cypher.slice(0, 100) + '…' : cg.cypher) : undefined,
            });
            scheduleUpdate();
          } else if (type === 'route_l3') {
            const rl3 = typeof content === 'string' ? JSON.parse(content) : content;
            const l2Step3 = executionStepsRef.current.find(s => s.key === 'route_l2' && s.status === 'running');
            if (l2Step3) l2Step3.status = 'done';
            const count = (rl3.available || []).length;
            executionStepsRef.current.push({ key: 'route_l3', label: `无匹配，列出 ${count} 个可用操作`, status: 'done' });
            scheduleUpdate();
          } else if (type === 'param_extract') {
            const pe = typeof content === 'string' ? JSON.parse(content) : content;
            const hasParams = pe.params && Object.keys(pe.params).length > 0;
            const paramStr = hasParams
              ? Object.entries(pe.params).map(([k, v]) => `${k}=${v}`).join(', ')
              : '无过滤条件';
            executionStepsRef.current.push({
              key: 'param_extract', label: '参数提取', status: 'done',
              detail: paramStr,
            });
            if (pe.filters && pe.filters.length > 0) {
              executionStepsRef.current.push({
                key: 'filter_applied',
                label: `数据过滤: ${pe.filters.join(', ')}`,
                status: 'done',
                detail: '基于用户角色自动注入行级安全过滤',
              });
            }
            scheduleUpdate();
          } else if (type === 'confirm_required') {
            const cr = typeof content === 'string' ? JSON.parse(content) : content;
            confirmRequiredRef.current = cr;
            confirmResolvedRef.current = false;
            executionStepsRef.current.push({ key: 'confirm_required', label: `人工确认: ${cr.action_label}`, status: 'running', detail: JSON.stringify(buildLabeledParams(cr.params, cr.param_schema)) });
            scheduleUpdate();
          } else if (type === 'confirm_delegated') {
            const cd = typeof content === 'string' ? JSON.parse(content) : content;
            const assignedList = cd.assigned_to || [];
            executionStepsRef.current.push({
              key: 'confirm_delegated',
              label: `委托审批: ${cd.action_label}`,
              status: 'done',
              detail: JSON.stringify({
                审批角色: assignedList[0] || '?',
                操作: cd.action_label,
                ...buildLabeledParams(cd.params, cd.param_schema),
              }),
            });
            scheduleUpdate();
          } else if (type === 'confirm_result') {
            const cr2 = typeof content === 'string' ? JSON.parse(content) : content;
            confirmResolvedRef.current = true;
            // find LAST running confirm step (there may be two: action + inference)
            const confirmStep = [...executionStepsRef.current].reverse().find(s => s.key === 'confirm_required' && s.status === 'running');
            if (cr2.approved) {
              if (confirmStep) {
                confirmStep.status = 'done';
                confirmStep.label = `人工确认通过: ${confirmStep.label.replace('人工确认: ', '')}`;
                if (cr2.params && Object.keys(cr2.params).length > 0) {
                  const schema = (confirmRequiredRef.current?.param_schema) || [];
                  confirmStep.detail = JSON.stringify(buildLabeledParams(cr2.params, schema));
                }
              }
              confirmRequiredRef.current = null;
            } else {
              if (confirmStep) { confirmStep.status = 'error'; confirmStep.label = '操作已取消'; }
            }
            scheduleUpdate();
          } else if (type === 'tool_start') {
            const ts = typeof content === 'string' ? JSON.parse(content) : content;
            const args = ts.params || {};
            const argsKeys = Object.keys(args);
            const argDetail = argsKeys.length > 0
              ? argsKeys.map(k => `${k}=${args[k]}`).join(', ')
              : '无查询条件';
            executionStepsRef.current.push({
              key: 'tool_start', label: `执行: ${ts.label || ts.tool}`, status: 'running',
              detail: argDetail,
            });
            scheduleUpdate();
          } else if (type === 'tool_result') {
            const tr = typeof content === 'string' ? JSON.parse(content) : content;
            // find LAST running tool_start (there may be two: preview + confirmed)
            const tsStep = [...executionStepsRef.current].reverse().find(s => s.key === 'tool_start' && s.status === 'running');
            if (tsStep) tsStep.status = 'done';
            executionStepsRef.current.push({
              key: 'tool_result', label: `查询结果: ${tr.rowCount} 条记录`, status: 'done',
              detail: `来源: ${tr.source}${tr.rowCount > 0 ? `, 返回 ${tr.rowCount} 条` : ''}`,
            });
            scheduleUpdate();
          } else if (type === 'format_start') {
            executionStepsRef.current.push({
              key: 'format_start', label: 'LLM 格式化回复', status: 'running',
              detail: '将查询结果转换为自然语言',
            });
            scheduleUpdate();
          } else if (type === 'execution_done') {
            const ed = typeof content === 'string' ? JSON.parse(content) : content;
            const fsStep = [...executionStepsRef.current].reverse().find(s => s.key === 'format_start' && s.status === 'running');
            if (fsStep) fsStep.status = 'done';
            if (ed.cancelled) {
              if (ed.delegated) {
                executionStepsRef.current.push({ key: 'execution_done', label: '已委托审批', status: 'done' });
              } else {
                executionStepsRef.current.push({ key: 'execution_done', label: '已取消', status: 'error' });
              }
            } else {
              executionStepsRef.current.push({
                key: 'execution_done', label: '执行完成', status: 'done',
                detail: `共 ${ed.totalSteps || (executionStepsRef.current.length + 1)} 步`,
              });
            }
            scheduleUpdate();
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
          collabAgents: collabAgentsRef.current.map(a => ({ ...a })),
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
          chainMode: chainModeRef.current,
          chainSteps: [...chainStepsRef.current],
          isChainComplete: isChainCompleteRef.current,
          isDynamic: isDynamicRef.current,
          // 数据源标识
          dataSource: dataSourceRef.current,
          dataSourceHint: dataSourceHintRef.current,
          backendId: backendIdRef.current,
          message_type: messageTypeRef.current,
          toolCalls: toolCallsRef.current.map(t => ({ ...t })),
          streaming: false,
          // 执行链路
          executionSteps: [...executionStepsRef.current],
          confirmRequired: confirmRequiredRef.current,
          confirmResolved: confirmResolvedRef.current,
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
          // 尝试从 API 拉取完整消息（后端 finally 块已保存）
          try {
            const resp = await fetch(`/api/conversations/${conversationId}/messages?limit=5`);
            const data = await resp.json();
            const msgs = (data.messages || []);
            // 优先用 backendId，否则取最新的 assistant 消息
            const saved = msgs.find(m => m.id === backendIdRef.current)
                       || msgs.reverse().find(m => m.role === 'assistant' && m.content?.length > (contentRef.current?.length || 0));
            if (saved) {
                const meta = saved.metadata || {};
                newMessages[msgIndex] = {
                  ...newMessages[msgIndex],
                  thinking: false,
                  content: saved.content || (contentRef.current || ''),
                  streaming: false,
                  backendId: saved.id,
                  message_type: saved.message_type || '',
                  chainSteps: meta.chain_steps || newMessages[msgIndex].chainSteps || [],
                  isChainComplete: !!(meta.chain_steps && meta.chain_steps.length > 0),
                };
                setMessages(newMessages);
                setSending(false);
                return;
              }
            } catch (e2) { /* fetch fallback failed, use partial content */ }
          newMessages[msgIndex] = {
            ...newMessages[msgIndex],
            thinking: false,
            content: (contentRef.current || '') + '\n\n⚠️ 响应中断，刷新页面可查看完整内容',
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

  // 写操作确认处理（本体路由 confirm_required）
  const handleConfirmApprove = async (params = {}) => {
    try {
      const conversationId = state.currentConversation?.id || 'default';
      const resp = await fetch(`/api/messages/confirm/${conversationId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ approved: true, params }),
      });
      const data = await resp.json();
      if (data.resolved) {
        message.success('操作已确认，正在执行...');
      }
    } catch (error) {
      message.error('确认失败: ' + error.message);
    }
  };

  const handleConfirmReject = async () => {
    try {
      const conversationId = state.currentConversation?.id || 'default';
      const resp = await fetch(`/api/messages/confirm/${conversationId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ approved: false }),
      });
      const data = await resp.json();
      if (data.resolved) {
        message.info('操作已取消');
      }
    } catch (error) {
      message.error('取消失败: ' + error.message);
    }
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
      hasNoAgents={agents.length === 0 && !sending}
      onInputChange={handleInputChange}
      onKeyPress={handleKeyPress}
      onSend={() => sendMessage()}
      onStop={stopGeneration}
      onMentionSelect={handleMentionSelect}
      onModelChange={handleModelChange}
      onAgentChange={(key) => setSelectedAgentName(key || null)}
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
      <ErrorBoundary>
        <MessageList
          messages={messages}
          copiedId={copiedId}
          onCopy={copyToClipboard}
          onToggleThinking={handleToggleThinking}
          messagesEndRef={messagesEndRef}
          onConfirmApprove={handleConfirmApprove}
          onConfirmReject={handleConfirmReject}
          onSaveChain={(steps, name, messageId) => {
            const allMsgs = messagesRef.current.length > 0 ? messagesRef.current : messages;
            const msgIdx = allMsgs.findIndex(m => m.id === messageId);
            const msg = msgIdx >= 0 ? allMsgs[msgIdx] : null;
            // 从消息元数据获取链模式（后端 chain_start 事件携带）
            const chainMode = msg?.chainMode || (steps.some(s => s.phase === 'reasoning' && s.step_id !== 'comprehensive_analysis') ? 'chained' : 'merged');
            const isChained = chainMode === 'chained';
            const dataSteps = steps.filter(s => s.phase === 'data');
            const reasoningSteps = steps.filter(s => s.phase === 'reasoning' && s.step_id !== 'comprehensive_analysis');

            const formSteps = isChained
              ? reasoningSteps.map((s, i) => ({
                  step_id: s.step_id || `step_${i + 1}`,
                  description: s.description || '',
                  output_key: `${s.step_id || `step_${i + 1}`}_result`,
                  focus_concepts: s.focus_concepts || s.concept || '',
                  prompt_template: s.concept
                    ? `根据以下数据进行分析：\n\n数据: {data_context}\n用户问题: {message}\n\n请给出关于 ${s.description || s.concept} 的分析结论。`
                    : '',
                }))
              : []; // 合并模式不填 steps

            const allConcepts = isChained
              ? [...new Set(reasoningSteps.flatMap(s => (s.focus_concepts || '').split(',').map(c => c.trim()).filter(Boolean)))]
              : [...new Set(
                  (dataSteps.length > 0 ? dataSteps : steps)  // 兼容旧数据：dataSteps 为空时用全量 steps
                    .map(s => s.concept).filter(Boolean)
                )];

            // 从对话中提取链名称：往前收集用户消息，组成上下文
            let topicContent = '';
            if (msgIdx >= 0) {
              const contextParts = [];
              for (let i = msgIdx - 1; i >= 0; i--) {
                if (allMsgs[i].role === 'user') {
                  contextParts.unshift((allMsgs[i].content || '').replace(/\n/g, ' ').trim());
                } else if (contextParts.join('').length >= 6) {
                  break; // 已收集到足够上下文，遇到 assistant 停止
                }
                // 上下文不足时继续往前跨 assistant 找原始问题
              }
              topicContent = contextParts.join('；');
            }
            const autoName = topicContent ? (topicContent.slice(0, 30) + (topicContent.length > 30 ? '...' : '')) : '';

            // 从步骤描述提取查询意图（含失败步骤），保留 LLM 生成的时间上下文
            const stepDescs = dataSteps.map(s => {
              const d = (s.description || s.concept || '').replace(/：.*$/, '').trim();
              return d || '';
            }).filter(Boolean);
            const conceptsDesc = stepDescs.length > 0 ? stepDescs.join('、') : allConcepts.slice(0, 4).join('、');
            const finalPrompt = isChained
              ? `## 汇总任务\n用户问题: {message}\n\n步骤结果:\n${reasoningSteps.map(s => `{${s.step_id || s.output_key || ''}_result}`).join('\n')}\n\n请综合以上步骤结果，给出最终分析结论。`
              : `## 分析任务\n用户问题: {message}\n\n数据: {data_context}\n\n请根据以上数据，对用户问题给出专业分析报告。包括：\n1. 数据概览\n2. 关键发现\n3. 建议行动项`;
            // 从用户问题关键词生成触发条件
            const keywords = (topicContent || '')
              .replace(/[，。！？、；：""''（）\s\d]/g, ' ')
              .split(' ').filter(w => w.length >= 2)
              .slice(0, 4);
            const triggers = keywords.map(k => `.*${k}.*`);

            setChainPrefillRecord({
              name: autoName || name || '动态规划链',
              description: isChained
                ? (reasoningSteps.map(s => s.description).filter(Boolean).join(' → ') || '逐步推理分析')
                : `查询${conceptsDesc || '相关'}数据并综合分析`,
              focus_concepts: allConcepts.join(','),
              triggers,
              final_prompt_template: finalPrompt,
              steps: formSteps,
            });
            setChainDrawerOpen(true);
          }}
        />
      </ErrorBoundary>

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

      {/* 保存为链 抽屉 */}
      <Drawer
        title="保存为链"
        open={chainDrawerOpen}
        onClose={() => { setChainDrawerOpen(false); setChainPrefillRecord(null); }}
        width={720}
        destroyOnClose
      >
        {chainDrawerOpen && (
          <ChainForm
            record={chainPrefillRecord}
            agents={initialAgents}
            onCancel={() => { setChainDrawerOpen(false); setChainPrefillRecord(null); }}
            onSuccess={() => { setChainDrawerOpen(false); setChainPrefillRecord(null); }}
          />
        )}
      </Drawer>
    </div>
  );
}

export default ChatInterface;
