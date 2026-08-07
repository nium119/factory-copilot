import React, { useState, useEffect, useCallback } from 'react';
import { Drawer, Spin, Empty, Divider } from 'antd';
import { useConversationStore } from '../../stores/ConversationContext';
import * as conversationService from '../../services/conversationService';
import MarkdownRenderer from '../MarkdownRenderer';

/**
 * 原对话抽屉 — 类似微信"查看历史记录"：从右侧滑出，展示指定会话的完整上下文。
 * 通知/复核卡片点"打开原对话"时打开，不离开当前页面。
 */
export default function OriginalConversationDrawer() {
  const { state, closeConversationView } = useConversationStore();
  const convId = state.viewConversationId;
  const messageId = state.viewMessageId;

  const [conv, setConv] = useState(null);
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    if (!convId) return;
    setLoading(true);
    try {
      const [c, msgs] = await Promise.all([
        conversationService.getById(convId),
        // 有锚点消息（变更方案）→ 展示其附近上下文；无 → 只取最近 N 条
        // 都不加载整个历史
        messageId
          ? conversationService.getMessages(convId, { anchor_message_id: messageId, before: 15, after: 10 })
          : conversationService.getMessages(convId, { limit: 30, latest: true }),
      ]);
      setConv(c || null);
      // 过滤审批/复核数据（与对话页一致），只展示普通对话上下文
      const list = (msgs?.messages || []).filter(m => m.message_type !== 'confirm' && m.message_type !== 'review');
      setMessages(list);
    } catch { /* ignore */ }
    finally { setLoading(false); }
  }, [convId, messageId]);

  useEffect(() => {
    setConv(null);
    setMessages([]);
    if (convId) load();
  }, [convId, messageId, load]);

  return (
    <Drawer
      title={conv?.title || '原对话'}
      open={!!convId}
      onClose={closeConversationView}
      placement="right"
      width={560}
      closable
    >
      {loading ? (
        <div style={{ textAlign: 'center', padding: 60 }}><Spin /></div>
      ) : messages.length === 0 ? (
        <Empty description="暂无对话内容" style={{ padding: 60 }} />
      ) : (
        <div>
          {messages.map((m, i) => {
            const isUser = m.role === 'user';
            return (
              <div key={m.id || i} style={{ marginBottom: 14 }}>
                <div style={{ fontSize: 11, color: '#999', marginBottom: 4 }}>
                  {isUser ? '👤 用户' : '🤖 AI'}
                  {m.created_at ? ` · ${new Date(m.created_at).toLocaleString()}` : ''}
                </div>
                <div style={{
                  padding: '8px 12px', borderRadius: 8, fontSize: 13, lineHeight: 1.7,
                  background: isUser ? '#e6f4ff' : '#f6f6f6',
                  color: '#333', wordBreak: 'break-word',
                }}>
                  {/* 有变更方案的消息：隐藏原始 JSON 代码块，只展示方案摘要（与对话页一致） */}
                  <MarkdownRenderer
                    content={(m.metadata?.change_plans || []).length > 0
                      ? String(m.content || '').replace(/```(?:json)?\s*\n[\s\S]*?\n```/g, '')
                      : m.content}
                  />
                  {/* 变更方案摘要：展示消息 metadata 中的 change_plans（与对话页 ChangePlanPanel 同源） */}
                  {(m.metadata?.change_plans || []).length > 0 && (
                    <div style={{ marginTop: 8 }}>
                      {(m.metadata.change_plans).map((p, pi) => (
                        <div key={pi} style={{
                          padding: '6px 10px', borderRadius: 6, border: '1px solid #d6e4ff',
                          background: '#f0f5ff', marginBottom: 6, fontSize: 12, lineHeight: 1.7,
                        }}>
                          <div style={{ fontWeight: 600, color: '#333' }}>📋 {p.label || p.name || '变更方案'}</div>
                          {(() => {
                            const steps = p.steps_preview || p.action_labels || p.actions || [];
                            if (steps.length) {
                              return <div style={{ color: '#555' }}>📝 步骤：{steps.join(' → ')}</div>;
                            }
                            return null;
                          })()}
                          {p.precondition && <div style={{ color: '#555' }}>📌 前提：{p.precondition}</div>}
                          {p.impact && <div style={{ color: '#555' }}>📊 影响：{p.impact}</div>}
                          {p.risk && <div style={{ color: p.risk === 'high' ? '#ff4d4f' : '#faad14' }}>
                            ⚠ 风险：{{ low: '低', medium: '中', high: '高' }[p.risk] || p.risk}
                          </div>}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
                {i < messages.length - 1 && <Divider style={{ margin: '8px 0' }} />}
              </div>
            );
          })}
        </div>
      )}
    </Drawer>
  );
}
