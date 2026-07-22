import React, { useState, useEffect } from 'react';
import { Spin, Statistic, Row, Col, Card, Table, Tag, Select } from 'antd';
import { BarChartOutlined, ThunderboltOutlined, RobotOutlined, SyncOutlined } from '@ant-design/icons';
import request from '../../services/request';

export default function StatsTab() {
  const [data, setData] = useState(null);
  const [rag, setRag] = useState(null);
  const [cm, setCm] = useState({});
  const [days, setDays] = useState(7);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    request.get('/chains/compile/status').then(d => {
      if (d.concept_map) setCm(d.concept_map);
    }).catch(() => {});
    request.get('/system/rag-stats').then(d => {
      if (d.ok) setRag(d.data);
    }).catch(() => {});
  }, []);

  useEffect(() => {
    setLoading(true);
    request.get(`/chains/api-logs/stats?days=${days}`)
      .then(d => { if (d.ok) setData(d); })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [days]);

  const cn = (concept) => (cm[concept] || {}).label || concept;

  if (loading) return <Spin style={{ display: 'block', margin: '60px auto' }} />;
  if (!data || data.total === 0) return <div style={{ padding: 40, textAlign: 'center', color: '#999' }}>暂无数据，开始使用后会累积统计</div>;

  const methodMap = {
    trigger: { label: '触发词直达', color: 'green', icon: <ThunderboltOutlined /> },
    rag_llm: { label: 'RAG+LLM', color: 'blue', icon: <RobotOutlined /> },
    llm: { label: 'LLM分类', color: 'purple', icon: <RobotOutlined /> },
    dynamic: { label: '智能分析', color: 'orange', icon: <SyncOutlined /> },
  };

  return (
    <div key={days} style={{ padding: 20 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <span style={{ fontSize: 16, fontWeight: 600 }}>📊 行为数据</span>
        <Select size="small" value={days} onChange={setDays} style={{ width: 120 }}
          options={[{ value: 7, label: '近7天' }, { value: 30, label: '近30天' }, { value: 90, label: '近90天' }]}
        />
      </div>

      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={6}>
          <Card size="small"><Statistic title="总查询" value={data.total} suffix="次" /></Card>
        </Col>
        <Col span={6}>
          <Card size="small"><Statistic title="触发词命中" value={data.triggerRate} suffix="%" precision={1} /></Card>
        </Col>
        <Col span={6}>
          <Card size="small"><Statistic title="智能分析兜底率" value={data.dynamicRate} suffix="%" precision={1} /></Card>
        </Col>
        <Col span={6}>
          <Card size="small"><Statistic title="追问率" value={data.followupRate} suffix="%" precision={1} /></Card>
        </Col>
      </Row>

      {rag && rag.total > 0 && (
        <>
          <Row gutter={8} style={{ marginBottom: 4 }}>
            <Col span={8}>
              <Card size="small"><Statistic title="RAG 召回" value={rag.total > 0 ? Math.round(rag.hit / rag.total * 100) : 0} suffix="%" precision={0} /></Card>
            </Col>
            <Col span={8}>
              <Card size="small"><Statistic title="均相似度" value={rag.avg_max_sim?.toFixed(2) || '-'} precision={2} /></Card>
            </Col>
            <Col span={8}>
              <Card size="small"><Statistic title="退回LLM" value={rag.miss + rag.fallback} suffix="次" /></Card>
            </Col>
          </Row>
          {rag.mode && Object.keys(rag.mode).length > 1 && (
            <div style={{ marginBottom: 8, fontSize: 11, color: '#888' }}>
              {Object.entries(rag.mode).map(([m, cnt]) => (
                <Tag key={m} color={m==='hybrid'?'purple':m==='vec'?'blue':m==='bm25'?'green':'red'} style={{fontSize:10}}>
                  {m==='hybrid'?'向量+BM25':m==='vec'?'纯向量':m==='bm25'?'纯BM25':'LLM'}:{cnt}次
                </Tag>
              ))}
            </div>
          )}
        </>
      )}

      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={12}>
          <Card size="small" title="路由方式分布" style={{ height: 280 }} bodyStyle={{ overflow: 'auto', maxHeight: 230 }}>
            {(data.methodDistribution ? Object.entries(data.methodDistribution) : []).map(([m, cnt]) => (
              <div key={m} style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
                <span>
                  <Tag color={(methodMap[m] || {}).color || 'default'}>{(methodMap[m] || {}).label || m}</Tag>
                </span>
                <span style={{ fontWeight: 500 }}>{cnt} 次 ({Math.round(cnt / data.total * 100)}%)</span>
              </div>
            ))}
          </Card>
        </Col>
        <Col span={12}>
          <Card size="small" title="高频概念 Top 10" style={{ height: 280 }} bodyStyle={{ overflow: 'auto', maxHeight: 230 }}>
            <Table size="small" dataSource={data.topConcepts || []} rowKey="concept" pagination={false}
              columns={[
                { title: '概念', dataIndex: 'concept', render: v => <span style={{ fontSize: 12 }}>{cn(v)}</span> },
                { title: '次数', dataIndex: 'count', width: 80, render: v => <b>{v}</b> },
                { title: '占比', dataIndex: 'count', width: 60, render: v => `${Math.round(v / data.total * 100)}%` },
              ]}
            />
          </Card>
        </Col>
      </Row>

      <Card size="small" title={`日均查询趋势 (${data.days}天)`}>
        <div style={{ display: 'flex', alignItems: 'flex-end', gap: 4, height: 120 }}>
          {(data.dailyTrend || []).map(d => {
            const maxCnt = Math.max(...(data.dailyTrend || []).map(x => x.count), 1);
            const h = Math.max(2, (d.count / maxCnt) * 100);
            return (
              <div key={d.date} style={{ flex: 1, textAlign: 'center' }}>
                <div style={{ height: 100, display: 'flex', alignItems: 'flex-end', justifyContent: 'center' }}>
                  <div style={{ width: '80%', height: h + '%', background: '#6c5ce7', borderRadius: '3px 3px 0 0', minHeight: 2 }} title={`${d.date}: ${d.count}`} />
                </div>
                <div style={{ fontSize: 9, color: '#999', marginTop: 4, transform: 'rotate(-30deg)', transformOrigin: 'left top' }}>{d.date.slice(5)}</div>
              </div>
            );
          })}
        </div>
      </Card>
    </div>
  );
}
