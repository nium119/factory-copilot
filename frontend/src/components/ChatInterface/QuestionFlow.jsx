import React, { useState } from 'react';
import { Button } from 'antd';

// 逐题问卷（DSH 式）：一次只问一组，1/N 进度 + 自由输入 + 选项点选 + 跳过本题/下一题导航。
// 两组消费方：
//  - MessageItem：done 事件 quick_replies groups（DynamicPlanner ask），提交走 quick-reply 事件；
//  - ClarifyTakeoverBar：clarify_required 事件 groups（loop_ask 逐项收集），提交走回调。
// 组格式：{ label: '字段中文名（问法）', options: [{label, description}...], required: true|false }
export default function QuestionFlow({ groups = [], submitting = false, onSubmit, optionChip }) {
  const [idx, setIdx] = useState(0);
  const [answers, setAnswers] = useState({});
  const [draft, setDraft] = useState('');
  const total = groups.length;
  if (total === 0) return null;
  const group = groups[idx] || {};
  const answeredCount = Object.keys(answers).length;
  const isLast = idx === total - 1;
  const currentAnswer = answers[idx];
  const required = !!group.required;
  // 必填校验要把当前输入框里的草稿（draft）视作本题的临时答案——
  // 否则最后一题打完字不回车，提交按钮仍是灰的（用户输入未被计入）
  const mergedNow = draft.trim() ? { ...answers, [idx]: draft.trim() } : answers;
  const requiredMissing = groups.some((g, gi) => g.required && !mergedNow[gi]);
  const canSubmit = !requiredMissing && Object.keys(mergedNow).length > 0;

  const applyAnswer = (val) => {
    const v = (val || '').trim();
    setAnswers(prev => {
      const next = { ...prev };
      if (v) next[idx] = v; else delete next[idx];
      return next;
    });
    setDraft('');
  };
  const goNext = () => {
    if (draft.trim()) applyAnswer(draft);
    setIdx(i => Math.min(i + 1, total - 1));
  };
  const submit = () => {
    if (submitting) return;
    const merged = draft.trim() ? { ...answers, [idx]: draft.trim() } : answers;
    const parts = Object.keys(merged).sort((a, b) => a - b).map(k => merged[k]).filter(Boolean);
    if (parts.length === 0) return;
    // 回调返回 false 表示提交失败（保持现场）；成功由调用方收尾
    const ok = onSubmit && onSubmit(parts);
    if (ok !== false) { setAnswers({}); setDraft(''); setIdx(0); }
  };

  const Chip = optionChip; // 选项渲染委托（MessageItem 传 OptionChip 保持推荐样式）

  return (
    <div style={{ marginTop: 10, border: '1px solid #ece9fb', borderRadius: 12, padding: '12px 14px', background: '#fbfaff', maxWidth: 520 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <span style={{ fontSize: 11, color: '#999' }}>
          {idx + 1} / {total}{answeredCount > 0 && ` · 已答 ${answeredCount}`}
          {required && <span style={{ color: '#fa8c16' }}> · 必填</span>}
        </span>
        {!required && (
          <span style={{ fontSize: 11, color: '#bbb', cursor: 'pointer' }}
            onClick={() => { applyAnswer(''); setIdx(i => Math.min(i + 1, total - 1)); }}>
            跳过本题 →
          </span>
        )}
      </div>
      <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>
        {group.label}{required && <span style={{ color: '#fa8c16', marginLeft: 4 }}>*</span>}
      </div>
      {(group.options || []).length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 8 }}>
          {(group.options || []).map((opt, oi) => {
            const o = typeof opt === 'string' ? { label: opt, description: '' } : (opt || {});
            const selected = currentAnswer === o.label;
            if (Chip) {
              return <Chip key={oi} opt={o} selected={selected} onClick={() => (selected ? applyAnswer('') : applyAnswer(o.label))} />;
            }
            return (
              <Button key={oi} size="small" type={selected ? 'primary' : 'default'} style={{ borderRadius: 999 }}
                disabled={submitting} title={o.description || o.label}
                onClick={() => (selected ? applyAnswer('') : applyAnswer(o.label))}>
                {o.label}
              </Button>
            );
          })}
        </div>
      )}
      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <input
          style={{ flex: 1, fontSize: 13, height: 32, padding: '0 10px', borderRadius: 8, border: '1px solid #e5e5e5', outline: 'none', boxSizing: 'border-box' }}
          placeholder="输入你的答案（可选，直接回车进入下一题）"
          value={draft}
          disabled={submitting}
          onChange={e => setDraft(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); goNext(); } }}
        />
        {!isLast ? (
          <Button onClick={goNext} style={{ borderRadius: 14, height: 32 }}>下一题</Button>
        ) : (
          <Button type="primary" disabled={!canSubmit || submitting}
            title={requiredMissing ? '还有必填题未回答（带 * 的题不能跳过）' : ''}
            style={{ borderRadius: 14, height: 32 }} onClick={submit}>
            提交
          </Button>
        )}
      </div>
    </div>
  );
}
