import React, { useState, useEffect } from 'react';
import { Form, Select, Button, message, Spin, Card } from 'antd';
import { SaveOutlined } from '@ant-design/icons';
import request from '../../services/request';

export default function ModelConfigTab() {
  const [config, setConfig] = useState(null);
  const [models, setModels] = useState([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm();

  useEffect(() => {
    setLoading(true);
    request.get('/config/models').then(d => {
      if (d.ok) {
        setConfig(d.config);
        setModels(d.available || []);
        form.setFieldsValue(d.config);
      }
    }).catch(() => message.error('加载失败'))
    .finally(() => setLoading(false));
  }, [form]);

  const handleSave = async () => {
    try {
      const vals = await form.validateFields(); setSaving(true);
      await request.put('/config/models', { config: vals });
      message.success('已保存，即时生效');
    } catch (err) {
      if (err?.errorFields) return;
      message.error('保存失败');
    } finally { setSaving(false); }
  };

  if (loading) return <Spin style={{ display: 'block', margin: '60px auto' }} />;

  return (
    <div style={{ maxWidth: 500 }}>
      <Card size="small" style={{ marginBottom: 16, background: '#f6ffed', border: '1px solid #b7eb8f' }}>
        <div style={{ fontSize: 12, color: '#52c41a' }}>
          模型配置即时生效，无需重启。系统负载高时自动切换为决策模型以节省成本。
        </div>
      </Card>
      <Form form={form} layout="vertical">
        <Form.Item name="default_model" label="默认模型" help="通用查询和分析使用的模型">
          <Select options={models} />
        </Form.Item>
        <Form.Item name="decision_model" label="决策模型" help="意图分类和路由决策使用，建议选快速模型">
          <Select options={models} />
        </Form.Item>
        <Form.Item name="summary_model" label="汇总模型" help="报告汇总和格式化使用的模型">
          <Select options={models} />
        </Form.Item>
        <Button type="primary" icon={<SaveOutlined />} loading={saving} onClick={handleSave}>保存</Button>
      </Form>
    </div>
  );
}
