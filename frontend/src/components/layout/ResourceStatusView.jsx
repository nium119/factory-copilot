import React, { useState, useEffect, useMemo } from 'react';
import { Spin, Card, Row, Col, Tag } from 'antd';
import { CheckCircleOutlined, CloseCircleOutlined, ThunderboltOutlined, ApiOutlined, CloudServerOutlined } from '@ant-design/icons';
import ReactECharts from 'echarts-for-react';

const GAUGE_OPT = (value, max, color) => ({
  series: [{
    type: 'gauge', center: ['50%', '55%'], radius: '85%', startAngle: 210, endAngle: -30,
    min: 0, max, splitNumber: 6, progress: { show: true, width: 10, roundCap: true, itemStyle: { color } },
    axisLine: { lineStyle: { width: 10, color: [[1, '#f0f0f0']] } },
    axisTick: { show: false }, splitLine: { show: false }, axisLabel: { show: false },
    pointer: { show: false },
    detail: { valueAnimation: true, fontSize: 18, fontWeight: 'bold', offsetCenter: [0, '55%'],
      formatter: (v) => `{value|${v}}{unit|/${max}}`,
      rich: { value: { fontSize: 18, fontWeight: 700 }, unit: { fontSize: 12, color: '#999' } } },
    data: [{ value }],
  }],
});

export default function ResourceStatusView() {
  const [state, setState] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchResources = async () => {
      try {
        const r = await fetch(window.__API_BASE__ + '/system/resources');
        if (!r.ok) throw new Error(r.status);
        setState(await r.json());
      } catch (e) { /* ignore */ }
      setLoading(false);
    };
    fetchResources();
  }, []);

  const gaugeData = useMemo(() => {
    if (!state) return [];
    const c = state.concurrent_requests || 0; const cm = state.max_concurrency || 10;
    const a = state.api_calls_per_minute || 0; const am = state.thresholds?.max_api_calls_per_minute || 30;
    const tk = state.token_usage_this_hour || 0; const tkm = state.thresholds?.token_budget_per_hour || 500000;
    const color = (v,m,w,c) => v >= c ? '#ff4d4f' : v >= w ? '#faad14' : '#52c41a';
    return [
      { label:'并发请求', value:c, max:cm, pct:cm>0?Math.round(c/cm*100):0,
        color:color(c,cm,state.thresholds?.constrained_at||6,state.thresholds?.critical_at||9) },
      { label:'API调用/分', value:a, max:am, pct:am>0?Math.round(a/am*100):0,
        color:color(a,am,am,am) },
      { label:'Token/时', value:tk, max:tkm, pct:tkm>0?Math.round(tk/tkm*100):0,
        color:color(tk,tkm,tkm,tkm) },
    ];
  }, [state]);

  const tier = state?.tier || 'normal';
  const tierColor = tier === 'critical' ? '#ff4d4f' : tier === 'constrained' ? '#faad14' : '#52c41a';

  if (loading) return <div style={{ textAlign:'center', padding:80 }}><Spin size="large" /></div>;
  if (!state) return <div style={{ textAlign:'center', padding:80, color:'#999' }}>无法获取资源状态</div>;

  return (
    <div style={{ padding: 24, height: '100%', overflow: 'auto', background: 'linear-gradient(180deg, #f0f2f5 0%, #e8eaed 100%)' }}>
      <div style={{ maxWidth: 1100, margin: '0 auto' }}>

        {/* 状态横幅 */}
        <div style={{
          background: `linear-gradient(135deg, ${tierColor} 0%, ${tierColor}cc 50%, ${tierColor}88 100%)`,
          borderRadius: 16, padding: '24px 32px', marginBottom: 24,
          color: '#fff', display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          boxShadow: `0 8px 24px ${tierColor}44`,
        }}>
          <div>
            <div style={{ fontSize: 13, opacity: 0.8 }}>系统运行状态</div>
            <div style={{ fontSize: 32, fontWeight: 800, marginTop: 2 }}>
              {tier === 'critical' ? '严重' : tier === 'constrained' ? '繁忙' : '正常'}
            </div>
          </div>
          <div style={{ fontSize: 64, opacity: 0.15, fontWeight: 900 }}>
            {tier === 'critical' ? '!' : tier === 'constrained' ? '~' : '✓'}
          </div>
        </div>

        {/* 仪表盘 */}
        <Row gutter={[16,16]} style={{ marginBottom: 24 }}>
          {gaugeData.map((m, i) => (
            <Col span={8} key={i}>
              <Card size="small" style={{
                borderRadius: 14, border: 'none', boxShadow: '0 2px 12px rgba(0,0,0,0.06)',
                textAlign: 'center', paddingTop: 8,
              }}>
                <div style={{ fontSize: 13, fontWeight: 500, color: '#555', marginBottom: -10, position: 'relative', zIndex: 1 }}>
                  {m.label} <span style={{ fontSize: 22, color: m.color, marginLeft: 4 }}>{m.pct}%</span>
                </div>
                <ReactECharts option={GAUGE_OPT(m.value, m.max, m.color)} style={{ height: 160 }} />
              </Card>
            </Col>
          ))}
        </Row>

        <SystemHealthPanel />
      </div>
    </div>
  );
}

function SystemHealthPanel() {
  const [checks, setChecks] = useState({});
  useEffect(() => {
    const fetchHealth = async () => { try { const r = await fetch(window.__API_BASE__ + '/system/health'); setChecks((await r.json()).checks || {}); } catch {} };
    fetchHealth();
  }, []);
  const items = [
    { key:'neo4j', label:'Neo4j', icon:'🗄️' },
    { key:'db', label:'数据库', icon:'💾' },
    { key:'data_backend', label:'数据后端', icon:'🔗' },
    { key:'notifications', label:'通知引擎', icon:'🔔' },
    { key:'resources', label:'资源管理', icon:'⚡' },
    { key:'uptime', label:'运行信息', icon:'⏱️' },
  ];
  const okCount = Object.values(checks).filter(v => v?.ok).length;
  const totalCount = Object.keys(checks).length || 7;

  return (
    <Card
      title={
        <div style={{ display:'flex', alignItems:'center', gap:10 }}>
          <span style={{ fontSize:15, fontWeight:600 }}>组件状态</span>
          <Tag color={okCount === totalCount ? 'success' : 'warning'}>
            {okCount}/{totalCount} 正常
          </Tag>
        </div>
      }
      style={{ borderRadius: 14, border: 'none', boxShadow: '0 2px 12px rgba(0,0,0,0.06)' }}
    >
      <Row gutter={[12,12]}>
        {items.map(({key,label,icon}) => {
          const v = checks[key]; const ok = v?.ok;
          return (
            <Col span={8} key={key}>
              <div style={{
                display:'flex', alignItems:'center', gap:10, padding:'14px 16px',
                borderRadius:12, background: ok ? 'linear-gradient(135deg, #f6ffed, #f0fff0)' : v ? 'linear-gradient(135deg, #fff2f0, #fff1f0)' : '#fafafa',
                border:`1px solid ${ok ? '#d9f7be' : v ? '#ffccc7' : '#f0f0f0'}`,
                transition: 'all 0.3s',
              }}>
                <span style={{fontSize:26}}>{icon}</span>
                <div style={{flex:1,minWidth:0}}>
                  <div style={{fontSize:13,fontWeight:500}}>{label}</div>
                  <div style={{fontSize:11,color:'#888',marginTop:2,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>
                    {key === 'uptime' && v
                      ? `${Math.floor(v.uptime_s / 3600)}h ${Math.floor((v.uptime_s % 3600) / 60)}m / ${v.memory_mb}MB`
                      : key === 'mcp' && ok
                      ? `${(v.servers || []).length} 个服务器`
                      : ok ? v?.uri || v?.source || '正常' : v?.error || '连接失败'}
                  </div>
                </div>
                {ok ? <CheckCircleOutlined style={{fontSize:16,color:'#52c41a',flexShrink:0}} />
                  : v ? <CloseCircleOutlined style={{fontSize:16,color:'#ff4d4f',flexShrink:0}} />
                  : <Spin size="small" />}
              </div>
            </Col>
          );
        })}
      </Row>
    </Card>
  );
}

