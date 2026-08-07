import React, { useState, useEffect, useRef } from 'react';
import { Spin, Statistic, Row, Col, Card, Table, Tag, Select } from 'antd';
import { BarChartOutlined, ThunderboltOutlined, RobotOutlined, SyncOutlined } from '@ant-design/icons';
import * as echarts from 'echarts';
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

  // 日均查询趋势（ECharts 柱+线）
  const trendRef = useRef(null);
  const trendChart = useRef(null);
  useEffect(() => {
    if (!data || !trendRef.current) return;
    if (!trendChart.current) trendChart.current = echarts.init(trendRef.current);
    const trend = data.dailyTrend || [];
    const maxCnt = Math.max(...trend.map(d => d.count), 1);
    trendChart.current.setOption({
      grid: { left: 40, right: 16, top: 24, bottom: 24 },
      tooltip: {
        trigger: 'axis',
        formatter: (params) => {
          const p = params[0];
          const i = p.dataIndex;
          const mom = i > 0 && trend[i-1].count ? Math.round((p.value - trend[i-1].count) / trend[i-1].count * 100) : 0;
          return `<b>${trend[i].date}</b><br/>查询: <b>${p.value}</b> 次<br/>${mom>=0?'↑':'↓'} 较前日 ${Math.abs(mom)}%`;
        },
      },
      xAxis: { type: 'category', data: trend.map(d => d.date.slice(5)), axisLabel: { fontSize: 10, rotate: 30 } },
      yAxis: { type: 'value', minInterval: 1 },
      series: [
        {
          type: 'bar', data: trend.map(d => d.count), barMaxWidth: 26,
          label: { show: true, position: 'top', fontSize: 10, color: '#666' },
          itemStyle: { borderRadius: [3, 3, 0, 0], color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: '#6c5ce7' }, { offset: 1, color: '#a29bfe' },
          ]) },
        },
        {
          type: 'line', data: trend.map(d => d.count), smooth: true, symbolSize: 5,
          lineStyle: { color: '#fd79a8', width: 2 }, itemStyle: { color: '#fd79a8' },
        },
      ],
    });
  }, [data]);
  useEffect(() => () => {
    trendChart.current?.dispose(); trendChart.current = null;
    methodChart.current?.dispose(); methodChart.current = null;
    conceptChart.current?.dispose(); conceptChart.current = null;
  }, []);

  // 路由方式分布（环形图）
  const methodRef = useRef(null);
  const methodChart = useRef(null);
  // 高频概念 Top 10（横向条形图）
  const conceptRef = useRef(null);
  const conceptChart = useRef(null);
  useEffect(() => {
    if (!data) return;
    // 路由方式环形图
    if (methodRef.current) {
      if (!methodChart.current) methodChart.current = echarts.init(methodRef.current);
      methodChart.current.setOption({
        tooltip: { trigger: 'item', formatter: '{b}: {c} 次 ({d}%)' },
        legend: { bottom: 0, textStyle: { fontSize: 10 } },
        series: [{
          type: 'pie', radius: ['45%', '68%'], center: ['50%', '42%'],
          itemStyle: { borderRadius: 4 },
          label: { show: false },
          data: Object.entries(data.methodDistribution || {}).map(([m, cnt]) => ({
            name: (methodMap[m] || {}).label || m, value: cnt,
          })),
        }],
      });
    }
    // 高频概念横向条形图
    if (conceptRef.current) {
      if (!conceptChart.current) conceptChart.current = echarts.init(conceptRef.current);
      const top = data.topConcepts || [];
      conceptChart.current.setOption({
        grid: { left: 100, right: 40, top: 8, bottom: 24 },
        tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
        xAxis: { type: 'value', minInterval: 1 },
        yAxis: { type: 'category', data: top.map(t => cn(t.concept)).reverse(), axisLabel: { fontSize: 10 } },
        series: [{
          type: 'bar', data: top.map(t => t.count).reverse(), barWidth: 13,
          itemStyle: { borderRadius: [0, 3, 3, 0], color: '#00b894' },
          label: { show: true, position: 'right', fontSize: 10, color: '#666' },
        }],
      });
    }
  }, [data]);

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
          <Card size="small" style={{height:90}}><Statistic title="总查询" value={data.total} suffix="次" /></Card>
        </Col>
        <Col span={6}>
          <Card size="small" style={{height:90}}><Statistic title="触发词命中" value={data.triggerRate} suffix="%" precision={1} /></Card>
        </Col>
        <Col span={6}>
          <Card size="small" style={{height:90}}><Statistic title="智能分析兜底率" value={data.dynamicRate} suffix="%" precision={1} /></Card>
        </Col>
        <Col span={6}>
          <Card size="small" style={{height:90}}><Statistic title="追问率" value={data.followupRate} suffix="%" precision={1} /></Card>
        </Col>
      </Row>

      {rag && (
        <Row gutter={16} style={{ marginBottom: 16 }}>
          <Col span={6}>
            <Card size="small" style={{height:90}}><Statistic title="RAG 命中率" value={rag.total > 0 ? Math.round(rag.hit / rag.total * 100) : 0} suffix="%" precision={0} /></Card>
          </Col>
          <Col span={6}>
            <Card size="small" style={{height:90}}><Statistic title="平均相似度" value={rag.total > 0 ? rag.avg_max_sim?.toFixed(2) : '-'} /></Card>
          </Col>
          <Col span={6}>
            <Card size="small" style={{height:90}}><Statistic title="退回全量LLM" value={rag.miss + rag.fallback} suffix="次" /></Card>
          </Col>
          <Col span={6}>
            <Card size="small" style={{ height: 90 }}>
              <div style={{ fontSize: 12, color: '#999', marginBottom: 4 }}>检索方式</div>
              {rag.mode ? (
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, fontSize: 13 }}>
                  {Object.entries(rag.mode).map(([m, cnt]) => {
                    const c = {hybrid:{color:'#722ed1',label:'向量+关键词'},vec:{color:'#1677ff',label:'纯向量'},bm25:{color:'#52c41a',label:'纯关键词'},fallback:{color:'#ff4d4f',label:'全量LLM'}}[m]||{color:'#999',label:m};
                    return <Tag key={m} color={c.color} style={{margin:0,fontSize:12}}>{c.label} {cnt}</Tag>;
                  })}
                </div>
              ) : <span style={{color:'#ccc'}}>-</span>}
            </Card>
          </Col>
        </Row>
      )}

      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={12}>
          <Card size="small" title="路由方式分布" style={{ height: 280 }} styles={{ body: { padding: 4 } }}>
            <div ref={methodRef} style={{ height: 210 }} />
          </Card>
        </Col>
        <Col span={12}>
          <Card size="small" title="高频概念 Top 10" style={{ height: 280 }} styles={{ body: { padding: 4 } }}>
            <div ref={conceptRef} style={{ height: 210 }} />
          </Card>
        </Col>
      </Row>

      <Card size="small" title={`日均查询趋势 (${data.days}天)`}
        extra={(() => {
          const trend = data.dailyTrend || [];
          const avg = trend.length ? Math.round(data.total / data.days) : 0;
          const peak = trend.reduce((a, b) => (b.count > a.count ? b : a), { count: 0, date: '' });
          const last = trend[trend.length - 1]?.count ?? 0;
          const prev = trend[trend.length - 2]?.count ?? 0;
          const mom = prev ? Math.round((last - prev) / prev * 100) : 0;
          return (
            <span style={{ fontSize: 12, color: '#888', fontWeight: 400 }}>
              日均 <b style={{ color: '#6c5ce7' }}>{avg}</b> ·
              峰值 <b>{peak.count}</b>（{peak.date.slice(5)}）·
              环比 <b style={{ color: mom >= 0 ? '#52c41a' : '#ff4d4f' }}>{mom >= 0 ? '↑' : '↓'}{Math.abs(mom)}%</b> ·
              平均耗时 <b>{data.avgMs}ms</b>
            </span>
          );
        })()}
      >
        <div ref={trendRef} style={{ height: 200 }} />
      </Card>
    </div>
  );
}
