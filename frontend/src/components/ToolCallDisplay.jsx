import React from 'react';
import { Card, Typography, Tag, Space, Divider } from 'antd';
import { SearchOutlined, ToolOutlined, CheckCircleOutlined, LoadingOutlined } from '@ant-design/icons';

const { Text, Paragraph, Title } = Typography;

function ToolCallDisplay({ toolCall }) {
  if (!toolCall) return null;

  const { tool, status, input, output } = toolCall;

  return (
    <Card
      size="small"
      style={{
        marginBottom: '12px',
        backgroundColor: '#f5f5f5',
        border: '1px solid #d9d9d9',
      }}
    >
      <Space direction="vertical" style={{ width: '100%' }}>
        {/* 工具头部 */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <ToolOutlined style={{ color: '#1890ff' }} />
          <Text strong>工具调用</Text>
          <Tag color={status === 'running' ? 'processing' : status === 'success' ? 'success' : 'error'}>
            {status === 'running' ? '执行中' : status === 'success' ? '成功' : '失败'}
          </Tag>
          {status === 'running' && <LoadingOutlined spin />}
          {status === 'success' && <CheckCircleOutlined style={{ color: '#52c41a' }} />}
        </div>

        <Divider style={{ margin: '8px 0' }} />

        {/* 工具名称 */}
        <div>
          <Text type="secondary">工具: </Text>
          <Tag icon={<SearchOutlined />} color="blue">
            {tool === 'search' ? '外网搜索' : tool}
          </Tag>
        </div>

        {/* 输入参数 */}
        {input && (
          <div>
            <Text type="secondary">查询: </Text>
            <Text code>{input}</Text>
          </div>
        )}

        {/* 输出结果 */}
        {output && status === 'success' && (
          <div>
            <Text type="secondary">结果:</Text>
            <Paragraph
              style={{
                marginTop: '8px',
                marginBottom: 0,
                padding: '8px',
                backgroundColor: '#fff',
                borderRadius: '4px',
                whiteSpace: 'pre-wrap',
              }}
            >
              {output}
            </Paragraph>
          </div>
        )}
      </Space>
    </Card>
  );
}

export default ToolCallDisplay;
