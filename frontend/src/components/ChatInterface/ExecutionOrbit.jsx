import React, { useMemo, useState } from 'react';
import { Button, Table, Drawer } from 'antd';
import {
  CheckCircleOutlined, CloseCircleOutlined, LoadingOutlined, BulbOutlined,
  ThunderboltOutlined, SearchOutlined, ToolOutlined, TeamOutlined,
  BranchesOutlined, DownOutlined, RightOutlined, QuestionCircleOutlined,
} from '@ant-design/icons';
import MarkdownRenderer from '../MarkdownRenderer';
import './ExecutionOrbit.css';

// 类型 → 元信息（图标 + 中文标识）
const TYPE_META = {
  thinking: { icon: <BranchesOutlined />, label: '思考' },
  plan: { icon: <ThunderboltOutlined />, label: '规划' },
  tool: { icon: <ToolOutlined />, label: '工具' },
  chain: { icon: <SearchOutlined />, label: '查询' },
  reflect: { icon: <BulbOutlined />, label: '反思' },
  collab: { icon: <TeamOutlined />, label: '协作' },
  exec: { icon: <BranchesOutlined />, label: '执行' },
};

// 状态 → 颜色 + 文字（亮色主题风格）
const STATUS_META = {
  running: { color: '#6c5ce7', label: '执行中' },
  done: { color: '#52c41a', label: '完成' },
  error: { color: '#ff4d4f', label: '失败' },
  pending: { color: '#bbb', label: '等待中' },
  reflect: { color: '#faad14', label: '反思' },
};

// 分层：任务层（用户关心的执行步骤） vs 明细层（底层执行细节）
const TASK_LAYER = new Set(['thinking', 'plan', 'tool', 'chain', 'reflect', 'collab']);

// 辅助：把 item 的分散执行信息统一成事件列表
function collectEvents(item) {
  const list = [];

  // 思考过程已由消息正文的「思考过程」折叠块展示，此处不再重复

  (item.planSteps || []).forEach((s, i) => {
    list.push({
      id: `plan_${i}`, type: 'plan',
      status: s.status === 'success' ? 'done' : s.status === 'failed' ? 'error' : s.status === 'running' ? 'running' : 'pending',
      label: s.name || `步骤 ${i + 1}`,
      detail: s.data,
    });
  });

  (item.toolCalls || []).forEach((tc, i) => {
    list.push({
      id: `tool_${i}`, type: 'tool',
      status: tc.status === 'executing' ? 'running' : tc.status === 'error' ? 'error' : 'done',
      label: tc.name || tc.functionName || `工具 ${i + 1}`,
      detail: tc.arguments ? (typeof tc.arguments === 'string' ? tc.arguments : JSON.stringify(tc.arguments)) : (tc.result || tc.error || ''),
    });
  });

  (item.chainSteps || []).forEach((s, i) => {
    const isThink = s.status === 'think';
    list.push({
      id: `chain_${s.step_id || i}`, type: isThink ? 'reflect' : 'chain',
      // think 事件是一次性反思结论（事件到达即反思完成），标 done 而非恒 reflect —— 反思节点要有完成状态码
      status: isThink ? 'done'
        : s.status === 'done' ? 'done'
        : s.status === 'error' ? 'error'
        : s.status === 'running' ? 'running' : 'pending',
      // 反思节点直接展示结论（description 里是反思内容），concept_label 仅作兜底
      label: isThink ? (s.description || s.concept_label || s.step_id || '反思')
        : (s.concept_label || s.description || s.step_id || `步骤 ${i + 1}`),
      detail: s.content || s.output_preview || s.error || (isThink ? s.description : ''),
    });
  });

  (item.collabAgents || []).forEach((a) => {
    // 外部协作节点是用户关心的任务层节点，即使无数据（empty）也要显示状态，不能因 detail 为空被减噪过滤
    const emptyHint = a.status === 'empty' ? '无匹配数据' : '';
    list.push({
      id: `collab_${a.name}`, type: 'collab',
      status: a.status === 'success' ? 'done'
        : (a.status === 'timeout' || a.status === 'error') ? 'error'
        : a.status === 'running' ? 'running'
        : a.status === 'empty' ? 'done' : 'pending',
      label: a.display_name || a.name,
      detail: a.data || a.error || emptyHint,
    });
  });

  if (item.reflectionReason) {
    list.push({
      id: 'reflection', type: 'reflect',
      status: item.isReflectionActive ? 'running' : 'done',
      label: item.isReflectionActive ? '正在自我修正' : '已自我修正',
      detail: item.reflectionReason,
    });
  }

  // 底层执行细节（路由/参数/工具执行/格式化）→ 明细层
  // DSH 式：合并 tool_start + tool_result 成「一个工具调用 = 一行」，
  // 跳过 route_match/param_extract/format_start/execution_done 等内部流水线标签；
  // step_note（LLM 中间关键信息）渲染成正文说明。
  (item.executionSteps || []).forEach((s, i) => {
    const isNote = s.key === 'step_note' || s.note === true;
    if (isNote) {
      // 用户决定不显示 LLM 动作复述说明（对齐 DSH：思考 → 工具行 → 结果，无中间说明段落）。
      // 中间关键信息已体现在「思考摘要 + 工具行 + 工具结果」里，不再单独占一行。
      return;
    }
    // 合并：tool_result 紧跟 tool_start 时，结果作为工具行的摘要，不再单独成行
    if (s.key === 'tool_start') {
      // 向后收集紧跟的 tool_result（含 0 条反思重查补充的 tool_result），取最后一个作最终结果
      const steps = item.executionSteps || [];
      let resultStep = null;
      for (let j = i + 1; j < steps.length; j += 1) {
        const ns = steps[j];
        if (ns.key === 'tool_result') {
          resultStep = ns;
        } else if (ns.key === 'tool_start') {
          break;
        }
      }
      list.push({
        id: `exec_${i}`, type: 'tool',
        status: (s.status === 'success' || s.status === 'done') ? 'done'
          : (s.status === 'error' || s.status === 'failed') ? 'error'
          : s.status === 'running' ? 'running' : 'pending',
        // 工具名：tool_start 的 label 去掉「执行:」前缀
        label: (s.label || '').replace(/^执行[:：]\s*/, '') || s.tool || '工具调用',
        // 折叠摘要：用「参数摘要」（DSH 折叠行 summary 是 args 派生），不默认平铺「来源:N条」明细；
        // 「无查询条件」是全量查询的占位，置空只显示工具名。
        detail: (s.detail && s.detail !== '无查询条件') ? s.detail : '',
        // 一句话摘要（取第一个参数值，如工单号 MO001），折叠行与标题同行显示，对齐 DSH
        summary: s.summary || '',
        tool: s.tool || (resultStep && resultStep.tool) || '',
        beforeSnapshot: (resultStep && resultStep.before_snapshot) || null,
        landing: (resultStep && resultStep.landing) || null,
        createdEntityId: (resultStep && resultStep.created_entity_id) || null,
        actionType: (resultStep && resultStep.actionType) || '',
        rowCount: (resultStep && resultStep.rowCount) || 0,
        records: (resultStep && resultStep.records) || null,
        columns: (resultStep && resultStep.columns) || null,
      });
      return;
    }
    // clarify_required → Ask 工具行（DSH AskQuestionRow：消息流单行「待补充 · 缺字段 ▸」），
    // 展开看 Input（问句）+ Output（用户回答）。
    if (s.key === 'clarify_required') {
      const raw = s.detail || '';
      const answered = item.clarifyAnswered === true;
      const answer = item.clarifyAnswer || '';
      // 展开 detail：问句（Input）+ 回答（Output），对齐 DSH AskQuestionRow 展开的 IN/OUT
      const detail = (answered && answer) ? `${raw}\n\n**回答**：${answer}` : raw;
      list.push({
        id: `exec_${i}`, type: 'tool',
        status: 'done',
        label: answered ? '已补充' : '待补充',
        detail,
        summary: answered ? '已回答' : raw.replace(/^缺[:：]\s*/, ''),
        tool: '',
        ask: true,
      });
      return;
    }
    // 跳过内部流水线标签 + 与前端 composer 接管重复的 step：
    // route_match/param_extract/tool_result/format_start/execution_done 是内部过程；
    // confirm_required/confirm_result/confirm_delegated 由 composer 接管条渲染（DSH ApprovalPanel），
    // 不在消息流重复显示。
    if (['route_match', 'param_extract', 'tool_result', 'format_start', 'execution_done',
         'route_l2', 'route_l3', 'confirm_required', 'confirm_result', 'confirm_delegated'].includes(s.key)) {
      return;
    }
    // 其余（confirm_required/clarify_required 等）保留
    list.push({
      id: `exec_${i}`, type: 'exec', layer: 'detail',
      status: (s.status === 'success' || s.status === 'done') ? 'done'
        : (s.status === 'error' || s.status === 'failed') ? 'error'
        : s.status === 'running' ? 'running' : 'pending',
      label: s.label || s.name || s.key || `步骤 ${i + 1}`,
      detail: s.detail || s.output || s.error || '',
      tool: s.tool || '',
    });
  });

  // 空详情且非运行/错误的节点不展示，减少噪音；tool 行有工具名则保留（即使无参数摘要）
  return list.filter(e => e.detail || e.status === 'running' || e.status === 'error' || (e.type === 'tool' && e.label));
}

function ExecutionOrbit({ item, isStreaming, onSaveChain, onRestore }) {
  const [expanded, setExpanded] = useState(null);
  const [drawer, setDrawer] = useState(null);

  const all = useMemo(() => collectEvents(item), [item]);
  const taskEvents = all.filter(e => !e.layer || TASK_LAYER.has(e.type));
  const detailEvents = all.filter(e => e.layer === 'detail');
  if (!all.length) return null;

  // 动态规划执行完成后提供「保存为链」入口（执行轨道重构时曾丢失，已恢复）
  const isDynamicDone = item.isDynamic && item.isChainComplete
    && Array.isArray(item.chainSteps) && item.chainSteps.length > 0;

  const renderNode = (ev, idx, list) => {
    // note：循环中间关键信息，渲染成正文式说明段落（对齐 DSH 每轮 text block），非工具行
    if (ev.note) {
      return (
        <div key={ev.id} className="orbit-note">
          <MarkdownRenderer content={ev.detail || ''} />
        </div>
      );
    }
    const tMeta = TYPE_META[ev.type] || TYPE_META.chain;
    const sMeta = STATUS_META[ev.status] || STATUS_META.pending;
    const isOpen = expanded === ev.id;
    const isRunning = ev.status === 'running';
    // 删除类操作（有改前快照）或创建类操作（有新建 id）→ 提供「回滚」
    const canRollback = ev.tool && ((ev.beforeSnapshot && ev.beforeSnapshot.length > 0) || ev.createdEntityId);
    const detailText = typeof ev.detail === 'string' ? ev.detail : (ev.detail ? JSON.stringify(ev.detail) : '');
    // 工具行：折叠态摘要与标题同行（DSH ToolRow：icon + title · summary），
    // 完整参数/结果只展开显示；非工具行保持原有的「下一行单行预览」。
    const isTool = ev.type === 'tool';
    const summaryText = isTool ? (ev.summary || '') : '';
    const inlineText = isTool ? '' : detailText;
    // 查询结果超过消息流展示上限（后端表格只渲染前 10 条）时提供「查看全部」→ 右侧抽屉分页
    const hasMoreRecords = isTool && Array.isArray(ev.records) && ev.records.length > 0
      && (ev.rowCount || 0) > 10;
    return (
      <div key={ev.id} className={`orbit-node orbit-node--${ev.status}`}>
        <div
          className="orbit-body"
          onClick={() => detailText && setExpanded(isOpen ? null : ev.id)}
          style={{ cursor: detailText ? 'pointer' : 'default' }}
        >
          <div className="orbit-row">
            <span className="orbit-type-icon" style={{ color: isRunning ? sMeta.color : undefined }}>{isRunning ? <LoadingOutlined /> : (ev.ask ? <QuestionCircleOutlined /> : tMeta.icon)}</span>
            <span className="orbit-label" style={{ color: isRunning ? sMeta.color : undefined, flex: isTool ? 'none' : undefined }}>{ev.label}</span>
            {canRollback && (
              <Button size="small" style={{ fontSize: 11, padding: '0 6px', height: 20, marginRight: 6 }}
                onClick={(e) => { e.stopPropagation(); onRestore?.(ev.tool, ev.beforeSnapshot, ev.createdEntityId); }}>
                回滚
              </Button>
            )}
            {hasMoreRecords && (
              <Button size="small" type="link" style={{ fontSize: 11, padding: '0 6px', height: 20, marginRight: 6 }}
                onClick={(e) => { e.stopPropagation(); setDrawer({ label: ev.label, records: ev.records, columns: ev.columns, rowCount: ev.rowCount }); }}>
                查看全部 {ev.rowCount} 条
              </Button>
            )}
            {/* DSH 式：标题与摘要同行，用圆点分隔；摘要过长省略 */}
            {isTool && summaryText && <span className="orbit-sep" aria-hidden="true" />}
            {isTool && summaryText && <span className="orbit-summary">{summaryText}</span>}
            {detailText && (isOpen ? <DownOutlined className="orbit-arrow" /> : <RightOutlined className="orbit-arrow" />)}
          </div>
          {/* 非工具行：未展开时显示单行内容预览 */}
          {!isOpen && inlineText && (
            <div className="orbit-detail-inline">{inlineText}</div>
          )}
          {isOpen && detailText && (
            <div className="orbit-detail">
              <MarkdownRenderer content={detailText} />
            </div>
          )}
        </div>
      </div>
    );
  };

  // 抽屉分页表格：列定义优先取后端 columns，兜底从首条记录提取字段
  const drawerColumns = (() => {
    if (!drawer) return [];
    if (drawer.columns && drawer.columns.length) {
      return drawer.columns.map((c) => ({ title: c.title || c.key, dataIndex: c.key, key: c.key, ellipsis: true }));
    }
    const first = (drawer.records && drawer.records[0]) || {};
    return Object.keys(first).filter((k) => !k.startsWith('_'))
      .map((k) => ({ title: k, dataIndex: k, key: k, ellipsis: true }));
  })();
  const drawerData = drawer ? (drawer.records || []) : [];

  return (
    <div className="orbit" style={{ margin: '10px 0' }}>
      <div className="orbit-track">
        {taskEvents.map((ev, idx) => renderNode(ev, idx, taskEvents))}
        {detailEvents.map((ev, idx) => renderNode(ev, idx, detailEvents))}
        {isDynamicDone && typeof onSaveChain === 'function' && (
          <div style={{ marginTop: 8, paddingTop: 8, borderTop: '1px dashed #e0e0ec' }}>
            <Button size="small" type="link" style={{ fontSize: 12, padding: 0 }}
              onClick={(e) => { e.stopPropagation(); onSaveChain(item.chainSteps, item.chainName || '动态规划链', item.id); }}>
              保存为链
            </Button>
          </div>
        )}
      </div>
      <Drawer
        title={drawer ? `${drawer.label || '查询结果'} · 共 ${drawer.rowCount || drawerData.length} 条` : '查询结果'}
        placement="right"
        width={760}
        open={!!drawer}
        onClose={() => setDrawer(null)}
        destroyOnClose
      >
        <Table
          size="small"
          columns={drawerColumns}
          dataSource={drawerData}
          rowKey={(r, i) => String(r.id || r.code || r.materialCode || r._id || i)}
          pagination={{
            pageSize: 10,
            showSizeChanger: true,
            pageSizeOptions: [10, 20, 50, 100],
            showTotal: (t) => `共 ${t} 条`,
          }}
          scroll={{ x: 'max-content' }}
        />
      </Drawer>
    </div>
  );
}

export default ExecutionOrbit;
