import React, { useState, useEffect, useRef } from 'react';
import { Spin, Card, Row, Col, Tag } from 'antd';
import { CheckCircleOutlined, CloseCircleOutlined } from '@ant-design/icons';
import * as echarts from 'echarts';
import request from '../../services/request';

// ── 工具函数 ──
const fmtK = (n) => (n >= 1000 ? `${Math.round(n / 1000)}K` : n);
const hm = (ts) => new Date(ts * 1000).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });

const TIER_META = {
  optimal: { label: '最佳', color: '#52c41a' },
  normal: { label: '正常', color: '#52c41a' },
  constrained: { label: '繁忙', color: '#faad14' },
  critical: { label: '严重', color: '#ff4d4f' },
};

export default function ResourceStatusView() {
  const [state, setState] = useState(null);
  const [loading, setLoading] = useState(true);
  const [updatedAt, setUpdatedAt] = useState(null);

  const trendRef = useRef(null);
  const trendChart = useRef(null);
  const tokenRef = useRef(null);
  const tokenChart = useRef(null);

  // 10s 轮询刷新
  useEffect(() => {
    const fetchResources = async () => {
      try {
        const data = await request.get('/system/resources');
        setState(data);
        setUpdatedAt(new Date());
      } catch (e) { /* ignore */ }
      setLoading(false);
    };
    fetchResources();
    const timer = setInterval(fetchResources, 10000);
    return () => clearInterval(timer);
  }, []);

  // 实时负载趋势：并发请求 + API/分（双折线面积图）
  useEffect(() => {
    if (!state || !trendRef.current) return;
    if (trendChart.current) { trendChart.current.dispose(); }
    trendChart.current = echarts.init(trendRef.current);
    const h = state.history || [];
    trendChart.current.setOption({
      tooltip: { trigger: 'axis' },
      legend: { top: 0, textStyle: { fontSize: 11 } },
      grid: { left: 36, right: 16, top: 28, bottom: 22 },
      xAxis: { type: 'category', data: h.map(p => hm(p.ts)), axisLabel: { fontSize: 9 } },
      yAxis: { type: 'value', minInterval: 1 },
      series: [
        {
          name: '并发请求', type: 'line', smooth: true, symbolSize: 4,
          data: h.map(p => p.concurrent),
          lineStyle: { width: 2, color: '#fa8c16' }, itemStyle: { color: '#fa8c16' },
          areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: '#fa8c1666' }, { offset: 1, color: '#fa8c1600' }]) },
        },
        {
          name: 'API/分', type: 'line', smooth: true, symbolSize: 4,
          data: h.map(p => p.api_cpm),
          lineStyle: { width: 2, color: '#1677ff' }, itemStyle: { color: '#1677ff' },
          areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: '#1677ff55' }, { offset: 1, color: '#1677ff00' }]) },
        },
      ],
    });
  }, [state]);

  // Token 用量趋势：面积图 + 预算标记线
  useEffect(() => {
    if (!state || !tokenRef.current) return;
    if (tokenChart.current) { tokenChart.current.dispose(); }
    tokenChart.current = echarts.init(tokenRef.current);
    const h = state.history || [];
    const budget = state.thresholds?.token_budget_per_hour || 500000;
    tokenChart.current.setOption({
      tooltip: {
        trigger: 'axis',
        formatter: (ps) => {
          const p = ps[0];
          return `<b>${hm(h[p.dataIndex].ts)}</b><br/>Token: <b>${fmtK(p.value)}</b>`;
        },
      },
      grid: { left: 52, right: 16, top: 20, bottom: 22 },
      xAxis: { type: 'category', data: h.map(p => hm(p.ts)), axisLabel: { fontSize: 9 } },
      yAxis: { type: 'value', axisLabel: { formatter: v => fmtK(v), fontSize: 10 } },
      series: [{
        type: 'line', smooth: true, symbolSize: 4,
        data: h.map(p => p.token_hour),
        lineStyle: { width: 2, color: '#722ed1' }, itemStyle: { color: '#722ed1' },
        areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: '#722ed155' }, { offset: 1, color: '#722ed100' }]) },
        markLine: {
          symbol: 'none', silent: true,
          data: [{
            yAxis: budget, lineStyle: { color: '#ff4d4f', type: 'dashed', width: 1 },
            label: { formatter: `预算 ${fmtK(budget)}`, color: '#ff4d4f', fontSize: 10, position: 'insideEndTop' },
          }],
        },
      }],
    });
  }, [state]);

  // 卸载清理图表实例
  useEffect(() => () => {
    trendChart.current?.dispose(); trendChart.current = null;
    tokenChart.current?.dispose(); tokenChart.current = null;
  }, []);

  if (loading) return <div style={{ textAlign: 'center', padding: 80 }}><Spin size="large" /></div>;
  if (!state) return <div style={{ textAlign: 'center', padding: 80, color: '#999' }}>无法获取资源状态</div>;

  const tier = TIER_META[state.tier] || TIER_META.normal;
  const h = state.history || [];
  const sampling = h.length < 2;

  const cards = [
    {
      title: '并发请求', color: state.concurrent_requests >= (state.thresholds?.critical_at || 9) ? '#ff4d4f' : '#52c41a',
      value: state.concurrent_requests, suffix: ` / ${state.max_concurrency}`,
    },
    {
      title: 'API 调用/分', color: '#1677ff',
      value: state.api_calls_per_minute, suffix: ` / ${state.thresholds?.max_api_calls_per_minute || 30}`,
    },
    {
      title: 'Token 使用/时', color: '#722ed1',
      value: fmtK(state.token_usage_this_hour), suffix: ` / ${fmtK(state.thresholds?.token_budget_per_hour || 500000)}`,
    },
    {
      title: '模型层级', color: tier.color,
      value: state.model_tier || '-', suffix: tier.label,
    },
  ];

  return (
    <div style={{ padding: 20, overflow: 'auto' }}>
      {/* 顶部标题 + 更新时间 */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        <span style={{ fontSize: 16, fontWeight: 600 }}>⚡ 资源监控</span>
        <span style={{ fontSize: 12, color: '#999' }}>
          {state.enabled ? '' : '资源感知已关闭 · '}
          {updatedAt ? `更新于 ${updatedAt.toLocaleTimeString('zh-CN')} · 每10s自动刷新` : '加载中…'}
        </span>
      </div>

      {/* 状态横幅 */}
      <div style={{
        background: `linear-gradient(135deg, ${tier.color} 0%, ${tier.color}cc 100%)`,
        borderRadius: 14, padding: '18px 24px', marginBottom: 16, color: '#fff',
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        boxShadow: `0 4px 16px ${tier.color}33`,
      }}>
        <div>
          <div style={{ fontSize: 12, opacity: 0.85 }}>系统运行状态</div>
          <div style={{ fontSize: 28, fontWeight: 800 }}>{tier.label}</div>
        </div>
        <div style={{ fontSize: 44, opacity: 0.18, fontWeight: 900 }}>
          {state.tier === 'critical' ? '!' : state.tier === 'constrained' ? '~' : '✓'}
        </div>
      </div>

      {/* 指标卡 */}
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        {cards.map((c, i) => (
          <Col span={6} key={i}>
            <Card size="small" styles={{ body: { padding: '14px 18px' } }}
              style={{ borderRadius: 12, boxShadow: '0 2px 10px rgba(0,0,0,0.05)' }}>
              <div style={{ fontSize: 12, color: '#999', marginBottom: 4 }}>{c.title}</div>
              <div style={{ fontSize: 22, fontWeight: 700 }}>
                <span style={{ color: c.color }}>{c.value}</span>
                <span style={{ fontSize: 12, color: '#aaa', fontWeight: 400 }}> {c.suffix}</span>
              </div>
            </Card>
          </Col>
        ))}
      </Row>

      {/* 趋势图 */}
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col span={12}>
          <Card size="small" title="实时负载趋势（近5分钟）" style={{ borderRadius: 12 }} styles={{ body: { padding: 4 } }}>
            {sampling
              ? <div style={{ height: 200, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#999', fontSize: 12 }}>数据采集中…</div>
              : <div ref={trendRef} style={{ height: 200 }} />}
          </Card>
        </Col>
        <Col span={12}>
          <Card size="small" title="Token 用量趋势（近5分钟）" style={{ borderRadius: 12 }} styles={{ body: { padding: 4 } }}>
            {sampling
              ? <div style={{ height: 200, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#999', fontSize: 12 }}>数据采集中…</div>
              : <div ref={tokenRef} style={{ height: 200 }} />}
          </Card>
        </Col>
      </Row>

      <SystemHealthPanel />
    </div>
  );
}

function SystemHealthPanel() {
  const [checks, setChecks] = useState({});
  useEffect(() => {
    const fetchHealth = async () => { try { const data = await request.get('/system/health'); setChecks(data.checks || {}); } catch {} };
    fetchHealth();
  }, []);
  const items = [
    { key: 'neo4j', label: 'Neo4j', icon: '🗄️' },
    { key: 'db', label: '数据库', icon: '💾' },
    { key: 'data_backend', label: '数据后端', icon: '🔗' },
    { key: 'notifications', label: '通知引擎', icon: '🔔' },
    { key: 'resources', label: '资源管理', icon: '⚡' },
    { key: 'uptime', label: '运行信息', icon: '⏱️' },
  ];
  const okCount = Object.values(checks).filter(v => v?.ok).length;
  const totalCount = Object.keys(checks).length || 7;

  return (
    <Card
      title={
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ fontSize: 15, fontWeight: 600 }}>组件状态</span>
          <Tag color={okCount === totalCount ? 'success' : 'warning'}>
            {okCount}/{totalCount} 正常
          </Tag>
        </div>
      }
      style={{ borderRadius: 12, boxShadow: '0 2px 10px rgba(0,0,0,0.05)' }}
    >
      <Row gutter={[12, 12]}>
        {items.map(({ key, label, icon }) => {
          const v = checks[key]; const ok = v?.ok;
          return (
            <Col span={8} key={key}>
              <div style={{
                display: 'flex', alignItems: 'center', gap: 10, padding: '14px 16px',
                borderRadius: 12, background: ok ? 'linear-gradient(135deg, #f6ffed, #f0fff0)' : v ? 'linear-gradient(135deg, #fff2f0, #fff1f0)' : '#fafafa',
                border: `1px solid ${ok ? '#d9f7be' : v ? '#ffccc7' : '#f0f0f0'}`,
                transition: 'all 0.3s',
              }}>
                <span style={{ fontSize: 26 }}>{icon}</span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 13, fontWeight: 500 }}>{label}</div>
                  <div style={{ fontSize: 11, color: '#888', marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {key === 'uptime' && v
                      ? `${Math.floor(v.uptime_s / 3600)}h ${Math.floor((v.uptime_s % 3600) / 60)}m / ${v.memory_mb}MB`
                      : key === 'mcp' && ok
                      ? `${(v.servers || []).length} 个服务器`
                      : ok ? v?.uri || v?.source || '正常' : v?.error || '连接失败'}
                  </div>
                </div>
                {ok ? <CheckCircleOutlined style={{ fontSize: 16, color: '#52c41a', flexShrink: 0 }} />
                  : v ? <CloseCircleOutlined style={{ fontSize: 16, color: '#ff4d4f', flexShrink: 0 }} />
                  : <Spin size="small" />}
              </div>
            </Col>
          );
        })}
      </Row>
    </Card>
  );
}
