import React, { useState, useEffect } from 'react';
import { Spin, Card, Row, Col, Tag, Progress } from 'antd';
import { CheckCircleOutlined, CloseCircleOutlined, CloudServerOutlined, ApiOutlined, ThunderboltOutlined } from '@ant-design/icons';

export default function ResourceStatusView() {
  const [state, setState] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetch = async () => {
      try {
        const r = await fetch('/api/system/resources'); const d = await r.json(); setState(d);
      } catch {}
      setLoading(false);
    };
    fetch();
    const t = setInterval(fetch, 30000);
    return () => clearInterval(t);
  }, []);

  if (loading) return <div style={{ textAlign:'center', padding:60 }}><Spin size="large" /></div>;
  if (!state) return <div style={{ textAlign:'center', padding:60,color:'#999' }}>无法获取资源状态</div>;

  const tier = state.tier || 'normal';
  const tierColor = tier === 'critical' ? '#ff4d4f' : tier === 'constrained' ? '#faad14' : '#52c41a';
  const tierLabel = tier === 'critical' ? '严重' : tier === 'constrained' ? '繁忙' : '正常';

  const metrics = [
    { label:'并发请求', value: state.concurrent_requests || 0, max: state.max_concurrency || 10, icon:<ThunderboltOutlined />, color:'#6c5ce7',
      warnAt: state.thresholds?.constrained_at || 6, critAt: state.thresholds?.critical_at || 9 },
    { label:'API调用/分', value: state.api_calls_per_minute || 0, max: state.thresholds?.max_api_calls_per_minute || 30, icon:<ApiOutlined />, color:'#1890ff',
      warnAt: state.thresholds?.max_api_calls_per_minute || 30, critAt: 999 },
    { label:'Token/时', value: state.token_usage_this_hour || 0, max: state.thresholds?.token_budget_per_hour || 500000, icon:<CloudServerOutlined />, color:'#00b894',
      warnAt: state.thresholds?.token_budget_per_hour || 500000, critAt: 999 },
  ];

  return (
    <div style={{ padding: 24, height: '100%', overflow: 'auto', background: '#f0f2f5' }}>
      <div style={{ maxWidth: 960, margin: '0 auto' }}>
        {/* 总体状态横幅 */}
        <div style={{
          background: `linear-gradient(135deg, ${tierColor} 0%, ${tierColor}dd 100%)`,
          borderRadius: 12, padding: '20px 28px', marginBottom: 24,
          color: '#fff', display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        }}>
          <div>
            <div style={{ fontSize: 14, opacity: 0.85 }}>系统运行状态</div>
            <div style={{ fontSize: 28, fontWeight: 700, marginTop: 4 }}>{tierLabel}</div>
          </div>
          <div style={{ textAlign: 'right', fontSize: 13, opacity: 0.85 }}>
            <div>模型: {state.model_tier || 'qwen-plus'}</div>
            <div>最大并发: {state.max_concurrency || 10}</div>
          </div>
        </div>

        {/* 资源指标 */}
        <Row gutter={[16,16]} style={{ marginBottom: 24 }}>
          {metrics.map(m => {
            const pct = m.max > 0 ? Math.round((m.value / m.max) * 100) : 0;
            const status = m.value >= m.critAt ? 'exception' : m.value >= m.warnAt ? 'active' : 'normal';
            return (
              <Col span={8} key={m.label}>
                <Card size="small" style={{ borderRadius: 10, border: 'none', boxShadow: '0 2px 8px rgba(0,0,0,0.06)' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                    <span style={{ fontSize: 20, color: m.color }}>{m.icon}</span>
                    <span style={{ fontSize: 13, color: '#888' }}>{m.label}</span>
                  </div>
                  <div style={{ fontSize: 28, fontWeight: 700, marginBottom: 4 }}>{m.value}<span style={{ fontSize: 14, fontWeight: 400, color: '#888' }}>/{m.max}</span></div>
                  <Progress percent={pct} showInfo={false} strokeColor={status === 'exception' ? '#ff4d4f' : status === 'active' ? '#faad14' : m.color} size="small" />
                </Card>
              </Col>
            );
          })}
        </Row>

        <SystemHealthPanel />
      </div>
    </div>
  );
}

function SystemHealthPanel() {
  const [checks, setChecks] = useState({});
  useEffect(() => {
    const f = async () => { try { const r = await fetch('/api/system/health'); setChecks((await r.json()).checks || {}); } catch {} };
    f(); const t = setInterval(f, 30000); return () => clearInterval(t);
  }, []);
  const items = [
    { key:'neo4j', label:'Neo4j', icon:'🗄️' },
    { key:'ontology', label:'本体加载', icon:'📐' },
    { key:'db', label:'数据库', icon:'💾' },
    { key:'data_backend', label:'数据后端', icon:'🔗' },
    { key:'notifications', label:'通知引擎', icon:'🔔' },
    { key:'resources', label:'资源管理', icon:'⚡' },
  ];
  return (
    <Card title={<span style={{fontSize:15,fontWeight:600}}>组件状态</span>} style={{ borderRadius: 10, border: 'none', boxShadow: '0 2px 8px rgba(0,0,0,0.06)' }}>
      <Row gutter={[12,12]}>
        {items.map(({key,label,icon}) => {
          const v = checks[key]; const ok = v?.ok;
          return (
            <Col span={12} key={key}>
              <div style={{
                display:'flex', alignItems:'center', gap:12, padding:'12px 16px',
                borderRadius:10, background: ok ? '#f6ffed' : v ? '#fff2f0' : '#fafafa',
                border:`1px solid ${ok ? '#b7eb8f' : v ? '#ffccc7' : '#f0f0f0'}`,
              }}>
                <span style={{fontSize:24}}>{icon}</span>
                <div style={{flex:1}}>
                  <div style={{fontSize:14,fontWeight:500}}>{label}</div>
                  <div style={{fontSize:12,marginTop:2}}>
                    {ok ? <Tag color="success" style={{margin:0}}>正常</Tag>
                      : v ? <Tag color="error" style={{margin:0}}>异常</Tag>
                      : <Tag style={{margin:0}}>检测中</Tag>}
                  </div>
                </div>
                {ok ? <CheckCircleOutlined style={{fontSize:18,color:'#52c41a'}} />
                  : v ? <CloseCircleOutlined style={{fontSize:18,color:'#ff4d4f'}} />
                  : <Spin size="small" />}
              </div>
            </Col>
          );
        })}
      </Row>
    </Card>
  );
}
