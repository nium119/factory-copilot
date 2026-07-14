import React, { useState, useEffect } from 'react';
import { Spin, Empty, Card, Statistic, Row, Col, Tag } from 'antd';
import { CloudServerOutlined, ApiOutlined, ThunderboltOutlined } from '@ant-design/icons';

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
          <Col span={8}>
            <Card size="small">
              <Statistic
                title="并发请求"
                value={state.concurrent_requests || 0}
                suffix={`/ ${state.max_concurrency || 0}`}
                prefix={<ThunderboltOutlined />}
              />
            </Card>
          </Col>
          <Col span={8}>
            <Card size="small">
              <Statistic
                title="活跃连接"
                value={state.active_connections || 0}
                prefix={<ApiOutlined />}
              />
            </Card>
          </Col>
          <Col span={8}>
            <Card size="small">
              <Statistic
                title="CPU 使用率"
                value={state.cpu_percent || 0}
                suffix="%"
                prefix={<CloudServerOutlined />}
              />
            </Card>
          </Col>
        </Row>

        <Card title="详细指标" size="small" style={{ marginTop: 16 }}>
          <pre style={{ fontSize: 12, color: '#666', whiteSpace: 'pre-wrap', margin: 0 }}>
            {JSON.stringify(state, null, 2)}
          </pre>
        </Card>
      </div>
    </div>
  );
}
