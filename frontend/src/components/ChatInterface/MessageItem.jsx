import React from 'react';
import { Avatar, Button, DatePicker, Drawer, Input, InputNumber, Select, Tooltip, Typography, Spin, Tag, Dropdown, Popconfirm, message } from 'antd';
import dayjs from 'dayjs';
import { useConversationStore } from '../../stores/ConversationContext';
import { UserOutlined, RobotOutlined, CopyOutlined, CheckOutlined, SyncOutlined, ReloadOutlined, WarningOutlined, ToolOutlined, CodeOutlined, CheckCircleFilled, CloseCircleFilled, ClockCircleFilled, ThunderboltOutlined, FilterOutlined, ExportOutlined } from '@ant-design/icons';
import MarkdownRenderer from '../MarkdownRenderer';
import PlanStepsPanel from './PlanStepsPanel';
import ChainProgress from './ChainProgress';
// 制造业场景用户不主动评价，FeedbackBar 已禁用
// import FeedbackBar from './FeedbackBar';
import CollabStepsPanel from './CollabStepsPanel';
import request from '../../services/request';
import { authFetch } from '../../utils/authFetch';

function MessageItem({ item, copiedId, onCopy, onToggleThinking, onConfirmApprove, onConfirmReject, onSaveChain, onRetry, onExecuteAction, conversationId, onOpenChainDrawer }) {
  const isUser = item.role === 'user';
  const isAgent = item.role === 'agent';
  const agentInfo = item.agentInfo || null;
  const avatarColor = isUser ? '#6c5ce7' : (agentInfo?.color || '#00b894');
  const agentName = isUser ? '用户' : (agentInfo?.display_name || 'AI助手');
  const agentIcon = agentInfo?.icon || '';
  const nameColor = isUser ? '#6c5ce7' : (agentInfo?.color || '#00b894');
  const isStreaming = item.streaming === true;

  const formatTime = (timestamp) => {
    return new Date(timestamp).toLocaleTimeString('zh-CN', {
      hour: '2-digit',
      minute: '2-digit',
    });
  };

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
          <Typography.Text strong style={{ color: nameColor }}>
            {isUser ? '用户' : `${agentIcon} ${agentName}`}
          </Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: '12px', marginLeft: '8px' }}>
            {formatTime(item.timestamp)}
          </Typography.Text>
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
              onClick={() => onToggleThinking(item.id)}
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

        {/* 任务分解步骤显示 */}
        {isAgent && item.planSteps && item.planSteps.length > 0 && (
          <PlanStepsPanel
            planTitle={item.planTitle}
            planSteps={item.planSteps}
            isPlanMode={item.isPlanMode}
          />
        )}

        {/* Prompt Chaining 步骤进度 */}
        {isAgent && (item.isDynamic || (item.chainSteps && item.chainSteps.length > 0)) && (
          <ChainProgress
            chainName={item.chainName}
            chainSteps={item.chainSteps}
            isChainMode={item.isChainMode}
            isChainComplete={item.isChainComplete}
            isDynamic={item.isDynamic}
            onSaveChain={onSaveChain ? (steps, name) => onSaveChain(steps, name, item.id) : undefined}
          />
        )}

        {/* Tool Calls — 显示本体工具调用 */}
        {isAgent && item.toolCalls && item.toolCalls.length > 0 && (
          <div style={{ marginBottom: '8px', width: 'fit-content' }}>
            {item.toolCalls.map((tc) => (
              <div key={tc.id} style={{
                background: 'linear-gradient(135deg, #f8faff 0%, #eef2ff 100%)',
                border: '1px solid #d6e4ff',
                borderRadius: '10px',
                padding: '8px 14px',
                fontSize: '12px',
                display: 'flex',
                alignItems: 'center',
                gap: '10px',
                marginBottom: '6px',
                boxShadow: '0 1px 2px rgba(89, 126, 247, 0.05)',
              }}>
                <span style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  width: '26px',
                  height: '26px',
                  borderRadius: '7px',
                  background: 'linear-gradient(135deg, #597ef7 0%, #85a5ff 100%)',
                  color: '#fff',
                  fontSize: '12px',
                }}>
                  <ToolOutlined style={{ fontSize: '13px' }} />
                </span>
                <span style={{ fontWeight: 600, color: '#2f54eb', fontSize: '12px' }}>{tc.name}</span>
                {tc.arguments && (
                  <Tag style={{
                    margin: 0,
                    fontSize: '10px',
                    fontFamily: '"SF Mono", "Cascadia Code", "Fira Code", monospace',
                    maxWidth: '220px',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    background: '#fff',
                    border: '1px solid #e8e8e8',
                    borderRadius: '5px',
                    color: '#595959',
                  }}>
                    {typeof tc.arguments === 'string'
                      ? tc.arguments.substring(0, 60)
                      : JSON.stringify(tc.arguments).substring(0, 60)}
                  </Tag>
                )}
                {tc.rowCount != null && (
                  <span style={{
                    fontSize: '10px',
                    fontWeight: 600,
                    color: '#389e0d',
                    background: '#f6ffed',
                    padding: '1px 7px',
                    borderRadius: '10px',
                    border: '1px solid #b7eb8f',
                  }}>
                    {tc.rowCount} 条结果
                  </span>
                )}
                <span style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center' }}>
                  {tc.status === 'executing' ? (
                    <Spin size="small" />
                  ) : (
                    <span style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      width: '18px',
                      height: '18px',
                      borderRadius: '50%',
                      background: '#f6ffed',
                      color: '#52c41a',
                      fontSize: '10px',
                    }}>
                      <CheckOutlined style={{ fontSize: '10px' }} />
                    </span>
                  )}
                </span>
              </div>
            ))}
          </div>
        )}

        {/* Reflection 自我修正指示器 */}
        {isAgent && item.reflectionReason && (
          <div style={{
            background: '#fff7e6',
            border: '1px solid #ffd591',
            borderRadius: '8px',
            padding: '6px 12px',
            marginBottom: '8px',
            fontSize: '12px',
            color: '#ad6800',
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
            width: 'fit-content',
          }}>
            <SyncOutlined spin={item.isReflectionActive} />
            <span>
              {item.isReflectionActive ? '正在自我修正...' : `已自我修正：${item.reflectionReason}`}
            </span>
          </div>
        )}

        {/* Guardrails 护栏错误显示（区别于普通错误） */}
        {isAgent && item.isGuardrailError && (
          <div style={{
            background: '#fff1f0',
            border: '1px solid #ffccc7',
            borderRadius: '8px',
            padding: '10px 14px',
            marginBottom: '8px',
            fontSize: '13px',
            color: '#cf1322',
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
            width: 'fit-content',
          }}>
            <WarningOutlined style={{ fontSize: '16px' }} />
            <span>{item.content}</span>
          </div>
        )}

        {/* 执行链路面板 — 有横向步骤/动态规划时不显示，避免重复 */}
        {isAgent && item.executionSteps && item.executionSteps.length > 0
          && !item.isChainMode && !item.isDynamic
          && !(item.chainSteps && item.chainSteps.length > 0) && (
          <ExecutionChain steps={item.executionSteps} />
        )}

        {/* 写操作确认卡片 */}
        {isAgent && item.confirmRequired && !item.confirmResolved && (
          <ConfirmCard
            confirm={item.confirmRequired}
            onApprove={onConfirmApprove}
            onReject={onConfirmReject}
          />
        )}

        {/* 排产优化评估结果 — 由 ChatInterface 层级渲染 */}

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
          {/* AI消息内容（错误也显示已接收部分） */}
          {isAgent && (
            <>
              {item.content && <MarkdownRenderer content={(item.actionItems?.length || item.changePlans?.length) ? item.content.replace(/```(?:json)?\s*\n[\s\S]*?\n```/g, '') : item.content} streaming={isStreaming} />}
              {isStreaming && !(item.confirmRequired && !item.confirmResolved) && (
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
              {/* 报告类型消息：行内导出链接 */}
              {isAgent && !isStreaming && item.content && (item.message_type === 'report' || item.messageType === 'report') && (
                <div style={{
                  marginTop: '10px', paddingTop: '8px',
                  borderTop: '1px solid #f0f0f0',
                  fontSize: '12px', color: '#bbb',
                  display: 'flex', alignItems: 'center', gap: '4px',
                }}>
                  <ExportOutlined style={{ fontSize: '12px' }} />
                  <span>导出</span>
                  <a onClick={() => {
                    const msgId = item.backendId || item.id;
                    if (msgId) window.open(`${window.__API_BASE__}/messages/reports/${msgId}/export?format=pdf`, '_blank');
                  }} style={{ color: '#6c5ce7', cursor: 'pointer', marginLeft: '2px' }}>PDF</a>
                  <span>·</span>
                  <a onClick={async () => {
                    const msgId = item.backendId || item.id;
                    if (msgId) {
                      try {
                        const url = `${window.__API_BASE__}/messages/reports/${msgId}/export?format=docx`;
                        const resp = await authFetch(url);
                        if (!resp.ok) {
                          const err = await resp.json().catch(() => ({}));
                          message.error(err.detail || 'Word 导出失败，请重试');
                          return;
                        }
                        const blob = await resp.blob();
                        const downloadUrl = URL.createObjectURL(blob);
                        const a = document.createElement('a');
                        a.href = downloadUrl;
                        a.download = 'report.docx';
                        document.body.appendChild(a);
                        a.click();
                        document.body.removeChild(a);
                        URL.revokeObjectURL(downloadUrl);
                        message.success('正在下载 Word 文件');
                      } catch (e) {
                        message.error('下载失败: ' + (e.message || '网络错误'));
                      }
                    }
                  }} style={{ color: '#6c5ce7', cursor: 'pointer' }}>Word</a>
                </div>
              )}
              {/* 快捷回复按钮 */}
              {isAgent && !isStreaming && item.quickReplies && item.quickReplies.length > 0 && (() => {
                const isGrouped = typeof item.quickReplies[0] === 'object';
                if (isGrouped) {
                  // 分组ASK：每组选一个，全部选完后点确认发送
                  const GroupedReplies = () => {
                    const [selected, setSelected] = React.useState({});
                    const groups = item.quickReplies;
                    const allSelected = Object.keys(selected).length === groups.length;
                    const handleSelect = (gi, opt) => setSelected(prev => ({ ...prev, [gi]: opt }));
                    const handleConfirm = () => {
                      const parts = groups.map((g, gi) => selected[gi]).filter(Boolean);
                      if (parts.length > 0) {
                        window.dispatchEvent(new CustomEvent('quick-reply', { detail: parts.join('，') }));
                      }
                    };
                    return (
                      <div style={{ marginTop: '10px', display: 'flex', flexDirection: 'column', gap: '8px' }}>
                        {groups.map((group, gi) => (
                          <div key={gi}>
                            <div style={{ fontSize: '11px', color: '#999', marginBottom: '4px' }}>{group.label}</div>
                            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px' }}>
                              {group.options.map((opt, oi) => (
                                <Button key={oi} size="small"
                                  type={selected[gi] === opt ? 'primary' : 'default'}
                                  style={{ borderRadius: '14px', fontSize: '12px' }}
                                  onClick={() => handleSelect(gi, opt)}>
                                  {opt}
                                </Button>
                              ))}
                            </div>
                          </div>
                        ))}
                        <Button type="primary" size="small" disabled={!allSelected}
                          style={{ alignSelf: 'flex-start', borderRadius: '14px' }}
                          onClick={handleConfirm}>
                          {allSelected ? '确认发送' : `已选 ${Object.keys(selected).length}/${groups.length}`}
                        </Button>
                      </div>
                    );
                  };
                  return <GroupedReplies />;
                }
                return (
                  <div style={{ marginTop: '10px', display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
                    {item.quickReplies.map((reply, i) => (
                      <Button key={i} size="small" type="default" style={{ borderRadius: '16px', fontSize: '12px', borderColor: '#d9d9d9' }}
                        onClick={() => window.dispatchEvent(new CustomEvent('quick-reply', { detail: reply }))}>
                        {reply}
                      </Button>
                    ))}
                  </div>
                );
              })()}
            </>
          )}
          {isUser && (
            <p
              style={{
                margin: 0,
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
                color: item.isError ? '#ff4d4f' : 'inherit',
              }}
            >
              {item.content}
            </p>
          )}
        </div>
        {isAgent && item.isError && onRetry && (
          <Button size="small" icon={<SyncOutlined />} onClick={() => onRetry(item)}
            style={{ marginTop: 4 }}>重试</Button>
        )}
        {isAgent && item.isError && onRetry && (
          <Button size="small" icon={<ReloadOutlined />} onClick={() => onRetry(item)}
            style={{ marginTop: 4, marginLeft: 8 }}>刷新</Button>
        )}
        {/* 行动项卡片 */}
        {isAgent && !item.isError && item.changePlans && item.changePlans.length > 0 && (
          <ChangePlanPanel plans={item.changePlans} conversationId={conversationId} messageId={item.backendId || item.id} savedResults={item.planExecResults} onOpenChainDrawer={onOpenChainDrawer} />
        )}
        {isAgent && !item.isError && (
          <Tooltip title={copiedId === item.id ? '已复制' : '复制'}>
            <Button
              type="text"
              size="small"
              icon={copiedId === item.id ? <CheckOutlined /> : <CopyOutlined />}
              onClick={() => onCopy(item.content, item.id)}
              style={{ marginTop: '4px', padding: '0 4px' }}
            />
          </Tooltip>
        )}
        {/* 反馈工具栏：制造业场景用户不主动评价，已禁用
        {isAgent && !item.isError && !item.isStopped && !item.streaming && (
          <FeedbackBar messageId={item.backendId || item.id} metadata={item.metadata} agentName={agentInfo?.name} />
        )}
        */}
      </div>
    </div>
  );
}

// ── ChangePlan 变更方案面板 ──
const RISK_COLORS = { low: '#52c41a', medium: '#faad14', high: '#ff4d4f' };
const RISK_BG = { low: '#f6ffed', medium: '#fffbe6', high: '#fff2f0' };

function ChangePlanPanel({ plans, conversationId, messageId, savedResults, onOpenChainDrawer }) {
  const { state: convState } = useConversationStore();
  const effectiveConvId = conversationId || convState?.currentConversation?.id || '';
  const [executing, setExecuting] = React.useState(null);
  const [confirmDrawer, setConfirmDrawer] = React.useState(null);
  const [confirmErrors, setConfirmErrors] = React.useState({});
  const submittedPlansRef = React.useRef(new Set());
  const [, forceUpdate] = React.useState(0);  // { `${stepIdx}.${paramName}`: '错误信息' }
  const searchTimers = React.useRef({});
  const [execProgress, setExecProgress] = React.useState(() => {
    // 从 DB metadata 恢复已完成/失败的执行状态
    const dbResults = savedResults || {};
    const initial = {};
    for (const [chainId, result] of Object.entries(dbResults)) {
      if (result && (result.status === 'ok' || result.status === 'failed' || result.status === 'needs_review')) {
        initial[chainId] = {
          status: result.status,
          desc: result.verify_summary || result.summary,
          step: result.ok, total: result.total, steps: [],
          verified: result.verified, verify_summary: result.verify_summary || '',
          verify_detail: result.verify_detail || [],
        };
      }
    }
    return initial;
  });
  if (!plans || !plans.length) return null;

  const handleExecute = (plan) => {
    setExecuting(plan.chain_id);
    const chainP = plan.chain_id
      ? request.get(`/chains/${encodeURIComponent(plan.chain_id)}`).catch(() => ({}))
      : Promise.resolve({});
    const actionP = request.get('/chains/actions').catch(() => []);
    Promise.all([chainP, actionP]).then(([chain, actions]) => {
      const actionParamsMap = {};
      const actionLabels = {};
      const actionConcepts = {};
      (actions || []).forEach(a => { actionParamsMap[a.name] = a.params || []; actionLabels[a.name] = a.label || a.name; actionConcepts[a.name] = a.conceptLabel || a.conceptName || ''; });
      // 每个步骤独立的参数编辑状态 — 优先用链步骤配置的 action_params 预填，
      // 保证确认抽屉显示的值与实际执行参数一致（此前只从 params_suggestion 预填，
      // 方案未给 params_suggestion 时输入框为空但执行仍用 action_params，体验不一致）
      const stepEditedParams = {};
      (chain.steps || plan.steps_preview || []).forEach((s, i) => {
        let base = {};
        if (s.action_params) {
          try { base = JSON.parse(s.action_params) || {}; } catch { /* 忽略非法 JSON */ }
        }
        stepEditedParams[i] = { ...base, ...(plan.params_suggestion || {}) };
      });
      setConfirmErrors({});
      setConfirmDrawer({ plan, chainSteps: chain.steps || [], actionParamsMap, actionLabels, actionConcepts, editedParams: stepEditedParams, refOptions: {} });
      // 加载 ref 类型参数的实体选项
      const refConcepts = new Set();
      Object.values(actionParamsMap).forEach(plist => {
        (plist || []).forEach(p => {
          if (p.type === 'ref' && p.conceptPropertyRef) {
            refConcepts.add(p.conceptPropertyRef.split('.')[0]);
          }
        });
      });
      refConcepts.forEach(concept => {
        request.get(`/chains/concept-entities/${encodeURIComponent(concept)}`)
          .then(opts => {
            if (opts.length > 0) setConfirmDrawer(d => d ? { ...d, refOptions: { ...d.refOptions, [concept]: opts } } : d);
          }).catch(() => {});
      });
    });
  };

  const doExecute = () => {
    if (!confirmDrawer) return;
    // 校验必填参数
    const missing = [];
    (confirmDrawer.chainSteps || confirmDrawer.plan.steps_preview || []).forEach((step, i) => {
      const actionName = step.action_name || '';
      const actionParams = (confirmDrawer.actionParamsMap || {})[actionName] || [];
      const stepParams = (confirmDrawer.editedParams || {})[i] || {};
      const pp = confirmDrawer.plan.params_suggestion || {};
      actionParams.forEach(p => {
        if (p.required) {
          const val = stepParams[p.name] || pp[p.name] || pp[p.label] || p.defaultValue || '';
          const isEmpty = val === '' || val === null || val === undefined || (typeof val === 'string' && !val.trim());
          if (isEmpty) {
            missing.push(`步骤${i + 1}「${step.description || step}」缺少必填参数`);
          }
        }
      });
    });
    if (missing.length > 0) {
      // 转为字段级错误 { `${stepIdx}.${paramName}`: 'error' }
      const errs = {};
      (confirmDrawer.chainSteps || confirmDrawer.plan.steps_preview || []).forEach((step, i) => {
        const actionName = step.action_name || '';
        const actionParams = (confirmDrawer.actionParamsMap || {})[actionName] || [];
        const stepParams = (confirmDrawer.editedParams || {})[i] || {};
        const pp = confirmDrawer.plan.params_suggestion || {};
        actionParams.forEach(p => {
          if (p.required) {
            const val = stepParams[p.name] || pp[p.name] || pp[p.label] || p.defaultValue || '';
            const isEmpty = val === '' || val === null || val === undefined || (typeof val === 'string' && !val.trim());
            if (isEmpty) {
              errs[`${i}.${p.name}`] = '不能为空';
            }
          }
        });
      });
      setConfirmErrors(errs);
      return;
    }
    setConfirmErrors({});
    const plan = confirmDrawer.plan;
    const stepParams = confirmDrawer.editedParams || {};
    const ep = {};
    Object.values(stepParams).forEach(s => { Object.assign(ep, s); });
    setConfirmDrawer(null);
    setExecProgress(prev => ({ ...prev, [plan.chain_id]: { step: 0, total: plan.steps_preview?.length || 0, desc: '准备执行...', status: 'running', steps: [] } }));
    (async () => {
    try {
      const resp = await authFetch(window.__API_BASE__ + '/messages/execute-plan', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ chain_id: plan.chain_id, params: { plan: { ...ep, verify_target: plan.verify_target } }, conversation_id: effectiveConvId || '', message_id: messageId || '' }),
      });
      const reader = resp.body.getReader(); const decoder = new TextDecoder(); let buffer = '';
      const ps = (text) => {
        const events = text.split('\n\n'); const inc = events.pop() || '';
        for (const event of events) {
          for (const line of event.split('\n')) {
            if (!line.startsWith('data: ')) continue;
            try {
              const evt = JSON.parse(line.slice(6));
              if (evt.type === 'chain_step') {
                const cs = typeof evt.content === 'string' ? JSON.parse(evt.content) : evt.content;
                setExecProgress(prev => {
                  const cur = prev[plan.chain_id] || { step: 0, total: 0, desc: '', status: 'running', steps: [] };
                  const ns = [...cur.steps]; const idx = ns.findIndex(s => s.step_id === cs.step_id);
                  const si = { step_id: cs.step_id, description: cs.description || '', status: cs.status || 'running', warnings: cs.warnings || [] };
                  if (idx >= 0) ns[idx] = si; else ns.push(si);
                  return { ...prev, [plan.chain_id]: { ...cur, step: ns.filter(s => s.status === 'done').length, total: cur.total || ns.length || 1, desc: cs.description || '', steps: ns } };
                });
              } else if (evt.type === 'chain_done') {
                const cd = typeof evt.content === 'string' ? JSON.parse(evt.content) : evt.content;
                const vStatus = cd?.verified === false ? 'needs_review' : 'ok';
                setExecProgress(prev => { const cur = prev[plan.chain_id] || {}; return { ...prev, [plan.chain_id]: { ...cur, status: vStatus, desc: cd?.verify_summary || '执行完成', verified: cd?.verified, verify_summary: cd?.verify_summary || '', verify_detail: cd?.verify_detail || [], step: cd?.steps_completed || 0, total: cd?.total_steps || plan.steps_preview?.length || 0 } }; });
                request.post('/messages/save-plan', { conversation_id: effectiveConvId, chain_id: plan.chain_id, message_id: messageId || '', status: vStatus, ok: cd?.steps_completed || 0, total: cd?.total_steps || plan.steps_preview?.length || 0, summary: (cd?.steps_completed || 0) + '/' + (cd?.total_steps || plan.steps_preview?.length || 0) + ' 成功', verified: cd?.verified ?? null, verify_summary: cd?.verify_summary || '', verify_detail: cd?.verify_detail || [] }).catch(() => {});
              } else if (evt.type === 'error') {
                setExecProgress(prev => { const cur = prev[plan.chain_id] || {}; return { ...prev, [plan.chain_id]: { ...cur, status: 'failed', desc: typeof evt.content === 'string' ? evt.content : '执行失败' } }; });
              }
            } catch (e) {}
          }
        }
        return inc;
      };
      while (true) { const { done, value } = await reader.read(); if (done) { ps(buffer + '\n\n'); break; } buffer += decoder.decode(value); buffer = ps(buffer); }
    } catch (e) { setExecProgress(prev => { const cur = prev[plan.chain_id] || {}; return { ...prev, [plan.chain_id]: { ...cur, status: 'failed', desc: '网络错误' } }; }); }
    finally { setExecuting(null); }
    })();
  };

  return (
    <>
    <div style={{ marginTop: 12, width: '100%' }}>
      <div style={{ fontSize: 13, fontWeight: 600, color: '#333', marginBottom: 8 }}>📋 变更方案</div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10, width: '100%' }}>
        {plans.map((plan) => {
          const color = RISK_COLORS[plan.risk] || '#d9d9d9';
          return (
            <div key={plan.id} style={{
              display: 'flex', alignItems: 'stretch', borderRadius: 8, width: '100%',
              background: `linear-gradient(135deg, ${RISK_BG[plan.risk] || '#fafafa'} 0%, #fff 100%)`,
              border: `1px solid ${color}20`, borderLeft: `4px solid ${color}`,
              boxSizing: 'border-box', overflow: 'hidden',
              boxShadow: '0 2px 6px rgba(0,0,0,0.06)',
            }}>
              <div style={{ flex: 1, padding: '12px 16px', minWidth: 0 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10, flexWrap: 'wrap' }}>
                  <span style={{ fontSize: 15, fontWeight: 600 }}>{plan.label}</span>
                  {plan.recommended && <Tag color="green" style={{ fontSize: 11 }}>推荐</Tag>}
                  <Tag color={color} style={{ fontSize: 11 }}>{{ low: '低风险', medium: '中风险', high: '高风险' }[plan.risk]}</Tag>
                  {plan.chain_name && <Tag style={{ fontSize: 11, background: '#f0f5ff', color: '#597ef7', border: '1px solid #d6e4ff' }}>🔗 {plan.chain_name}</Tag>}
                </div>
                <div style={{ marginBottom: 8, fontSize: 12, color: '#666', lineHeight: 1.8, wordBreak: 'break-word' }}>
                  <div>📌 <strong>前提：</strong>{plan.precondition}</div>
                  <div>📊 <strong>影响：</strong>{plan.impact}</div>
                </div>
                {/* 缺失操作提示 */}
                {plan.missing_actions && plan.missing_actions.length > 0 && (
                  <div style={{ marginBottom: 8, padding: '6px 10px', background: '#fff2f0', border: '1px solid #ffccc7', borderRadius: 6, fontSize: 11 }}>
                    <span style={{ color: '#ff4d4f', fontWeight: 500 }}>⚠ 需要先在 本体图谱 中创建操作：</span>
                    {plan.missing_actions.map((a, i) => (
                      <Tag key={i} color="red" style={{ fontSize: 10, margin: '2px 2px', fontFamily: 'monospace' }}>{a}</Tag>
                    ))}
                  </div>
                )}
                <div style={{ marginTop: 4 }}>
                  {/* 圆圈 + 连接线 + 步骤文字 — 统一 grid 对齐；验证步骤追加在末尾 */}
                  {(() => {
                    const prog = execProgress[plan.chain_id];
                    const vstep = prog?.steps?.find(s => s.step_id === 'verify');
                    const hasVerify = !!plan.verify_target;
                    const cols = plan.steps_preview.length + (hasVerify ? 1 : 0);
                    // 恢复时 steps 为空但 verified 有值 → 仍按已验证显示（状态持久化）
                    const vResolved = vstep || (prog?.verified === true || prog?.verified === false);
                    const vDone = !!(vstep && (vstep.status === 'done' || vstep.status === 'error'))
                      || prog?.verified === true || prog?.verified === false;
                    const vBg = !vResolved ? '#d9d9d9'
                      : vstep?.status === 'running' ? '#faad14'
                      : vstep?.status === 'error' ? '#ff4d4f'
                      : prog?.verified === true ? '#52c41a' : '#fa8c16';
                    const vText = !vResolved ? '🔎'
                      : vstep?.status === 'running' ? '●'
                      : vstep?.status === 'error' ? '✗'
                      : prog?.verified === true ? '✓' : '⚠';
                    const vLine = vDone ? '#52c41a60' : `${color}40`;
                    return (
                    <div style={{ display: 'grid', gridTemplateColumns: `repeat(${cols}, 1fr)` }}>
                      {plan.steps_preview.map((s, i) => {
                        const stepState = prog?.steps?.[i];
                        const isStepDone = stepState?.status === 'done';
                        const isStepRunning = stepState?.status === 'running';
                        const isStepError = stepState?.status === 'error';
                        const hasWarnings = stepState?.warnings?.length > 0;
                        const circleBg = isStepError ? '#ff4d4f' : hasWarnings ? '#fa8c16' : isStepDone ? '#52c41a' : isStepRunning ? '#faad14' : color;
                        const lineColor = isStepDone ? '#52c41a60' : `${color}40`;
                        return (
                        <div key={i} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
                          {/* 圆圈 + 左右半截连接线 */}
                          <div style={{ display: 'flex', alignItems: 'center', width: '100%', height: 22 }}>
                            <span style={{ flex: 1, height: 2, background: i > 0 ? lineColor : 'transparent', minWidth: 6, transition: 'background 0.3s' }} />
                            <Tooltip title={hasWarnings ? stepState.warnings.join('\n') : ''}>
                              <span style={{
                                display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                                width: 22, height: 22, borderRadius: '50%',
                                background: circleBg, color: '#fff', fontSize: 11, fontWeight: 600,
                                flexShrink: 0, lineHeight: 1, transition: 'background 0.3s', cursor: hasWarnings ? 'help' : 'default',
                              }}>
                                {isStepDone ? (hasWarnings ? '⚠' : '✓') : isStepError ? '✗' : isStepRunning ? '●' : (i + 1)}
                              </span>
                            </Tooltip>
                            <span style={{ flex: 1, height: 2, background: (i < plan.steps_preview.length - 1 || hasVerify) ? lineColor : 'transparent', minWidth: 6, transition: 'background 0.3s' }} />
                          </div>
                          {/* 步骤文字 */}
                          <span style={{ fontSize: 11, lineHeight: '16px', textAlign: 'center', marginTop: 4, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '100%' }}>{s}</span>
                        </div>
                      );})}
                      {/* 验证步骤 — 作为最后一步 */}
                      {hasVerify && (
                        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
                          <div style={{ display: 'flex', alignItems: 'center', width: '100%', height: 22 }}>
                            <span style={{ flex: 1, height: 2, background: vLine, minWidth: 6, transition: 'background 0.3s' }} />
                            <span style={{
                              display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                              width: 22, height: 22, borderRadius: '50%',
                              background: vBg, color: '#fff', fontSize: 11, fontWeight: 600,
                              flexShrink: 0, lineHeight: 1, transition: 'background 0.3s',
                            }}>
                              {vText}
                            </span>
                            <span style={{ flex: 1, height: 2, background: 'transparent', minWidth: 6 }} />
                          </div>
                          <span title={plan.verify_target?.label || '验证'} style={{ fontSize: 11, lineHeight: '16px', textAlign: 'center', marginTop: 4, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '100%' }}>{plan.verify_target?.label || '验证'}</span>
                        </div>
                      )}
                    </div>
                    );
                  })()}
                </div>
                {/* 执行后验证结论条 */}
                {(() => {
                  const vprog = execProgress[plan.chain_id];
                  if (vprog?.verified === true || vprog?.verified === false) {
                    const ok = vprog.verified === true;
                    const detail = vprog.verify_detail || [];
                    return (
                      <div style={{ marginTop: 8, padding: '6px 10px', background: ok ? '#f6ffed' : '#fffbe6', border: `1px solid ${ok ? '#b7eb8f' : '#ffe58f'}`, borderRadius: 6, fontSize: 11, color: ok ? '#389e0d' : '#d48806' }}>
                        <div>{ok ? '✅' : '⚠'} {vprog.verify_summary || (ok ? '验证通过' : '验证未通过，需人工复核')}</div>
                        {detail.length > 0 && detail.map((d, i) => (
                          <div key={i} style={{ marginTop: 2, color: '#666' }}>
                            期望 {d.expected} · 实际 {d.actual} · {d.match === true ? '✓ 匹配' : d.match === false ? '✗ 不匹配' : '— 无法判定'}
                          </div>
                        ))}
                      </div>
                    );
                  }
                  return null;
                })()}
              </div>
              <div style={{
                display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
                padding: '8px 16px', background: `${color}08`,
                borderLeft: `1px solid ${color}20`, flexShrink: 0, gap: 4, minWidth: 80,
              }}>
                {(() => {
                  const prog = execProgress[plan.chain_id];
                  if (prog) {
                    const done = prog.step || 0;
                    const total = prog.total || plan.steps_preview?.length || 1;
                    if (prog.status === 'running') {
                      return (
                        <>
                          <Spin size="small" />
                          <span style={{ fontSize: 11, color: color, fontWeight: 500, whiteSpace: 'nowrap' }}>
                            执行中 {done}/{total}
                          </span>
                        </>
                      );
                    }
                    return (
                      <Tag color={prog.status === 'ok' ? 'green' : prog.status === 'needs_review' ? 'orange' : 'red'} style={{ fontSize: 12, margin: 0 }}>
                        {prog.status === 'ok' ? '✓ 已完成' : prog.status === 'needs_review' ? '⚠ 需复核' : '✗ 失败'}
                      </Tag>
                    );
                  }
                  const hasExecuted = Object.values(execProgress).some(p => p && (p.status === 'ok' || p.status === 'failed' || p.status === 'needs_review'));
                  // 无链：区分「缺 action」和「仅缺链」
                  if (!plan.chain_id) {
                    const hasMissing = plan.missing_actions && plan.missing_actions.length > 0;
                    const submitted = submittedPlansRef.current.has(plan.id);

                    // 操作都有，只缺链 → 直接配链
                    if (!hasMissing) {
                      return (
                        <Button type="dashed" shape="round" size="small"
                          onClick={(e) => { e.stopPropagation(); onOpenChainDrawer?.(plan); }}>
                          配置执行链
                        </Button>
                      );
                    }

                    // 缺操作 → 提交到本体图谱
                    return (
                      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4, padding: '0 8px' }}>
                        <Tag color={submitted ? 'green' : 'red'} style={{ fontSize: 11, margin: 0, textAlign: 'center', whiteSpace: 'normal', lineHeight: '16px', maxWidth: 140 }}>
                          {submitted ? '已提交操作请求' : '方案无法执行'}
                        </Tag>
                        {submitted ? (
                          <Button type="dashed" shape="round" size="small"
                            onClick={(e) => { e.stopPropagation(); onOpenChainDrawer?.(plan); }}>
                            配置执行链
                          </Button>
                        ) : (
                          <Popconfirm
                            title={
                              <div style={{ maxWidth: 320 }}>
                                <div style={{ marginBottom: 8 }}>确定提交以下操作请求？</div>
                                {plan.missing_actions && plan.missing_actions.length > 0 && (
                                  <div style={{ marginBottom: 4, fontSize: 12, color: '#ff4d4f' }}>
                                    ❌ 需创建（通知本体图谱）:<br/>
                                    {plan.missing_actions.join(', ')}
                                  </div>
                                )}
                                {plan.existing_actions && plan.existing_actions.length > 0 && (
                                  <div style={{ fontSize: 12, color: '#fa8c16' }}>
                                    ⚠ 需配链（在 FC 中完成）:<br/>
                                    {plan.existing_actions.join(', ')}
                                  </div>
                                )}
                              </div>
                            }
                            onConfirm={async (e) => {
                              e?.stopPropagation();
                              try {
                                await request.post('/notifications/action-request', {
                                  plan_id: plan.id,
                                  plan_label: plan.label,
                                  steps: plan.steps_preview,
                                  actions: plan.actions || [],
                                  missing_actions: plan.missing_actions || [],
                                  existing_actions: plan.existing_actions || [],
                                  conversation_id: effectiveConvId,
                                });
                                submittedPlansRef.current.add(plan.id);
                                forceUpdate(n => n + 1);
                                message.success('已提交，建模人员将收到通知');
                              } catch { message.error('提交失败'); }
                            }}
                            okText="确认提交"
                            cancelText="取消"
                          >
                            <Button type="primary" shape="round" size="small" ghost
                              onClick={(e) => e.stopPropagation()}>
                              提交操作请求
                            </Button>
                          </Popconfirm>
                        )}
                      </div>
                    );
                  }
                  return (
                    <Button type="primary" shape="round" size="large"
                      disabled={!!executing || hasExecuted}
                      loading={executing === plan.chain_id}
                      onClick={() => handleExecute(plan)}>执行</Button>
                  );
                })()}
              </div>
            </div>
          );
        })}
      </div>
    </div>
    <Drawer
      title={`确认执行：${confirmDrawer?.plan?.label || ''}`}
      open={!!confirmDrawer}
      onClose={() => { setConfirmDrawer(null); setExecuting(null); }}
      width={520}
      footer={
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
          <Button onClick={() => { setConfirmDrawer(null); setExecuting(null); }}>取消</Button>
          <Button type="primary" onClick={doExecute}>确认执行</Button>
        </div>
      }
    >
      {confirmDrawer && (
        <>
          <div style={{ marginBottom: 16 }}>
            <div style={{ marginBottom: 8 }}><strong>📌 前提：</strong>{confirmDrawer.plan.precondition}</div>
            <div><strong>📊 影响：</strong>{confirmDrawer.plan.impact}</div>
          </div>
          <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 10 }}>⚡ 执行步骤确认</div>
          {(confirmDrawer.chainSteps || confirmDrawer.plan.steps_preview || []).map((step, i) => {
            const actionName = step.action_name || '';
            const actionParams = (confirmDrawer.actionParamsMap || {})[actionName] || [];
            const stepDesc = typeof step === 'string' ? step : (step.description || '步骤' + (i + 1));
            return (
              <div key={i} style={{ marginBottom: 10, padding: 10, background: '#fafafa', borderRadius: 6, border: '1px solid #f0f0f0' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: actionParams.length > 0 ? 8 : 0 }}>
                  <span style={{ width: 22, height: 22, borderRadius: '50%', background: '#6c5ce7', color: '#fff', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontSize: 11, fontWeight: 600, flexShrink: 0 }}>{i + 1}</span>
                  <span style={{ fontSize: 13, flex: 1 }}>{stepDesc}</span>
                  {actionName && <Tag style={{ fontSize: 10, margin: 0 }}>{(confirmDrawer.actionLabels || {})[actionName] || actionName}</Tag>}
                  {actionName && (confirmDrawer.actionConcepts || {})[actionName] && <Tag style={{ fontSize: 10, margin: 0, background: '#f5f5f5', color: '#999' }}>{(confirmDrawer.actionConcepts || {})[actionName]}</Tag>}
                </div>
                {actionParams.length > 0 ? (
                  <div style={{ marginLeft: 28 }}>
                    {actionParams.map(p => {
                      const stepIdx = i;
                      const pp = confirmDrawer.plan.params_suggestion || {};
                      const stepParams = (confirmDrawer.editedParams || {})[stepIdx] || {};
                      const val = stepParams[p.name] || pp[p.name] || pp[p.label] || p.defaultValue || '';
                      const errKey = `${stepIdx}.${p.name}`;
                      const hasError = !!confirmErrors[errKey];
                      const onChange = (v) => { setConfirmErrors(prev => { const n = {...prev}; delete n[errKey]; return n; }); setConfirmDrawer(d => { if (!d) return d; const newE = { ...(d.editedParams || {}) }; newE[stepIdx] = { ...(newE[stepIdx] || {}), [p.name]: v }; return { ...d, editedParams: newE }; }); };
                      const pType = p.type || 'string';
                      return (
                        <div key={p.name} style={{ marginBottom: 4, fontSize: 12 }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                            <span style={{ color: '#666', whiteSpace: 'nowrap', minWidth: 72 }}>{p.label || p.name}{p.required ? ' *' : ''}</span>
                            {pType === 'datetime' ? (
                              <DatePicker size="small" value={val ? dayjs(val) : null} onChange={(d) => onChange(d ? d.format('YYYY-MM-DD HH:mm:ss') : '')} style={{ flex: 1 }} placeholder={p.label} />
                            ) : pType === 'float' || pType === 'int' || pType === 'number' ? (
                              <InputNumber size="small" value={val ? Number(val) : null} onChange={onChange} style={{ flex: 1 }} placeholder={p.label} />
                            ) : pType === 'ref' ? (
                              <Select size="small" value={val || undefined} onChange={onChange} style={{ flex: 1 }}
                                showSearch placeholder={`搜索${p.label || p.name}`}
                                filterOption={(input, option) => (option?.label ?? '').toLowerCase().includes(input.toLowerCase())}
                                onSearch={(kw) => {
                                  const concept = (p.conceptPropertyRef || '').split('.')[0];
                                  if (kw) {
                                    clearTimeout(searchTimers.current[concept]);
                                    searchTimers.current[concept] = setTimeout(() => {
                                      request.post('/ontology/entities/search', { concept, keyword: kw })
                                        .then(d => {
                                          setConfirmDrawer(dr => dr ? { ...dr, refOptions: { ...dr.refOptions, [concept]: d.options || [] } } : dr);
                                        }).catch(() => {});
                                    }, 300);
                                  }
                                }}
                                options={(() => {
                                  const concept = (p.conceptPropertyRef || '').split('.')[0];
                                  return (confirmDrawer.refOptions || {})[concept] || [];
                                })()}
                                allowClear
                              />
                            ) : (
                              <Input size="small" value={val} placeholder={p.label || p.name}
                                style={{ flex: 1 }}
                                onChange={(e) => onChange(e.target.value)}
                              />
                            )}
                          </div>
                          {hasError && <div style={{ marginLeft: 78, fontSize: 10, color: '#ff4d4f', lineHeight: '16px' }}>{confirmErrors[errKey]}</div>}
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <div style={{ marginLeft: 28, fontSize: 11, color: '#aaa' }}>
                    {actionName ? '参数由前序步骤自动传递' : (step.concept || step.focus_concepts ? `数据范围: ${step.concept || step.focus_concepts}` : '数据查询 — 无需参数')}
                  </div>
                )}
              </div>
            );
          })}
        </>
      )}
    </Drawer>
    </>
  );
}

// ── PromptInfo 提示词折叠面板 ──
function PromptInfoPanel({ promptInfo }) {
  const [expanded, setExpanded] = React.useState(false);
  if (!promptInfo) return null;
  const { model, system_prompt_len, user_message, enable_thinking, web_search } = promptInfo;
  return (
    <div style={{
      background: '#fafafa', border: '1px solid #e8e8e8', borderRadius: 6,
      marginBottom: 8, overflow: 'hidden', fontSize: 12,
    }}>
      <div onClick={() => setExpanded(!expanded)} style={{
        padding: '6px 12px', display: 'flex', alignItems: 'center', gap: 8,
        cursor: 'pointer', userSelect: 'none', color: '#666',
      }}>
        <span>🔍</span>
        <span style={{ fontWeight: 500 }}>调用参数</span>
        <Tag style={{ fontSize: 10, marginLeft: 4 }}>{model}</Tag>
        {enable_thinking && <Tag color="purple" style={{ fontSize: 10 }}>深度思考</Tag>}
        {web_search && <Tag color="blue" style={{ fontSize: 10 }}>联网搜索</Tag>}
        {system_prompt_len > 0 && <span style={{ fontSize: 10, color: '#bbb' }}>SP:{system_prompt_len}字</span>}
        <span style={{ marginLeft: 'auto', fontSize: 10, color: '#999' }}>{expanded ? '▲' : '▼'}</span>
      </div>
      {expanded && (
        <div style={{ borderTop: '1px solid #e8e8e8', padding: '8px 12px' }}>
          <div style={{ marginBottom: 8 }}>
            <div style={{ fontWeight: 600, color: '#999', marginBottom: 4 }}>System Prompt</div>
            <div style={{ color: '#bbb', fontStyle: 'italic' }}>
              {system_prompt_len > 0 ? `${system_prompt_len} 字符（不存储原文）` : '未设置'}
            </div>
          </div>
          <div>
            <div style={{ fontWeight: 600, color: '#999', marginBottom: 4 }}>User Message</div>
            <div style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', color: '#555', lineHeight: 1.6 }}>
              {user_message}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ── ExecutionChain 执行链路面板 ──

const STEP_META = {
  done:     { Icon: CheckCircleFilled, color: '#52c41a', bg: '#f6ffed', shadow: '0 0 0 2px rgba(82, 196, 26, 0.12)' },
  running:  { Icon: ThunderboltOutlined, color: '#1677ff', bg: '#e6f4ff', shadow: '0 0 0 3px rgba(22, 119, 255, 0.18)', pulse: true },
  error:    { Icon: CloseCircleFilled, color: '#ff4d4f', bg: '#fff2f0', shadow: '0 0 0 2px rgba(255, 77, 79, 0.12)' },
  pending:  { Icon: ClockCircleFilled, color: '#d9d9d9', bg: '#fafafa', shadow: 'none' },
};

const STEP_LABEL_MAP = {
  route_l2: '意图识别',
  route_match: '匹配工具',
  param_extract: '参数提取',
  filter_applied: '数据过滤',
  confirm_required: '等待确认',
  confirm_result: '确认结果',
  confirm_delegated: '委托审批',
  tool_start: '工具执行',
  tool_result: '查询结果',
  route_agent_fallback: 'Cypher 兜底',
  cypher_generation: 'Cypher 生成',
  format_start: 'LLM 格式化',
  execution_done: '执行完成',
  parallel_start: '多域协作',
  parallel_task: 'Agent 查询',
  parallel_done: '协作完成',
};

function ExecutionChain({ steps }) {
  const [expanded, setExpanded] = React.useState(true);
  const [hasCollapsed, setHasCollapsed] = React.useState(false);
  const [selectedIndex, setSelectedIndex] = React.useState(null);
  if (!steps || steps.length === 0) return null;

  React.useEffect(() => {
    if (!hasCollapsed && steps.length > 0 && steps.every(s => s.status === 'done')) {
      const timer = setTimeout(() => {
        setExpanded(false);
        setHasCollapsed(true);
      }, 3000);
      return () => clearTimeout(timer);
    }
  }, [steps, hasCollapsed]);

  const doneCount = steps.filter(s => s.status === 'done').length;
  const runningStep = steps.find(s => s.status === 'running');
  const selectedStep = selectedIndex != null ? steps[selectedIndex] : null;
  const allDone = doneCount === steps.length;

  const formatDetailValue = (raw) => {
    if (!raw) return null;
    try {
      const parsed = JSON.parse(raw);
      if (typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed)) {
        return (
          <table style={{ width: '100%', fontSize: '12px', borderCollapse: 'collapse' }}>
            <tbody>
              {Object.entries(parsed).map(([k, v]) => (
                <tr key={k} style={{ borderBottom: '1px solid #f0f0f0' }}>
                  <td style={{ padding: '5px 10px', color: '#8c8c8c', fontWeight: 500, whiteSpace: 'nowrap', verticalAlign: 'top', width: '40%' }}>{k}</td>
                  <td style={{ padding: '5px 10px', color: '#262626', wordBreak: 'break-all', fontSize: '12px' }}>{typeof v === 'object' ? JSON.stringify(v) : String(v)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        );
      }
      return <span style={{ fontFamily: '"SF Mono", "Cascadia Code", "Fira Code", monospace', fontSize: '12px', color: '#434343' }}>{raw}</span>;
    } catch {
      return <span style={{ color: '#434343', fontSize: '12px' }}>{raw}</span>;
    }
  };

  const getStepLabel = (step) => step.label || STEP_LABEL_MAP[step.key] || step.key || '处理中';

  return (
    <div style={{ display: 'flex', gap: '10px', alignItems: 'flex-start', marginBottom: '8px' }}>
      {/* ── Main panel ── */}
      <div style={{
        background: '#fff',
        border: '1px solid #e8e8ec',
        borderRadius: '10px',
        overflow: 'hidden',
        width: selectedStep ? '320px' : 'fit-content',
        minWidth: '280px',
        maxWidth: selectedStep ? '320px' : '420px',
        boxShadow: '0 1px 4px rgba(0,0,0,0.04)',
        transition: 'width 0.25s ease, box-shadow 0.2s',
      }}>
        {/* Header */}
        <div
          onClick={() => setExpanded(!expanded)}
          style={{
            padding: '8px 12px',
            display: 'flex',
            alignItems: 'center',
            cursor: 'pointer',
            userSelect: 'none',
            gap: '8px',
            color: '#262626',
            fontSize: '12px',
            fontWeight: 600,
            background: expanded ? '#fafafa' : '#fff',
            transition: 'background 0.2s',
          }}
        >
          <span style={{
            display: 'inline-flex',
            alignItems: 'center',
            justifyContent: 'center',
            width: '24px',
            height: '24px',
            borderRadius: '7px',
            background: allDone
              ? 'linear-gradient(135deg, #52c41a 0%, #73d13d 100%)'
              : runningStep
                ? 'linear-gradient(135deg, #1677ff 0%, #4096ff 100%)'
                : '#f5f5f5',
            color: (allDone || runningStep) ? '#fff' : '#8c8c8c',
            fontSize: '12px',
            flexShrink: 0,
            transition: 'all 0.35s ease',
          }}>
            {allDone ? <CheckCircleFilled style={{ fontSize: '13px' }} /> : <ThunderboltOutlined style={{ fontSize: '13px' }} />}
          </span>
          <span style={{ fontSize: '13px', letterSpacing: '0.3px' }}>执行链路</span>
          {runningStep && (
            <span style={{
              fontSize: '11px',
              color: '#1677ff',
              fontWeight: 400,
              background: '#e6f4ff',
              padding: '0 6px',
              borderRadius: '4px',
              lineHeight: '18px',
            }}>
              {getStepLabel(runningStep)}
            </span>
          )}
          <span style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: '6px' }}>
            <span style={{
              fontSize: '11px',
              fontWeight: 600,
              color: allDone ? '#52c41a' : '#8c8c8c',
              background: allDone ? '#f6ffed' : '#f5f5f5',
              padding: '1px 8px',
              borderRadius: '10px',
              border: `1px solid ${allDone ? '#b7eb8f' : '#e8e8e8'}`,
              letterSpacing: '0.2px',
            }}>
              {doneCount}/{steps.length}
            </span>
            <span style={{
              fontSize: '9px',
              color: '#bfbfbf',
              transition: 'transform 0.25s ease',
              transform: expanded ? 'rotate(180deg)' : 'rotate(0)',
              lineHeight: 1,
            }}>
              ▴
            </span>
          </span>
        </div>

        {/* Steps list */}
        {expanded && (
          <div style={{ padding: '4px 10px 8px', borderTop: '1px solid #f5f5f5' }}>
            {steps.map((step, i) => {
              const meta = STEP_META[step.status] || STEP_META.pending;
              const { Icon } = meta;
              const isLast = i === steps.length - 1;
              const isSelected = selectedIndex === i;
              const isDone = step.status === 'done';
              const isRunning = step.status === 'running';
              return (
                <div key={i}
                  onClick={() => setSelectedIndex(isSelected ? null : i)}
                  style={{
                    display: 'flex',
                    alignItems: 'flex-start',
                    gap: '8px',
                    position: 'relative',
                    padding: '3px 6px',
                    margin: '0 -6px',
                    borderRadius: '7px',
                    cursor: 'default',
                    background: isSelected ? '#f0f5ff' : 'transparent',
                    transition: 'background 0.18s ease',
                  }}
                  onMouseEnter={e => { if (!isSelected) e.currentTarget.style.background = '#fafafa'; }}
                  onMouseLeave={e => { if (!isSelected) e.currentTarget.style.background = 'transparent'; }}
                >
                  {/* Timeline */}
                  <div style={{
                    display: 'flex',
                    flexDirection: 'column',
                    alignItems: 'center',
                    width: '18px',
                    flexShrink: 0,
                    paddingTop: '5px',
                  }}>
                    <span style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      width: '18px',
                      height: '18px',
                      borderRadius: '50%',
                      background: isRunning ? meta.bg : (isDone ? '#f6ffed' : meta.bg),
                      color: meta.color,
                      fontSize: isRunning ? '14px' : (isDone ? '10px' : '10px'),
                      lineHeight: 1,
                      flexShrink: 0,
                      zIndex: 1,
                      boxShadow: meta.shadow,
                      animation: meta.pulse ? 'chain-dot-pulse 2s ease-in-out infinite' : 'none',
                      transition: 'all 0.3s ease',
                    }}>
                      {isRunning ? <SyncOutlined spin style={{ fontSize: '11px' }} /> : <Icon style={{ fontSize: '11px' }} />}
                    </span>
                    {!isLast && (
                      <div style={{
                        width: '1.5px',
                        flex: 1,
                        minHeight: '16px',
                        marginTop: '3px',
                        borderRadius: '1px',
                        background: isDone
                          ? 'linear-gradient(180deg, #b7eb8f 0%, #d9f7be 100%)'
                          : 'linear-gradient(180deg, #e8e8e8 0%, #f5f5f5 100%)',
                      }} />
                    )}
                  </div>
                  {/* Step content */}
                  <div style={{
                    flex: 1,
                    paddingTop: '3px',
                    fontSize: '13px',
                    lineHeight: '22px',
                    minWidth: 0,
                  }}>
                    <span style={{
                      color: isDone ? '#262626' : (isRunning ? '#1677ff' : meta.color),
                      fontWeight: isRunning ? 600 : 400,
                      letterSpacing: '0.2px',
                      transition: 'color 0.3s',
                    }}>
                      {getStepLabel(step)}
                    </span>
                    {step.detail && (
                      <span style={{
                        display: 'inline-block',
                        color: '#8c8c8c',
                        marginLeft: '6px',
                        fontSize: '11px',
                        maxWidth: '200px',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                        verticalAlign: 'middle',
                        letterSpacing: '0.1px',
                      }}>
                        {step.detail.length > 55 ? step.detail.substring(0, 55) + '…' : step.detail}
                      </span>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}

        <style>{`
          @keyframes chain-dot-pulse {
            0%, 100% { box-shadow: 0 0 0 0 rgba(22, 119, 255, 0.35); }
            50% { box-shadow: 0 0 0 5px rgba(22, 119, 255, 0.06); }
          }
        `}</style>
      </div>

      {/* ── Detail panel ── */}
      {selectedStep && (
        <div style={{
          background: '#fff',
          border: '1px solid #e8e8ec',
          borderRadius: '10px',
          overflow: 'hidden',
          minWidth: '260px',
          maxWidth: '380px',
          flex: 1,
          boxShadow: '0 2px 6px rgba(0,0,0,0.05)',
          animation: 'chain-detail-in 0.22s ease-out',
        }}>
          <div style={{
            padding: '8px 12px',
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            borderBottom: '1px solid #f0f0f0',
            background: '#fafafa',
          }}>
            <span style={{
              display: 'inline-block',
              width: '7px',
              height: '7px',
              borderRadius: '50%',
              background: (STEP_META[selectedStep.status] || STEP_META.pending).color,
              flexShrink: 0,
            }} />
            <span style={{ fontSize: '13px', fontWeight: 600, color: '#262626', letterSpacing: '0.2px' }}>
              步骤详情
            </span>
            <span
              onClick={e => { e.stopPropagation(); setSelectedIndex(null); }}
              style={{
                marginLeft: 'auto',
                cursor: 'pointer',
                fontSize: '16px',
                color: '#bfbfbf',
                lineHeight: 1,
                padding: '2px 6px',
                borderRadius: '4px',
                transition: 'all 0.15s',
              }}
              onMouseEnter={e => { e.currentTarget.style.color = '#595959'; e.currentTarget.style.background = '#f0f0f0'; }}
              onMouseLeave={e => { e.currentTarget.style.color = '#bfbfbf'; e.currentTarget.style.background = 'transparent'; }}
            >
              ×
            </span>
          </div>
          <div style={{ padding: '14px' }}>
            <div style={{ marginBottom: '12px' }}>
              <div style={{ fontSize: '11px', color: '#8c8c8c', marginBottom: '4px', fontWeight: 500, textTransform: 'uppercase', letterSpacing: '0.5px' }}>步骤名称</div>
              <div style={{ fontSize: '14px', fontWeight: 600, color: '#262626' }}>
                {getStepLabel(selectedStep)}
              </div>
            </div>
            <div style={{ marginBottom: '12px', display: 'flex', gap: '12px' }}>
              <div>
                <div style={{ fontSize: '11px', color: '#8c8c8c', marginBottom: '4px', fontWeight: 500, textTransform: 'uppercase', letterSpacing: '0.5px' }}>事件类型</div>
                <Tag style={{ fontSize: '11px', margin: 0 }} color={
                  selectedStep.status === 'done' ? 'success' :
                  selectedStep.status === 'running' ? 'processing' :
                  selectedStep.status === 'error' ? 'error' : 'default'
                }>
                  {selectedStep.key}
                </Tag>
              </div>
              <div>
                <div style={{ fontSize: '11px', color: '#8c8c8c', marginBottom: '4px', fontWeight: 500, textTransform: 'uppercase', letterSpacing: '0.5px' }}>状态</div>
                <Tag style={{ fontSize: '11px', margin: 0 }} color={
                  selectedStep.status === 'done' ? 'success' :
                  selectedStep.status === 'running' ? 'processing' :
                  selectedStep.status === 'error' ? 'error' : 'default'
                }>
                  {selectedStep.status === 'done' ? '已完成' :
                   selectedStep.status === 'running' ? '运行中' :
                   selectedStep.status === 'error' ? '失败' : '等待中'}
                </Tag>
              </div>
            </div>
            {selectedStep.detail && (
              <div>
                <div style={{ fontSize: '11px', color: '#8c8c8c', marginBottom: '6px', fontWeight: 500, textTransform: 'uppercase', letterSpacing: '0.5px' }}>详细数据</div>
                <div style={{
                  background: '#fafafa',
                  borderRadius: '8px',
                  padding: '2px 0',
                  border: '1px solid #f0f0f0',
                }}>
                  {formatDetailValue(selectedStep.detail)}
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      <style>{`
        @keyframes chain-detail-in {
          from { opacity: 0; transform: translateX(-10px) scale(0.97); }
          to { opacity: 1; transform: translateX(0) scale(1); }
        }
      `}</style>
    </div>
  );
}

// ── ConfirmCard 确认卡片 ──

const inputFocusStyle = `
  .confirm-field-input:focus,
  .confirm-field-select:focus {
    outline: none;
    border-color: #fa8c16 !important;
    box-shadow: 0 0 0 2px rgba(250, 140, 22, 0.12);
  }
  .confirm-field-input:hover,
  .confirm-field-select:hover {
    border-color: #ffa940;
  }
`;

function isEmpty(val, type) {
    if (val === undefined || val === null) return true;
    if (type === 'int') return isNaN(val) || val === '';
    return val === '';
  }

function ComboField({ value, options, placeholder, hasError, onChange, entitySearch }) {
  const [open, setOpen] = React.useState(false);
  const [editValue, setEditValue] = React.useState(null);
  const [searchResults, setSearchResults] = React.useState(null); // null=未搜索, [] = 搜索中
  const [searchTimer, setSearchTimer] = React.useState(null);
  const wrapperRef = React.useRef(null);

  // 点击外部关闭下拉，重置编辑状态
  React.useEffect(() => {
    const handleClick = (e) => {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target)) {
        setOpen(false);
        setEditValue(null);
        setSearchResults(null);
      }
    };
    if (open) {
      document.addEventListener('mousedown', handleClick);
      return () => document.removeEventListener('mousedown', handleClick);
    }
  }, [open]);

  // 当前显示值：编辑中显示 editValue，否则显示选中值
  const displayValue = editValue !== null ? editValue : (value || '');

  // 服务端搜索逻辑
  const doServerSearch = React.useCallback(async (keyword) => {
    if (!keyword) {
      setSearchResults(null);
      return;
    }
    setSearchResults([]); // 标记搜索中
    try {
      const data = await request.post('/ontology/entities/search', { concept: entitySearch, keyword });
      setSearchResults(data.options || []);
    } catch {
      setSearchResults(null);
    }
  }, [entitySearch]);

  // 输入变更：服务端搜索走 debounce，否则即时客户端过滤
  const handleInputChange = React.useCallback((text) => {
    setEditValue(text);
    setOpen(true);
    if (entitySearch) {
      if (searchTimer) clearTimeout(searchTimer);
      const timer = setTimeout(() => doServerSearch(text), 300);
      setSearchTimer(timer);
    }
  }, [entitySearch, searchTimer, doServerSearch]);

  // 确定显示的选项列表
  let filtered;
  if (entitySearch) {
    if (searchResults === null) {
      // 未开始搜索：显示初始 options
      filtered = options;
    } else if (searchResults.length === 0 && editValue !== null && editValue !== '') {
      // 搜索中或空结果
      filtered = [];
    } else {
      filtered = searchResults;
    }
  } else {
    const filterText = editValue !== null ? editValue : '';
    filtered = filterText
      ? options.filter(o => o.value.toLowerCase().includes(filterText.toLowerCase()) || o.label.toLowerCase().includes(filterText.toLowerCase()))
      : options;
  }

  const isLoading = entitySearch && searchResults !== null && searchResults.length === 0 && editValue !== null && editValue !== '';

  const handleSelect = (optValue) => {
    onChange(optValue);
    setEditValue(null);
    setSearchResults(null);
    setOpen(false);
  };

  const toggleOpen = () => {
    setOpen(!open);
    setEditValue(null);
    setSearchResults(null);
  };

  const inputStyle = {
    width: '100%',
    padding: '6px 30px 6px 10px',
    borderRadius: '6px',
    fontSize: '13px',
    border: `1px solid ${hasError ? '#ff4d4f' : '#e8e8e8'}`,
    background: hasError ? '#fffbfb' : '#fff',
    boxSizing: 'border-box',
    color: '#333',
    outline: 'none',
  };

  return (
    <div ref={wrapperRef} style={{ position: 'relative' }}>
      <div style={{ position: 'relative' }}>
        <input
          type="text"
          value={displayValue}
          placeholder={placeholder}
          onChange={e => handleInputChange(e.target.value)}
          onFocus={() => setOpen(true)}
          onClick={() => setOpen(true)}
          style={inputStyle}
        />
        <button
          type="button"
          tabIndex={-1}
          onClick={toggleOpen}
          style={{
            position: 'absolute', right: '4px', top: '50%', transform: 'translateY(-50%)',
            background: 'none', border: 'none', cursor: 'pointer', padding: '4px',
            fontSize: '10px', color: '#999', lineHeight: 1,
          }}
        >
          {open ? '▲' : '▼'}
        </button>
      </div>
      {open && (
        <div style={{
          position: 'absolute', top: '100%', left: 0, right: 0, zIndex: 1000,
          maxHeight: '180px', overflowY: 'auto',
          background: '#fff', borderRadius: '6px',
          border: '1px solid #e8e8e8', boxShadow: '0 4px 12px rgba(0,0,0,0.12)',
          marginTop: '2px',
        }}>
          {isLoading ? (
            <div style={{ padding: '8px 12px', color: '#999', fontSize: '12px' }}>搜索中…</div>
          ) : filtered.length === 0 ? (
            <div style={{ padding: '8px 12px', color: '#999', fontSize: '12px' }}>无匹配选项</div>
          ) : (
            filtered.map(opt => (
              <div
                key={opt.value}
                onClick={() => handleSelect(opt.value)}
                style={{
                  padding: '7px 12px', fontSize: '13px', cursor: 'pointer',
                  background: opt.value === value ? '#e6f7ff' : 'transparent',
                  color: '#333',
                  borderBottom: '1px solid #f5f5f5',
                }}
                onMouseEnter={e => { e.target.style.background = '#f0f5ff'; }}
                onMouseLeave={e => { e.target.style.background = opt.value === value ? '#e6f7ff' : 'transparent'; }}
              >
                {opt.label}
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}

  function ConfirmCard({ confirm, onApprove, onReject }) {
    const [submitting, setSubmitting] = React.useState(false);
    const [cancelHover, setCancelHover] = React.useState(false);
    const [errors, setErrors] = React.useState({});
    const paramSchema = confirm?.param_schema || [];
    const prefillParams = confirm?.params || {};
    const ontologyContext = confirm?.context || {};
    const violationMsg = typeof ontologyContext.violation === 'string' ? ontologyContext.violation : '';
    const cleanContext = { ...ontologyContext };
    delete cleanContext.violation;
    const [autoFilled, setAutoFilled] = React.useState(() => {
      const filled = {};
      paramSchema.forEach(p => {
        const hasPrefill = p.name in prefillParams && prefillParams[p.name] != null && prefillParams[p.name] !== '';
        const hasSingleOption = p.entityOptions && p.entityOptions.length === 1;
        const hasDefault = p.defaultValue != null && p.defaultValue !== '';
        filled[p.name] = hasPrefill || (!hasPrefill && (hasSingleOption || hasDefault));
      });
      return filled;
    });
    const [formValues, setFormValues] = React.useState(() => {
      const init = {};
      paramSchema.forEach(p => {
        if (p.name in prefillParams && prefillParams[p.name] != null && prefillParams[p.name] !== '') {
          init[p.name] = prefillParams[p.name];
        } else if (p.entityOptions && p.entityOptions.length === 1) {
          init[p.name] = p.entityOptions[0].value;
        } else if (p.defaultValue != null && p.defaultValue !== '') {
          init[p.name] = p.defaultValue;
        } else {
          init[p.name] = p.type === 'int' ? undefined : '';
        }
      });
      return init;
    });

    if (!confirm) return null;

    const setField = (name, value) => {
      setFormValues(prev => ({ ...prev, [name]: value }));
      if (autoFilled[name]) {
        setAutoFilled(prev => ({ ...prev, [name]: false }));
      }
      if (errors[name]) {
        setErrors(prev => { const n = { ...prev }; delete n[name]; return n; });
      }
    };

    const validate = () => {
      const newErrors = {};
      paramSchema.forEach(p => {
        if (p.required !== false && isEmpty(formValues[p.name], p.type)) {
          newErrors[p.name] = `${p.label || p.name} 不能为空`;
        }
        // 如果字段有 entityOptions（下拉选项），值必须在选项中
        // 但有 entitySearch（服务端搜索）时放宽：允许搜索到的值不在预加载列表中
        if (p.entityOptions && p.entityOptions.length > 0 && !p.entitySearch) {
          const v = formValues[p.name];
          if (v != null && v !== '' && !p.entityOptions.some(o => o.value === v)) {
            newErrors[p.name] = `"${v}" 不在${p.label || p.name}可选范围内`;
          }
        }
        // 如果字段有 enumValues（枚举选项），值必须在选项中
        if (p.enumValues && p.enumValues.length > 0) {
          const v = formValues[p.name];
          if (v != null && v !== '' && !p.enumValues.includes(v)) {
            newErrors[p.name] = `"${v}" 不在${p.label || p.name}可选范围内`;
          }
        }
      });
      setErrors(newErrors);
      return Object.keys(newErrors).length === 0;
    };

    const handleApprove = async () => {
      if (!validate()) return;
      setSubmitting(true);
      try {
        await onApprove?.(formValues);
      } finally {
        setSubmitting(false);
      }
    };

    const handleReject = async () => {
      setSubmitting(true);
      try {
        await onReject?.();
      } finally {
        setSubmitting(false);
      }
    };

    const renderField = (p) => {
      const hasError = !!errors[p.name];
      const baseStyle = {
        width: '100%',
        padding: '6px 10px',
        borderRadius: '6px',
        fontSize: '13px',
        border: `1px solid ${hasError ? '#ff4d4f' : '#e8e8e8'}`,
        background: hasError ? '#fffbfb' : '#fff',
        transition: 'border-color 0.2s, box-shadow 0.2s',
        boxSizing: 'border-box',
        color: '#333',
      };
      if ((p.entityOptions && p.entityOptions.length > 0) || p.entitySearch) {
        return (
          <ComboField
            value={formValues[p.name]}
            options={p.entityOptions || []}
            entitySearch={p.entitySearch || null}
            placeholder={`搜索${p.label}`}
            hasError={hasError}
            onChange={v => setField(p.name, v)}
          />
        );
      }
      if (p.enumValues && p.enumValues.length > 0) {
        const enumOpts = p.enumValues.map(v => ({ value: v, label: v }));
        return (
          <ComboField
            value={formValues[p.name]}
            options={enumOpts}
            placeholder={`选择${p.label}`}
            hasError={hasError}
            onChange={v => setField(p.name, v)}
          />
        );
      }
      const isRequired = p.required !== false;
      if (p.type === 'int' || p.type === 'float' || p.type === 'number') {
        return (
          <input
            className="confirm-field-input"
            type="number"
            required={isRequired}
            value={formValues[p.name] ?? ''}
            onChange={e => setField(p.name, e.target.value ? parseFloat(e.target.value) : undefined)}
            placeholder={p.label}
            style={baseStyle}
          />
        );
      }
      if (p.type === 'date' || p.type === 'datetime' || p.name.toLowerCase().includes('date') || p.label.includes('日期')) {
        return (
          <input
            className="confirm-field-input"
            type="date"
            required={isRequired}
            value={formValues[p.name] || ''}
            onChange={e => setField(p.name, e.target.value)}
            style={{ ...baseStyle, colorScheme: 'light' }}
          />
        );
      }
      return (
        <input
          className="confirm-field-input"
          type="text"
          required={isRequired}
          value={formValues[p.name] || ''}
          onChange={e => setField(p.name, e.target.value)}
          placeholder={p.label}
          style={baseStyle}
        />
      );
    };

  const formatContextValue = (entity) => {
    if (!entity) return '';
    const name = entity.name || entity.id || '';
    const extra = [];
    if (entity.status) extra.push(entity.status);
    if (entity.quantity) extra.push(`x${entity.quantity}件`);
    if (entity.price) extra.push(`¥${entity.price}`);
    return extra.length > 0 ? `${name} (${extra.join(', ')})` : name;
  };

  const hasContext = Object.keys(cleanContext).length > 0;

  return (
    <div style={{
      background: '#fffbf0',
      border: '1px solid #ffd591',
      borderRadius: '12px',
      marginBottom: '8px',
      width: 'fit-content',
      minWidth: '340px',
      maxWidth: '440px',
      overflow: 'hidden',
      boxShadow: '0 1px 4px rgba(250, 140, 22, 0.06)',
    }}>
      <style>{inputFocusStyle}</style>

      {/* Header */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        gap: '8px',
        padding: '12px 16px',
        background: 'linear-gradient(135deg, #fff7e6 0%, #fff3d9 100%)',
        borderBottom: '1px solid #ffe7ba',
      }}>
        <span style={{
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
          width: '22px',
          height: '22px',
          borderRadius: '50%',
          background: '#fa8c16',
          color: '#fff',
          fontSize: '12px',
        }}>
          <WarningOutlined style={{ fontSize: '11px' }} />
        </span>
        <div>
          <div style={{ fontSize: '13px', fontWeight: 700, color: '#ad4e00', lineHeight: 1.3 }}>
            操作确认
          </div>
          <div style={{ fontSize: '11px', color: '#d48806', marginTop: '1px' }}>
            {confirm.action_label}
          </div>
        </div>
        <Tag style={{ marginLeft: 'auto', fontSize: '10px', border: 'none', background: confirm.risk === 'rule_approval' ? '#fff7e6' : '#fff1cc', color: confirm.risk === 'rule_approval' ? '#d46b08' : '#ad6800' }}>
          {confirm.risk === 'rule_approval' ? '规则审批' : '需确认'}
        </Tag>
      </div>

      <div style={{ padding: '14px 16px' }}>

        {/* 违规提示 */}
        {violationMsg && (
          <div style={{
            background: '#fff2f0', border: '1px solid #ffccc7', borderRadius: '8px',
            padding: '10px 12px', marginBottom: '14px', fontSize: '12px', color: '#cf1322',
          }}>
            <div style={{ fontWeight: 600, marginBottom: '4px', fontSize: '12px' }}>⚠️ 规则校验失败，请修正后重新提交：</div>
            <div style={{ whiteSpace: 'pre-wrap' }}>{violationMsg}</div>
          </div>
        )}

        {/* Ontology context */}
        {hasContext && (
          <div style={{
            background: '#f6ffed',
            border: '1px solid #b7eb8f',
            borderRadius: '8px',
            padding: '10px 12px',
            marginBottom: '14px',
            fontSize: '12px',
            color: '#389e0d',
          }}>
            <div style={{ fontWeight: 600, marginBottom: '6px', fontSize: '12px', color: '#237804' }}>
              已识别以下关联信息：
            </div>
            {Object.entries(cleanContext).map(([key, ctxValue]) => {
              const entity = ctxValue.entity || ctxValue;
              const label = ctxValue.label || key;
              return (
                <div key={key} style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '8px',
                  padding: '3px 0',
                }}>
                  <span style={{
                    display: 'inline-block',
                    minWidth: label.length <= 4 ? '52px' : 'auto',
                    fontSize: '11px',
                    fontWeight: 500,
                    color: '#52c41a',
                    background: '#f0fff0',
                    padding: '1px 6px',
                    borderRadius: '4px',
                    textAlign: 'center',
                  }}>{label}</span>
                  <span style={{ color: '#135200' }}>{formatContextValue(entity)}</span>
                </div>
              );
            })}
          </div>
        )}

        {/* Inference chain (推理链确认) */}
        {confirm.type === 'inference_chain' && confirm.inferences?.length > 0 && (
          <div style={{ marginBottom: '14px' }}>
            <div style={{
              fontSize: '12px',
              fontWeight: 600,
              color: '#595959',
              marginBottom: '10px',
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
            }}>
              <span style={{
                display: 'inline-block',
                width: '3px',
                height: '14px',
                borderRadius: '2px',
                background: '#a78bfa',
              }} />
              推理链将自动执行以下操作：
            </div>
            {confirm.inferences.map((inf, idx) => (
              <div key={idx} style={{
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
                padding: '6px 10px',
                marginBottom: '4px',
                background: '#faf5ff',
                borderRadius: '6px',
                border: '1px solid #f0e0ff',
                fontSize: '12px',
              }}>
                <span style={{
                  color: '#7c3aed',
                  fontWeight: 600,
                  fontSize: '11px',
                  minWidth: '20px',
                }}>{idx + 1}.</span>
                <span style={{ color: '#5b3ea8', flex: 1 }}>{inf.description || inf.rule_label}</span>
                <span style={{
                  color: '#a78bfa',
                  fontSize: '10px',
                  fontFamily: 'monospace',
                  background: '#f5f0ff',
                  padding: '2px 6px',
                  borderRadius: '4px',
                }}>{inf.target}</span>
              </div>
            ))}
          </div>
        )}

        {/* Form fields */}
        {paramSchema.length > 0 && (
          <div style={{ marginBottom: '16px' }}>
            <div style={{
              fontSize: '12px',
              fontWeight: 600,
              color: '#595959',
              marginBottom: '10px',
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
            }}>
              <span style={{
                display: 'inline-block',
                width: '3px',
                height: '14px',
                borderRadius: '2px',
                background: '#fa8c16',
              }} />
              填写参数
            </div>
            {paramSchema.map(p => {
              const hasError = !!errors[p.name];
              return (
                <div key={p.name} style={{ marginBottom: '12px' }}>
                  <div style={{
                    display: 'flex',
                    alignItems: 'center',
                    marginBottom: '4px',
                    fontSize: '12px',
                    color: hasError ? '#ff4d4f' : '#8c8c8c',
                    fontWeight: 500,
                  }}>
                    {p.label || p.name}
                    {p.required !== false && (
                      <span style={{ color: '#ff4d4f', marginLeft: '2px' }}>*</span>
                    )}
                    {autoFilled[p.name] && (
                      <span style={{
                        marginLeft: '8px',
                        fontSize: '10px',
                        padding: '0 5px',
                        borderRadius: '4px',
                        background: '#e6f7ff',
                        color: '#1890ff',
                        fontWeight: 400,
                      }}>已识别</span>
                    )}
                    {p.description && (
                      <Tooltip title={p.description}>
                        <span style={{
                          marginLeft: '4px',
                          cursor: 'help',
                          color: '#bfbfbf',
                          fontSize: '11px',
                        }}>?</span>
                      </Tooltip>
                    )}
                  </div>
                  {renderField(p)}
                  {errors[p.name] && (
                    <div style={{
                      fontSize: '11px',
                      color: '#ff4d4f',
                      marginTop: '3px',
                      display: 'flex',
                      alignItems: 'center',
                      gap: '4px',
                    }}>
                      <span style={{
                        display: 'inline-block',
                        width: '5px',
                        height: '5px',
                        borderRadius: '50%',
                        background: '#ff4d4f',
                      }} />
                      {errors[p.name]}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}

        {/* No params fallback */}
        {paramSchema.length === 0 && (
          <div style={{
            fontSize: '13px',
            color: '#8c8c8c',
            marginBottom: '14px',
            padding: '10px 12px',
            background: '#fffbe6',
            borderRadius: '6px',
            border: '1px dashed #ffe58f',
          }}>
            <WarningOutlined style={{ color: '#faad14', marginRight: '6px' }} />
            此操作将修改数据库，是否继续？
          </div>
        )}

        {/* Actions */}
        <div style={{
          display: 'flex',
          gap: '10px',
          justifyContent: 'flex-end',
          paddingTop: hasContext || paramSchema.length > 0 ? '4px' : '0',
          borderTop: hasContext || paramSchema.length > 0 ? '1px solid #f5f5f5' : 'none',
        }}>
          <button
            onClick={handleReject}
            disabled={submitting}
            onMouseEnter={() => !submitting && setCancelHover(true)}
            onMouseLeave={() => setCancelHover(false)}
            style={{
              padding: '6px 18px',
              borderRadius: '6px',
              border: `1px solid ${cancelHover ? '#fa8c16' : '#d9d9d9'}`,
              background: '#fff',
              color: cancelHover ? '#fa8c16' : '#666',
              fontSize: '13px',
              cursor: submitting ? 'not-allowed' : 'pointer',
              fontWeight: 500,
              transition: 'all 0.2s',
            }}
          >
            取消
          </button>
          <button
            onClick={handleApprove}
            disabled={submitting}
            style={{
              padding: '6px 20px',
              borderRadius: '6px',
              border: 'none',
              background: submitting
                ? '#ffc069'
                : 'linear-gradient(135deg, #fa8c16 0%, #faad14 100%)',
              color: '#fff',
              fontSize: '13px',
              cursor: submitting ? 'not-allowed' : 'pointer',
              fontWeight: 600,
              transition: 'all 0.2s',
              boxShadow: submitting ? 'none' : '0 2px 4px rgba(250, 140, 22, 0.3)',
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
            }}
            onMouseEnter={e => {
              if (!submitting) {
                e.target.style.transform = 'translateY(-1px)';
                e.target.style.boxShadow = '0 3px 8px rgba(250, 140, 22, 0.4)';
              }
            }}
            onMouseLeave={e => {
              e.target.style.transform = 'translateY(0)';
              e.target.style.boxShadow = '0 2px 4px rgba(250, 140, 22, 0.3)';
            }}
          >
            {submitting ? (
              <>
                <Spin size="small" style={{ color: '#fff' }} />
                处理中...
              </>
            ) : (
              '确认执行'
            )}
          </button>
        </div>
      </div>
    </div>
  );
}

export default MessageItem;
export { ChangePlanPanel };
