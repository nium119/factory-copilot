import React, { createContext, useContext, useReducer, useCallback, useEffect } from 'react';

// 初始状态
const initialState = {
  // 会话列表
  conversations: [],
  // 当前会话
  currentConversation: null,
  // 当前会话的消息列表
  messages: [],
  // 草稿(会话ID -> 草稿内容)
  drafts: {},
  // 记忆配置
  memoryConfig: {
    enabled: true,
    top_k: 5,
    similarity_threshold: 0.7,
    auto_inject: true
  },
  // 加载状态
  loading: {
    conversations: false,
    messages: false,
    sending: false
  },
  // 分页信息
  pagination: {
    page: 1,
    pageSize: 20,
    total: 0
  },
  // 原对话抽屉：待查看的会话 ID（通知/卡片"打开原对话"右侧抽屉展示）
  viewConversationId: '',
  // 原对话抽屉：锚点消息 ID（变更方案消息，用于定位其附近上下文；空则取最近）
  viewMessageId: ''
};

// Action类型
const ActionTypes = {
  // 会话相关
  SET_CONVERSATIONS: 'SET_CONVERSATIONS',
  SET_CURRENT_CONVERSATION: 'SET_CURRENT_CONVERSATION',
  ADD_CONVERSATION: 'ADD_CONVERSATION',
  UPDATE_CONVERSATION: 'UPDATE_CONVERSATION',
  DELETE_CONVERSATION: 'DELETE_CONVERSATION',

  // 消息相关
  SET_MESSAGES: 'SET_MESSAGES',
  ADD_MESSAGE: 'ADD_MESSAGE',
  CLEAR_MESSAGES: 'CLEAR_MESSAGES',

  // 草稿相关
  SET_DRAFT: 'SET_DRAFT',
  CLEAR_DRAFT: 'CLEAR_DRAFT',

  // 记忆配置相关
  SET_MEMORY_CONFIG: 'SET_MEMORY_CONFIG',

  // 加载状态相关
  SET_LOADING: 'SET_LOADING',

  // 分页相关
  SET_PAGINATION: 'SET_PAGINATION',

  // 原对话抽屉相关
  SET_VIEW_CONVERSATION: 'SET_VIEW_CONVERSATION',
  SET_VIEW_MESSAGE: 'SET_VIEW_MESSAGE'
};

// Reducer
function conversationReducer(state, action) {
  switch (action.type) {
    // 会话相关
    case ActionTypes.SET_CONVERSATIONS:
      return { ...state, conversations: action.payload };

    case ActionTypes.SET_CURRENT_CONVERSATION:
      // 持久化当前会话ID
      try {
        if (action.payload?.id) {
          localStorage.setItem('fc_current_conversation_id', action.payload.id);
        } else {
          localStorage.removeItem('fc_current_conversation_id');
        }
      } catch (e) {}
      return { ...state, currentConversation: action.payload };

    case ActionTypes.ADD_CONVERSATION:
      return {
        ...state,
        conversations: [action.payload, ...state.conversations]
      };

    case ActionTypes.UPDATE_CONVERSATION:
      return {
        ...state,
        conversations: state.conversations.map(conv =>
          conv.id === action.payload.id ? { ...conv, ...action.payload } : conv
        )
      };

    case ActionTypes.DELETE_CONVERSATION:
      return {
        ...state,
        conversations: state.conversations.filter(conv => conv.id !== action.payload)
      };

    // 消息相关
    case ActionTypes.SET_MESSAGES:
      return { ...state, messages: action.payload };

    case ActionTypes.ADD_MESSAGE:
      return {
        ...state,
        messages: [...state.messages, action.payload]
      };

    case ActionTypes.CLEAR_MESSAGES:
      return { ...state, messages: [] };

    // 草稿相关
    case ActionTypes.SET_DRAFT:
      return {
        ...state,
        drafts: { ...state.drafts, [action.payload.id]: action.payload.content }
      };

    case ActionTypes.CLEAR_DRAFT:
      const newDrafts = { ...state.drafts };
      delete newDrafts[action.payload];
      return { ...state, drafts: newDrafts };

    // 记忆配置相关
    case ActionTypes.SET_MEMORY_CONFIG:
      return { ...state, memoryConfig: action.payload };

    // 加载状态相关
    case ActionTypes.SET_LOADING:
      return {
        ...state,
        loading: { ...state.loading, ...action.payload }
      };

    // 分页相关
    case ActionTypes.SET_PAGINATION:
      return {
        ...state,
        pagination: { ...state.pagination, ...action.payload }
      };

    case ActionTypes.SET_VIEW_CONVERSATION:
      return { ...state, viewConversationId: action.payload };

    case ActionTypes.SET_VIEW_MESSAGE:
      return { ...state, viewMessageId: action.payload };

    default:
      return state;
  }
}

// 创建Context
const ConversationContext = createContext(null);

// 从 localStorage 恢复当前会话ID
function loadPersistedState() {
  try {
    const savedConvId = localStorage.getItem('fc_current_conversation_id');
    if (savedConvId) {
      return { ...initialState, _restoredConvId: savedConvId };
    }
  } catch (e) {}
  return initialState;
}

// Provider组件
export function ConversationProvider({ children }) {
  const [state, dispatch] = useReducer(conversationReducer, loadPersistedState());

  // Action creators
  const actions = {
    // 会话相关
    setConversations: useCallback((conversations) => {
      dispatch({ type: ActionTypes.SET_CONVERSATIONS, payload: conversations });
    }, []),

    setCurrentConversation: useCallback((conversation) => {
      dispatch({ type: ActionTypes.SET_CURRENT_CONVERSATION, payload: conversation });
    }, []),

    addConversation: useCallback((conversation) => {
      dispatch({ type: ActionTypes.ADD_CONVERSATION, payload: conversation });
    }, []),

    updateConversation: useCallback((conversation) => {
      dispatch({ type: ActionTypes.UPDATE_CONVERSATION, payload: conversation });
    }, []),

    deleteConversation: useCallback((conversationId) => {
      dispatch({ type: ActionTypes.DELETE_CONVERSATION, payload: conversationId });
    }, []),

    // 消息相关
    setMessages: useCallback((messages) => {
      dispatch({ type: ActionTypes.SET_MESSAGES, payload: messages });
    }, []),

    addMessage: useCallback((message) => {
      dispatch({ type: ActionTypes.ADD_MESSAGE, payload: message });
    }, []),

    clearMessages: useCallback(() => {
      dispatch({ type: ActionTypes.CLEAR_MESSAGES });
    }, []),

    // 草稿相关
    setDraft: useCallback((id, content) => {
      dispatch({ type: ActionTypes.SET_DRAFT, payload: { id, content } });
    }, []),

    clearDraft: useCallback((id) => {
      dispatch({ type: ActionTypes.CLEAR_DRAFT, payload: id });
    }, []),

    // 记忆配置相关
    setMemoryConfig: useCallback((config) => {
      dispatch({ type: ActionTypes.SET_MEMORY_CONFIG, payload: config });
    }, []),

    // 加载状态相关
    setLoading: useCallback((loading) => {
      dispatch({ type: ActionTypes.SET_LOADING, payload: loading });
    }, []),

    // 分页相关
    setPagination: useCallback((pagination) => {
      dispatch({ type: ActionTypes.SET_PAGINATION, payload: pagination });
    }, []),

    // 原对话抽屉：设置待查看会话 + 锚点消息（右侧抽屉展示方案附近上下文）
    setViewConversation: useCallback((conversationId, messageId = '') => {
      dispatch({ type: ActionTypes.SET_VIEW_CONVERSATION, payload: conversationId || '' });
      dispatch({ type: ActionTypes.SET_VIEW_MESSAGE, payload: messageId || '' });
    }, []),
    closeConversationView: useCallback(() => {
      dispatch({ type: ActionTypes.SET_VIEW_CONVERSATION, payload: '' });
      dispatch({ type: ActionTypes.SET_VIEW_MESSAGE, payload: '' });
    }, [])
  };

  return (
    <ConversationContext.Provider value={{ state, ...actions }}>
      {children}
    </ConversationContext.Provider>
  );
}

// 自定义Hook
export function useConversationStore() {
  const context = useContext(ConversationContext);
  if (!context) {
    throw new Error('useConversationStore must be used within ConversationProvider');
  }
  return context;
}

/**
 * 获取持久化的当前会话ID（供 ChatInterface 等组件在挂载时恢复状态）
 */
export function getPersistedConversationId() {
  try {
    return localStorage.getItem('fc_current_conversation_id');
  } catch (e) {
    return null;
  }
}
