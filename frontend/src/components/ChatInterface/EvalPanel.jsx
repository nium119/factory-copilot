/**
 * 排产优化评估雷达图
 *
 * 使用 ECharts 五维雷达图展示评估维度：
 * 产线平衡率、设备利用率、交期达成率、换线次数、在制品数量。
 *
 * Props:
 *   evalResult   object  {scores, overall_score, needs_optimization, suggestions}
 */
import React, { useRef, useEffect, useState } from 'react';
import { Tag, Spin } from 'antd';
import { ThunderboltOutlined } from '@ant-design/icons';

function EvalPanel({ evalResult }) {
  const chartRef = useRef(null);
  const echartsLoaded = useRef(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!evalResult?.scores) return;

    // 懒加载 ECharts (ECharts 6: module = echarts directly, no .default)
    if (!echartsLoaded.current) {
      import('echarts').then((echarts) => {
        echartsLoaded.current = true;
        renderChart(echarts);
      }).catch(() => {
        setLoading(false);
      });
    } else {
      import('echarts').then((echarts) => {
        renderChart(echarts);
      });
    }
  }, [evalResult]);

  const renderChart = (echarts) => {
    if (!chartRef.current || !evalResult?.scores) {
      setLoading(false);
      return;
    }

    setLoading(false);

    const chart = echarts.init(chartRef.current);

    const dimensions = Object.keys(evalResult.scores);
    const values = Object.values(evalResult.scores);
    const maxScore = 5;

    const option = {
      title: {
        text: '排产优化评估',
        left: 'center',
        textStyle: { fontSize: '14px', fontWeight: 500, color: '#333' },
      },
      radar: {
        indicator: dimensions.map(d => ({ name: d, max: maxScore })),
        radius: '65%',
        center: ['50%', '55%'],
        axisName: { fontSize: '11px', color: '#666' },
        splitLine: { lineStyle: { color: '#e8e8e8' } },
        splitArea: { areaStyle: { color: ['#f8f8f8', '#fff'] } },
      },
      series: [{
        type: 'radar',
        data: [{
          value: values,
          name: '评分',
          areaStyle: { color: 'rgba(108, 92, 231, 0.2)' },
          lineStyle: { color: '#6c5ce7', width: 2 },
          itemStyle: { color: '#6c5ce7' },
        }],
      }],
      graphic: [{
        type: 'text',
        left: 'center',
        bottom: '10%',
        style: {
          text: `综合评分: ${evalResult.overall_score?.toFixed(1) || 'N/A'} / ${maxScore}`,
          fill: evalResult.needs_optimization ? '#faad14' : '#52c413',
          fontSize: '13px',
          fontWeight: 600,
        },
      }],
    };

    chart.setOption(option);

    // 响应窗口大小变化
    const handleResize = () => chart.resize();
    window.addEventListener('resize', handleResize);

    return () => {
      window.removeEventListener('resize', handleResize);
      chart.dispose();
    };
  };

  if (!evalResult?.scores) return null;

  return (
    <div style={{
      background: '#f8f7ff',
      border: '1px solid rgba(108, 92, 231, 0.12)',
      borderRadius: '10px',
      marginBottom: '8px',
      padding: '12px 16px',
      width: '100%',
    }}>
      <div style={{
        display: 'flex',
        alignItems: 'center',
        gap: '6px',
        marginBottom: '8px',
        fontSize: '13px',
        fontWeight: 500,
        color: '#6c5ce7',
      }}>
        <ThunderboltOutlined style={{ fontSize: '14px' }} />
        <span>排产优化评估</span>
        {evalResult.needs_optimization && (
          <Tag color="orange" style={{ marginLeft: '8px', fontSize: '11px' }}>需优化</Tag>
        )}
      </div>

      {loading && (
        <div style={{ textAlign: 'center', padding: '20px' }}>
          <Spin size="small" />
        </div>
      )}
      <div ref={chartRef} style={{ height: '280px', display: loading ? 'none' : 'block' }} />

      {/* 建议列表 */}
      {evalResult.suggestions && evalResult.suggestions.length > 0 && (
        <div style={{
          marginTop: '12px',
          padding: '8px 12px',
          background: '#fff',
          borderRadius: '8px',
          border: '1px solid rgba(108, 92, 231, 0.08)',
        }}>
          <div style={{
            fontSize: '12px',
            fontWeight: 500,
            color: '#6c5ce7',
            marginBottom: '6px',
          }}>优化建议：</div>
          {evalResult.suggestions.map((s, idx) => (
            <div key={idx} style={{
              fontSize: '12px',
              color: '#555',
              lineHeight: '1.8',
              paddingLeft: '12px',
              position: 'relative',
            }}>
              <span style={{
                position: 'absolute',
                left: '0',
                color: '#6c5ce7',
              }}>•</span>
              {s}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default EvalPanel;
