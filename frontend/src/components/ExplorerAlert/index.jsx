/**
 * 探索者异常预警面板
 *
 * 通过定时轮询后端 GET /api/explorer/analyze 接口，
 * 发现生产数据异常时主动推送预警通知。
 *
 * 使用场景：
 * - AgentSidebar 中的预警按钮（带 Badge）
 * - 点击打开 Drawer 显示异常卡片列表
 *
 * Props:
 *   anomalies    array  异常列表 [{severity, title, description, suggestion}]
 *   visible      bool    Drawer 是否可见
 *   onClose      func    关闭回调
 */
import React from 'react';
import { Drawer, Badge, Card, Tag, Empty, Button } from 'antd';
import { BellOutlined, WarningOutlined, ExclamationCircleOutlined, InfoCircleOutlined } from '@ant-design/icons';

const SEVERITY_ICONS = {
  high: <WarningOutlined style={{ color: '#ff4d4f' }} />,
  medium: <ExclamationCircleOutlined style={{ color: '#faad14' }} />,
  low: <InfoCircleOutlined style={{ color: '#1890ff' }} />,
};

const SEVERITY_TAGS = {
  high: { color: 'red', label: '严重' },
  medium: { color: 'orange', label: '警告' },
  low: { color: 'blue', label: '提示' },
};

function ExplorerAlertDrawer({ anomalies, visible, onClose }) {
  const highCount = (anomalies || []).filter(a => a.severity === 'high').length;
  const mediumCount = (anomalies || []).filter(a => a.severity === 'medium').length;

  return (
    <Drawer
      title="异常预警"
      placement="right"
      width={420}
      open={visible}
      onClose={onClose}
    >
      {(!anomalies || anomalies.length === 0) ? (
        <Empty description="暂无异常预警" />
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
          {anomalies.map((item, idx) => {
            const sev = SEVERITY_TAGS[item.severity] || SEVERITY_TAGS.low;
            const icon = SEVERITY_ICONS[item.severity] || SEVERITY_ICONS.low;
            return (
              <Card
                key={idx}
                size="small"
                style={{
                  borderLeft: `4px solid ${
                    item.severity === 'high' ? '#ff4d4f' :
                    item.severity === 'medium' ? '#faad14' : '#1890ff'
                  }`,
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '8px' }}>
                  {icon}
                  <strong style={{ fontSize: '14px' }}>{item.title}</strong>
                  <Tag color={sev.color}>{sev.label}</Tag>
                </div>
                <div style={{ fontSize: '13px', color: '#666', marginBottom: '8px', lineHeight: '1.6' }}>
                  {item.description}
                </div>
                {item.suggestion && (
                  <div style={{
                    background: '#f8f7ff',
                    borderRadius: '6px',
                    padding: '6px 10px',
                    fontSize: '12px',
                    color: '#6c5ce7',
                  }}>
                    建议：{item.suggestion}
                  </div>
                )}
              </Card>
            );
          })}
        </div>
      )}

      {/* 统计摘要 */}
      {(highCount > 0 || mediumCount > 0) && (
        <div style={{
          marginTop: '16px',
          padding: '10px 12px',
          background: '#fafafa',
          borderRadius: '8px',
          fontSize: '12px',
          color: '#666',
        }}>
          <div style={{ marginBottom: '4px' }}>
            <WarningOutlined style={{ color: '#ff4d4f' }} /> 严重异常：{highCount} 项
          </div>
          <div>
            <ExclamationCircleOutlined style={{ color: '#faad14' }} /> 警告：{mediumCount} 项
          </div>
        </div>
      )}
    </Drawer>
  );
}

// 预警按钮组件（用于 AgentSidebar）
export function ExplorerAlertButton({ count, onClick }) {
  return (
    <Badge count={count} offset={[-2, 4]}>
      <Button
        type="text"
        size="small"
        icon={<BellOutlined />}
        onClick={onClick}
        style={{
          width: '100%',
          textAlign: 'left',
          paddingLeft: '12px',
          justifyContent: 'flex-start',
          color: count > 0 ? '#ff4d4f' : '#999',
        }}
      >
        异常预警
      </Button>
    </Badge>
  );
}

export default ExplorerAlertDrawer;
