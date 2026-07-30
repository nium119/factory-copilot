import React, { useState, useEffect } from 'react';
import { Spin, Empty, Card, Statistic, Row, Col, Tag } from 'antd';
import { CloudServerOutlined, ApiOutlined, ThunderboltOutlined, CheckCircleOutlined, CloseCircleOutlined } from '@ant-design/icons';

const RESOURCE_META = {
  low: { color: '#52c41a', text: '正常' },
  constrained: { color: '#faad14', text: '繁忙' },
  critical: { color: '#ff4d4f', text: '高负载' },
};

export default function ResourceStatusView() {
  const [state, setState] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchResources = async () => {
      try {
        const resp = await fetch('/api/system/resources');
        const data = await resp.json();
        setState(data);
      } catch {
        // ignore
      } finally {
        setLoading(false);
      }
    };
    fetchResources();
    const interval = setInterval(fetchResources, 30000);
    return () => clearInterval(interval);
  }, []);

  if (loading) return <div style={{ textAlign: 'center', padding: 60 }}><Spin /></div>;
  if (!state) return <Empty description="无法获取资源状态" style={{ padding: 60 }} />;

  const meta = RESOURCE_META[state.tier] || RESOURCE_META.low;

  return (
    <div style={{ padding: 24, height: '100%', overflow: 'auto', background: '#f5f5f7' }}>
      <div style={{ maxWidth: 800, margin: '0 auto' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 24 }}>
          <h2 style={{ margin: 0, fontSize: 18, fontWeight: 600 }}>系统资源状态</h2>
          <Tag color={meta.color} style={{ fontSize: 13, padding: '2px 12px' }}>{meta.text}</Tag>
        </div>

        <Row gutter={[16, 16]}>
          <Col span={6}>
            <Card size="small">
              <Statistic
                title="并发请求"
                value={state.concurrent_requests || 0}
                suffix={`/ ${state.max_concurrency || 0}`}
                prefix={<ThunderboltOutlined />}
                valueStyle={{ color: state.concurrent_requests >= (state.thresholds?.critical_at || 9) ? '#ff4d4f' : state.concurrent_requests >= (state.thresholds?.constrained_at || 6) ? '#faad14' : undefined }}
              />
            </Card>
          </Col>
          <Col span={6}>
            <Card size="small">
              <Statistic
                title="API调用/分"
                value={state.api_calls_per_minute || 0}
                suffix={`/ ${state.thresholds?.max_api_calls_per_minute || 30}`}
                prefix={<ApiOutlined />}
                valueStyle={{ color: state.api_calls_per_minute >= (state.thresholds?.max_api_calls_per_minute || 30) ? '#ff4d4f' : undefined }}
              />
            </Card>
          </Col>
          <Col span={6}>
            <Card size="small">
              <Statistic
                title="Token/时"
                value={state.token_usage_this_hour || 0}
                suffix={`/ ${state.thresholds?.token_budget_per_hour || 100000}`}
                prefix={<CloudServerOutlined />}
                valueStyle={{ color: state.token_usage_this_hour >= (state.thresholds?.token_budget_per_hour || 100000) ? '#ff4d4f' : undefined }}
              />
            </Card>
          </Col>
          <Col span={6}>
            <Card size="small">
              <Statistic
                title="模型等级"
                value={state.model_tier || '-'}
                prefix={<ThunderboltOutlined />}
                valueStyle={{ color: state.model_tier === 'qwen-turbo' ? '#faad14' : '#52c41a' }}
              />
            </Card>
          </Col>
        </Row>
        {state.tier !== 'optimal' && state.tier !== 'normal' && (
          <div style={{ marginTop: 12, padding: '8px 16px', background: '#fff2f0', border: '1px solid #ffccc7', borderRadius: 6, fontSize: 13, color: '#ff4d4f' }}>
            ⚠️ 触发原因：
            {state.concurrent_requests >= (state.thresholds?.critical_at || 9) ? ' 并发超限' : ''}
            {state.api_calls_per_minute >= (state.thresholds?.max_api_calls_per_minute || 30) ? ' API调用过频' : ''}
            {state.token_usage_this_hour >= (state.thresholds?.token_budget_per_hour || 100000) ? ' Token额度超限' : ''}
            {state.concurrent_requests >= (state.thresholds?.constrained_at || 6) && state.concurrent_requests < (state.thresholds?.critical_at || 9) ? ' 并发偏高' : ''}
            &nbsp;| 当前使用 {state.model_tier || 'budget'} 模型
          </div>
        )}

        <SystemHealthPanel />
        <Card title="详细指标" size="small" style={{ marginTop: 16 }}>
          <pre style={{ fontSize: 12, color: '#666', whiteSpace: 'pre-wrap', margin: 0 }}>
            {JSON.stringify(state, null, 2)}
          </pre>
        </Card>
      </div>
    </div>
  );
}

function SystemHealthPanel() {
  const [checks, setChecks] = useState({});
  useEffect(() => {
    const f = async () => { try { const r = await fetch('/api/system/health'); const d = await r.json(); setChecks(d.checks || {}); } catch {} };
    f(); const t = setInterval(f, 30000); return () => clearInterval(t);
  }, []);
  const lb = { neo4j:'Neo4j', ontology:'本体', db:'数据库', data_backend:'数据后端', notifications:'通知', resources:'资源' };
  return (
    <Card title="系统状态" size="small" style={{ marginTop:16 }}>
      <Row gutter={[8,8]}>
        {Object.entries(checks).map(([k,v]) => (
          <Col span={8} key={k}><Tag icon={v.ok ? <CheckCircleOutlined/> : <CloseCircleOutlined/>} color={v.ok?'green':'red'}>{lb[k]||k}{v.ok?' 正常':' 异常'}</Tag></Col>
        ))}
      </Row>
    </Card>
  );
}
