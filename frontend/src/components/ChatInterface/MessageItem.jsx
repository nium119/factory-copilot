import React from 'react';
import { Avatar, Button, DatePicker, Drawer, Input, InputNumber, Select, Tooltip, Typography, Spin, Tag, Dropdown, Popconfirm, message } from 'antd';
import dayjs from 'dayjs';
import { useConversationStore } from '../../stores/ConversationContext';
import { UserOutlined, RobotOutlined, CopyOutlined, CheckOutlined, SyncOutlined, ReloadOutlined, WarningOutlined, ToolOutlined, CodeOutlined, CheckCircleFilled, CloseCircleFilled, ClockCircleFilled, ThunderboltOutlined, FilterOutlined, ExportOutlined, BulbOutlined } from '@ant-design/icons';
import MarkdownRenderer from '../MarkdownRenderer';
import ExecutionOrbit from './ExecutionOrbit';
import QuestionFlow from './QuestionFlow';
// 制造业场景用户不主动评价，FeedbackBar 已禁用
// import FeedbackBar from './FeedbackBar';
import request from '../../services/request';
import { authFetch } from '../../utils/authFetch';

function MessageItem({ item, copiedId, onCopy, onToggleThinking, onConfirmApprove, onConfirmReject, onSaveChain, onRetry, onRefresh, onExecuteAction, conversationId, onOpenChainDrawer }) {
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

  // 轨迹回滚：删除操作→恢复被删实体；创建操作→删除新建实体
  const handleRestore = async (tool, records, createdEntityId) => {
    try {
      const res = await request.post('/messages/restore-entity', { tool, records, created_entity_id: createdEntityId });
      const n = res.restored ?? res.deleted ?? 0;
      message.success(`已回滚 ${n} 条${tool ? `（${tool.replace('_delete', '').replace('_create', '')}）` : ''}`);
    } catch (e) {
      message.error(`回滚失败: ${e?.message || e}`);
    }
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

        {/* 思考过程：DSH ReasoningRow 风格 —— 透明裸行，running 光扫，展开看详情。
            思考是执行前的推理，排在工具行之前（对齐 DSH：reasoning → tool-call → text）。 */}
        {isAgent && item.thinkingContent && (() => {
          // 思考块默认折叠成单行（DSH Think row：思考 · 摘要），用户手动展开才显示全文；
          // 流式时不自动展开（避免「思考标题 + 全文」占两行显得松散）。
          const thinkExpanded = !!item.thinkingExpanded;
          return (
            <div
              className={`think-root${(isStreaming && item.thinking) ? ' running' : ''}`}
              onClick={() => onToggleThinking && onToggleThinking(item.id)}
            >
              <div className="think-row">
                <span className="think-lede">
                  <BulbOutlined style={{ fontSize: 14 }} />
                  <span>思考</span>
                </span>
                {!thinkExpanded && (
                  <>
                    <span className="think-sep" aria-hidden />
                    <span className={`think-summary${(isStreaming && item.thinking) ? ' follow-end' : ''}`}>
                      {(() => {
                        const lines = (item.thinkingContent || '').trimEnd().split('\n');
                        return (isStreaming && item.thinking) ? (lines[lines.length - 1] || '') : (lines[0] || '');
                      })()}
                    </span>
                  </>
                )}
                <span style={{ fontSize: 12, color: '#bbb' }}>{thinkExpanded ? '▾' : '▸'}</span>
              </div>
              {thinkExpanded && (
                <div className="think-body">{item.thinkingContent}</div>
              )}
            </div>
          );
        })()}

        {/* 执行轨道：思考/规划/工具/链/反思/协作/执行 统一时间线 */}
        {isAgent && <ExecutionOrbit item={item} isStreaming={isStreaming} onSaveChain={onSaveChain} onRestore={handleRestore} />}

        {/* 工具调用与自我修正已并入执行轨道 */}

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

        {/* 执行链路已并入执行轨道 */}

        {/* 写操作确认 / 澄清已改为 composer 接管条（DSH 式），不再在消息流里渲染卡片 */}

        {/* 排产优化评估结果 — 由 ChatInterface 层级渲染 */}

        <div
          style={{
            background: item.isError ? '#fff2f0' : (isUser ? '#f0eeff' : 'transparent'),
            border: item.isError ? '1px solid #ffccc7' : (isUser ? '1px solid #d4cfff' : 'none'),
            borderRadius: (isUser || item.isError) ? '8px' : '0',
            padding: (isUser || item.isError) ? '12px 16px' : '0',
            width: isUser ? 'fit-content' : '100%',
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
              {/* 快捷回复按钮（结构化追问：label + description + 推荐标注） */}
              {isAgent && !isStreaming && item.quickReplies && item.quickReplies.length > 0 && (() => {
                // 选项归一化：字符串 → {label}；对象 → 原样（label/description/recommended）
                const norm = (o) => (typeof o === 'string' ? { label: o } : (o || {}));
                const isGrouped = typeof item.quickReplies[0] === 'object' && Array.isArray(item.quickReplies[0].options);
                const OptionChip = ({ opt, selected, onClick }) => {
                  const { label, description, recommended } = opt;
                  return (
                    <div onClick={onClick} style={{
                      display: 'flex', flexDirection: 'column', gap: 2,
                      padding: '6px 12px', borderRadius: 10, cursor: 'pointer',
                      border: `1px solid ${selected ? '#6c5ce7' : '#e5e5e5'}`,
                      background: selected ? 'rgba(108,92,231,0.06)' : '#fff',
                      color: selected ? '#6c5ce7' : '#333',
                      maxWidth: 360, userSelect: 'none',
                    }}>
                      <div style={{ fontSize: 13, fontWeight: 500, display: 'flex', alignItems: 'center', gap: 6 }}>
                        {recommended && <span style={{ fontSize: 11, color: '#fff', background: '#fa8c16', borderRadius: 4, padding: '0 5px', lineHeight: '18px' }}>推荐</span>}
                        <span>{label}</span>
                      </div>
                      {description && <div style={{ fontSize: 11, color: '#999', lineHeight: 1.4 }}>{description}</div>}
                    </div>
                  );
                };
                if (isGrouped) {
                  // 逐题问卷（DSH 式）：一次只问一组，1/N 进度 + 自由输入 + 选项点选
                  // + 跳过本题/下一题导航；提交时跳过的组不拼进回答（与后端 quick-reply 链路兼容）
                  return (
                    <QuestionFlow
                      groups={item.quickReplies}
                      optionChip={OptionChip}
                      onSubmit={(parts) => {
                        window.dispatchEvent(new CustomEvent('quick-reply', { detail: parts.join('，') }));
                      }}
                    />
                  );
                }
                return (
                  <div style={{ marginTop: '10px', display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                    {item.quickReplies.map((reply, i) => {
                      const o = norm(reply);
                      return <OptionChip key={i} opt={o} selected={false} onClick={() => window.dispatchEvent(new CustomEvent('quick-reply', { detail: o.label }))} />;
                    })}
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
        {isAgent && item.isError && (onRetry || onRefresh) && (
          <div style={{ display: 'flex', gap: 8, marginTop: 4 }}>
            {onRetry && (
              <Button size="small" icon={<SyncOutlined />} onClick={() => onRetry(item)}>重试</Button>
            )}
            {onRefresh && (
              <Button size="small" icon={<ReloadOutlined />} onClick={onRefresh}>刷新</Button>
            )}
          </div>
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
              } else if (evt.type === 'think') {
                // P2 反思过程：灰字展示（如"结果为空，调整查询条件重试"）
                try {
                  const tk = typeof evt.content === 'string' ? JSON.parse(evt.content) : evt.content;
                  setExecProgress(prev => {
                    const cur = prev[plan.chain_id] || { steps: [] };
                    const reflects = [...(cur.reflects || [])];
                    reflects.push(tk.content || '');
                    if (reflects.length > 5) reflects.shift();
                    return { ...prev, [plan.chain_id]: { ...cur, reflects } };
                  });
                } catch (e) {}
              } else if (evt.type === 'chain_done') {
                const cd = typeof evt.content === 'string' ? JSON.parse(evt.content) : evt.content;
                const vStatus = cd?.verified === false ? 'needs_review' : 'ok';
                setExecProgress(prev => { const cur = prev[plan.chain_id] || {}; return { ...prev, [plan.chain_id]: { ...cur, status: vStatus, desc: cd?.verify_summary || '执行完成', verified: cd?.verified, verify_summary: cd?.verify_summary || '', verify_detail: cd?.verify_detail || [], rolled_back: cd?.rolled_back || false, step: cd?.steps_completed || 0, total: cd?.total_steps || plan.steps_preview?.length || 0 } }; });
                request.post('/messages/save-plan', { conversation_id: effectiveConvId, chain_id: plan.chain_id, message_id: messageId || '', status: vStatus, ok: cd?.steps_completed || 0, total: cd?.total_steps || plan.steps_preview?.length || 0, summary: (cd?.steps_completed || 0) + '/' + (cd?.total_steps || plan.steps_preview?.length || 0) + ' 成功', verified: cd?.verified ?? null, verify_summary: cd?.verify_summary || '', verify_detail: cd?.verify_detail || [] }).catch(() => {});
              } else if (evt.type === 'review_created') {
                // 变更类写操作验证失败 → 已创建复核条目，待复核人处理
                const rc = typeof evt.content === 'object' ? evt.content : {};
                setExecProgress(prev => { const cur = prev[plan.chain_id] || {}; return { ...prev, [plan.chain_id]: { ...cur, submitted_for_review: true, review_id: rc.message_id || '', review_note: '已提交复核，待复核人在审批中心处理（接受或回滚）' } }; });
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
                  {/* 验证状态标记：执行并验证后明显展示 */}
                  {(() => {
                    const prog = execProgress[plan.chain_id];
                    if (prog?.verified === true) return <Tag color="success" style={{ fontSize: 11, fontWeight: 500 }}>✅ 已验证</Tag>;
                    if (prog?.verified === false && prog?.submitted_for_review) return <Tag color="orange" style={{ fontSize: 11, fontWeight: 500 }}>⚠ 已提交复核</Tag>;
                    if (prog?.verified === false) return <Tag color="warning" style={{ fontSize: 11, fontWeight: 500 }}>⚠ 需复核</Tag>;
                    return null;
                  })()}
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
                {/* P2 反思过程：灰字展示（如"结果为空，调整查询条件重试"） */}
                {(() => {
                  const prog = execProgress[plan.chain_id];
                  const reflects = prog?.reflects || [];
                  if (!reflects.length) return null;
                  return (
                    <div style={{ margin: '6px 0 10px', padding: '6px 10px', background: '#fafafa', borderLeft: '3px solid #d9d9d9', borderRadius: 4 }}>
                      {reflects.map((r, i) => (
                        <div key={i} style={{ fontSize: 11, color: '#8c8c8c', lineHeight: 1.6 }}>💭 {r}</div>
                      ))}
                    </div>
                  );
                })()}
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
                          <span title={`🔎 验证：${plan.verify_target?.label || '验证'}`} style={{ fontSize: 11, lineHeight: '16px', textAlign: 'center', marginTop: 4, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '100%' }}>🔎 验证：{plan.verify_target?.label || '验证'}</span>
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
                      <div style={{ marginTop: 8, padding: '10px 12px', background: ok ? '#f6ffed' : '#fffbe6', border: `1px solid ${ok ? '#b7eb8f' : '#ffe58f'}`, borderRadius: 8, fontSize: 12 }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                          <Tag color={ok ? 'success' : 'warning'} style={{ margin: 0, fontSize: 11 }}>{ok ? '✅ 验证通过' : '⚠ 需人工复核'}</Tag>
                          {vprog.rolled_back && <Tag color="orange" style={{ margin: 0, fontSize: 11 }}>↩ 已自动回滚</Tag>}
                          {!ok && vprog.submitted_for_review && <Tag color="orange" style={{ margin: 0, fontSize: 11 }}>⏳ 已提交复核</Tag>}
                        </div>
                        <div style={{ color: '#333', lineHeight: 1.7 }}>{vprog.verify_summary || (ok ? '验证通过' : '验证未通过')}</div>
                        {detail.length > 0 && (
                          <div style={{ marginTop: 6, borderTop: '1px dashed #e0e0e0', paddingTop: 4 }}>
                            <div style={{ display: 'grid', gridTemplateColumns: '110px 1fr 1fr 70px', gap: '2px 8px', fontSize: 11, color: '#999', paddingBottom: 2 }}>
                              <span>属性</span><span>期望</span><span>实际</span><span style={{ textAlign: 'right' }}>结果</span>
                            </div>
                            {detail.map((d, i) => (
                              <div key={i} style={{ display: 'grid', gridTemplateColumns: '110px 1fr 1fr 70px', gap: '2px 8px', fontSize: 12, padding: '3px 0', borderTop: i > 0 ? '1px dashed #f0f0f0' : 'none' }}>
                                <span style={{ color: '#666' }}>{d.property}</span>
                                <span style={{ color: '#333', fontWeight: 500 }}>{d.expected}</span>
                                <span style={{ color: '#333', fontWeight: 500 }}>{d.actual}</span>
                                <span style={{ textAlign: 'right', color: d.match === true ? '#52c41a' : d.match === false ? '#ff4d4f' : '#999', fontWeight: 500 }}>
                                  {d.match === true ? '✓ 一致' : d.match === false ? '✗ 不一致' : '—'}
                                </span>
                              </div>
                            ))}
                          </div>
                        )}
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
  think:    { Icon: BulbOutlined, color: '#bfbfbf', bg: '#fafafa', shadow: 'none' },
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


export default MessageItem;
export { ChangePlanPanel };
