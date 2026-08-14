import React, { useEffect, useRef, useState } from 'react';
import { Tag, Typography, Spin, Empty, Card, Statistic, Row, Col, Select, Table } from 'antd';
import { ProTable } from '@ant-design/pro-components';
import request from '../../services/request';

// 状态色
const STATUS_COLOR = { ok: 'green', error: 'red', failed: 'red' };
const KIND_LABEL = { llm: 'LLM', generic: '通用', io: 'IO', tool: '工具', default: '步骤' };
const SPAN_NAME_LABEL = {
  memory_retrieve: '记忆检索',
  history_load: '历史加载',
  route: '链检测/路由',
  route_intent: '意图路由',
  tool_exec: '工具执行',
  db_query: '数据查询',
  format: 'LLM 格式化',
  persist: '持久化',
  llm: 'LLM 调用',
  dynamic_plan: '动态规划',
  dynamic_review: '计划评审',
  dynamic_extract: '参数填槽',
  dynamic_reflect: '全局反思',
  dynamic_summarize: '汇总',
  knowledge_retrieve: '领域知识检索',
};

/** 展开内容：概览 + span 瀑布 */
function TraceDetail({ detail }) {
  // 命中的领域知识默认折叠，点击「命中 N 条」徽标切换展开
  const [expandedHits, setExpandedHits] = useState({});
  if (!detail) return <Spin size="small" />;
  const spans = detail.spans || [];
  const totalMs = detail.total_ms || 0;
  const maxDur = spans.reduce((m, s) => Math.max(m, s.dur_ms || 0), 1);

  return (
    <div style={{ padding: '12px 16px', background: '#fafafa' }}>
      {/* 概览 */}
      <div style={{ display: 'flex', gap: 24, marginBottom: 12, flexWrap: 'wrap', fontSize: 13, color: '#555' }}>
        <span>状态：<Tag color={STATUS_COLOR[detail.status] || 'default'} style={{ marginRight: 0 }}>{detail.status || '-'}</Tag></span>
        <span>总耗时：<b>{totalMs}ms</b></span>
        <span>LLM 调用：<b>{detail.llm_calls || 0}</b> 次</span>
        <span>总 Token：<b>{(detail.total_tokens || 0).toLocaleString()}</b></span>
        <span>Namespace：<b>{detail.namespace || '-'}</b></span>
      </div>

      {detail.error && (
        <div style={{ marginBottom: 12, padding: '8px 12px', background: '#fff1f0', border: '1px solid #ffccc7', borderRadius: 4, color: '#cf1322', fontSize: 12 }}>
          错误：{detail.error}
        </div>
      )}

      {/* span 瀑布 */}
      {spans.length === 0 ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="无 span 记录（该 trace 可能未圈定子步骤）" />
      ) : (
        <div>
          <Typography.Text type="secondary" style={{ fontSize: 12, marginBottom: 8, display: 'block' }}>
            Span 瀑布（横向位置 = 开始时间，长度 = 耗时，相对总耗时 {totalMs}ms）
          </Typography.Text>
          <div style={{ marginBottom: 12 }}>
            {spans.map((s, i) => (
              <div key={i}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 5 }}>
                  <div style={{ width: 170, flexShrink: 0, textAlign: 'right', fontSize: 12, color: '#333',
                    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                    title={s.meta?.concept ? `查询概念：${s.meta.concept}` : undefined}>
                    {(SPAN_NAME_LABEL[s.name] || s.name)}{s.meta?.concept ? ` · ${s.meta.concept}` : ''}
                  </div>
                  <div style={{ flex: 1, position: 'relative', height: 18, background: '#f0f0f0', borderRadius: 3 }}>
                    <div style={{
                      position: 'absolute',
                      left: `${totalMs ? (s.start_ms / totalMs) * 100 : 0}%`,
                      width: `${totalMs ? Math.max((s.dur_ms / totalMs) * 100, 0.6) : 0}%`,
                      height: '100%',
                      background: s.status === 'error' ? '#ff4d4f' : '#6c5ce7',
                      borderRadius: 3, minWidth: 2,
                    }} />
                  </div>
                  <div style={{ width: 64, flexShrink: 0, fontSize: 11, color: '#999' }}>{s.dur_ms}ms</div>
                  <div style={{ width: 52, flexShrink: 0, fontSize: 11, color: '#bbb' }}>{KIND_LABEL[s.kind] || s.kind || ''}</div>
                  {s.meta?.tokens ? <div style={{ width: 80, flexShrink: 0, fontSize: 11, color: '#8a6d3b' }}>{s.meta.tokens} tok</div> : null}
                  {s.meta?.prompt_chars ? <div style={{ width: 85, flexShrink: 0, fontSize: 11, color: '#999' }}>{s.meta.prompt_chars} 字</div> : null}
                  {s.meta?.row_count != null ? <div style={{ width: 60, flexShrink: 0, fontSize: 11, color: '#1677ff' }}>{s.meta.row_count} 行</div> : null}
                  {s.meta?.hit_count != null ? (
                    <div onClick={() => setExpandedHits((p) => ({ ...p, [i]: !p[i] }))}
                      style={{ width: 82, flexShrink: 0, fontSize: 11, color: '#722ed1', cursor: 'pointer', userSelect: 'none' }}>
                      {expandedHits[i] ? '▾' : '▸'} 命中 {s.meta.hit_count} 条
                    </div>
                  ) : null}
                  {s.meta?.thinking ? <Tag color="orange" style={{ marginRight: 0, fontSize: 10 }}>深度思考</Tag> : null}
                </div>
                {expandedHits[i] && s.meta?.hits?.length ? (
                  <div style={{ marginLeft: 178, marginBottom: 8, padding: '6px 10px', fontSize: 11, lineHeight: 1.8,
                    color: '#531dab', background: '#f9f0ff', border: '1px solid #efdbff', borderRadius: 6 }}>
                    <div style={{ fontWeight: 600, marginBottom: 2 }}>命中领域知识：</div>
                    {s.meta.hits.map((h, j) => <div key={j}>• {h}</div>)}
                  </div>
                ) : null}
              </div>
            ))}
          </div>

          {/* 占比说明 */}
          {spans.some(s => s.dur_ms > 0) && (
            <div style={{ fontSize: 11, color: '#999' }}>
              最耗时步骤：{spans.reduce((a, b) => (b.dur_ms > a.dur_ms ? b : a), spans[0]).name}（{maxDur}ms，占 {(maxDur / totalMs * 100).toFixed(0)}%）
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/** 执行质量聚合面板：总执行 / 成功率 / 错误环节分布 / 最近失败（可下钻到 span 详情） */
function TraceSummary() {
  const [summary, setSummary] = useState(null);
  const [days, setDays] = useState(7);
  const [detail, setDetail] = useState(null);
  const [detailTraceId, setDetailTraceId] = useState(null);

  useEffect(() => {
    let alive = true;
    setSummary(null);
    request.get(`/tracing/summary?days=${days}`)
      .then((d) => { if (alive) setSummary(d); })
      .catch(() => {});
    return () => { alive = false; };
  }, [days]);

  const openTrace = async (traceId) => {
    setDetailTraceId(traceId);
    setDetail(null);
    try {
      const d = await request.get(`/tracing/traces/${traceId}`);
      setDetail(d);
    } catch {
      setDetail({ status: 'error', error: '加载详情失败', spans: [] });
    }
  };

  if (!summary) {
    return <Card size="small" style={{ marginBottom: 16 }}><Spin size="small" style={{ marginRight: 8 }} />加载执行质量…</Card>;
  }

  const nsEntries = Object.entries(summary.by_namespace || {});
  const errEntries = Object.entries(summary.errors || {});
  const failures = summary.recent_failures || [];

  return (
    <Card
      size="small"
      style={{ marginBottom: 16, borderRadius: 8 }}
      title={<span>🩺 执行质量（近 {summary.days} 天）</span>}
      extra={
        <Select size="small" value={days} onChange={setDays} style={{ width: 100 }}
          options={[{ value: 7, label: '近7天' }, { value: 30, label: '近30天' }]} />
      }
    >
      {/* 指标卡 */}
      <Row gutter={12} style={{ marginBottom: 12 }}>
        <Col span={6}><Statistic title="总执行" value={summary.total} suffix="次" /></Col>
        <Col span={6}><Statistic title="成功率" value={summary.success_rate} suffix="%" precision={1}
          valueStyle={{ color: summary.success_rate >= 90 ? '#52c41a' : '#faad14' }} /></Col>
        <Col span={6}><Statistic title="失败" value={summary.failed} suffix="次"
          valueStyle={{ color: summary.failed > 0 ? '#ff4d4f' : '#52c41a' }} /></Col>
        <Col span={6}><Statistic title="异常环节" value={errEntries.length} suffix="类" /></Col>
      </Row>

      {/* 错误环节分布 */}
      {errEntries.length > 0 && (
        <div style={{ marginBottom: 8, fontSize: 12, color: '#888' }}>
          常失败环节：{errEntries.map(([name, cnt]) => (
            <Tag key={name} color="red" style={{ marginBottom: 4 }}>{name} × {cnt}</Tag>
          ))}
        </div>
      )}

      {/* 按 namespace 分组（多业务域时展示） */}
      {nsEntries.length > 1 && (
        <Table size="small" rowKey="ns" pagination={false} style={{ marginBottom: 12 }}
          dataSource={nsEntries.map(([ns, v]) => ({ ns, ...v }))}
          columns={[
            { title: '业务域 (namespace)', dataIndex: 'ns' },
            { title: '执行数', dataIndex: 'count', width: 90 },
            { title: '成功率', dataIndex: 'success_rate', width: 100,
              render: (_, r) => {
                const rate = r.count ? Math.round((r.count - r.failed) / r.count * 100) : 0;
                return <Tag color={rate >= 90 ? 'green' : 'orange'}>{rate}%</Tag>;
              } },
            { title: '失败数', dataIndex: 'failed', width: 90,
              render: (v) => v > 0 ? <Tag color="red">{v}</Tag> : <span style={{ color: '#999' }}>-</span> },
          ]} />
      )}

      {/* 最近失败明细（可下钻） */}
      {failures.length > 0 && (
        <div>
          <Typography.Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 8 }}>
            最近失败（点击查看 span 详情）
          </Typography.Text>
          <Table size="small" rowKey="trace_id" pagination={{ pageSize: 5, size: 'small' }}
            dataSource={failures}
            onRow={(r) => ({ onClick: () => openTrace(r.trace_id), style: { cursor: 'pointer' } })}
            columns={[
              { title: '时间', dataIndex: 'created_at', width: 150,
                render: (_, r) => r.created_at ? new Date(r.created_at).toLocaleString() : '-' },
              { title: '消息', dataIndex: 'message', ellipsis: true },
              { title: '错误', dataIndex: 'error', ellipsis: true, render: (_, r) => r.error || '-' },
            ]} />
        </div>
      )}

      {/* 下钻详情 */}
      {detailTraceId && (
        <div style={{ marginTop: 12, border: '1px solid #f0f0f0', borderRadius: 6, background: '#fff' }}>
          <div style={{ padding: '8px 16px', fontSize: 12, color: '#666', borderBottom: '1px solid #f0f0f0',
            display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span>Trace {detailTraceId}</span>
            <Typography.Link onClick={() => { setDetailTraceId(null); setDetail(null); }} style={{ fontSize: 12 }}>
              关闭
            </Typography.Link>
          </div>
          <TraceDetail detail={detail} />
        </div>
      )}
    </Card>
  );
}

export default function TraceView() {
  const actionRef = useRef();
  const [expandedKeys, setExpandedKeys] = useState([]);
  const [details, setDetails] = useState({}); // trace_id → 详情

  const loadDetail = async (traceId) => {
    if (details[traceId]) return;
    try {
      const d = await request.get(`/tracing/traces/${traceId}`);
      setDetails((prev) => ({ ...prev, [traceId]: d }));
    } catch {
      setDetails((prev) => ({ ...prev, [traceId]: { spans: [], status: 'error', error: '加载详情失败' } }));
    }
  };

  const columns = [
    { title: '时间', dataIndex: 'created_at', width: 160, search: false,
      render: (_, r) => r.created_at ? new Date(r.created_at).toLocaleString() : '-' },
    { title: '用户', dataIndex: 'user_id', width: 110, search: false,
      render: (_, r) => r.user_id || '-' },
    { title: '消息', dataIndex: 'message', ellipsis: true,
      render: (_, r) => r.message ? <span style={{ fontSize: 12 }}>{r.message}</span> : '-' },
    { title: '状态', dataIndex: 'status', width: 80, search: false,
      render: (_, r) => <Tag color={STATUS_COLOR[r.status] || 'default'}>{r.status || '-'}</Tag> },
    { title: '耗时', dataIndex: 'total_ms', width: 90, search: false,
      render: (_, r) => r.total_ms != null ? `${r.total_ms}ms` : '-' },
    { title: 'LLM 调用', dataIndex: 'llm_calls', width: 90, search: false,
      render: (_, r) => r.llm_calls || 0 },
    { title: 'Token', dataIndex: 'total_tokens', width: 100, search: false,
      render: (_, r) => r.total_tokens ? r.total_tokens.toLocaleString() : '-' },
  ];

  return (
    <div style={{ padding: '24px 24px 48px', height: '100%', overflow: 'auto', background: '#f5f5f7', boxSizing: 'border-box' }}>
      <TraceSummary />
      <ProTable
        actionRef={actionRef}
        columns={columns}
        rowKey="trace_id"
        search={false}
        options={{ reload: true, density: true }}
        pagination={{ defaultPageSize: 20, showSizeChanger: false }}
        scroll={{ x: 'max-content' }}
        headerTitle="LLM 追踪"
        expandable={{
          expandedRowRender: (record) => <TraceDetail detail={details[record.trace_id]} />,
          expandedRowKeys: expandedKeys,
          onExpand: (expanded, record) => {
            setExpandedKeys(expanded ? [record.trace_id] : []);
            if (expanded) loadDetail(record.trace_id);
          },
        }}
        onRow={(record) => ({
          onClick: () => {
            setExpandedKeys(expandedKeys.includes(record.trace_id) ? [] : [record.trace_id]);
            loadDetail(record.trace_id);
          },
          style: { cursor: 'pointer' },
        })}
        request={async (params) => {
          const data = await request.get('/tracing/traces?limit=100');
          const list = Array.isArray(data) ? data : [];
          return { data: list, total: list.length, success: true };
        }}
        locale={{ emptyText: '暂无追踪记录（发起一次对话后自动记录）' }}
      />
    </div>
  );
}
