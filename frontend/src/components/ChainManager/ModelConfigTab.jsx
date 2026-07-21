import React, { useState, useEffect } from 'react';
import { Form, Select, Button, Switch, message, Spin, Tag, Space } from 'antd';
import { SaveOutlined } from '@ant-design/icons';
import request from '../../services/request';

export default function ModelConfigTab() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm();

  useEffect(() => {
    setLoading(true);
    request.get('/config/models').then(d => {
      if (d.ok) { setData(d); form.setFieldsValue(d.config); }
    }).catch(() => message.error('加载失败'))
    .finally(() => setLoading(false));
  }, [form]);

  if (loading || !data) return <Spin style={{ display: 'block', margin: '60px auto' }} />;

  const allModels = data.all_models || [];
  const enabledModels = data.enabled_models || [];
  const availableModels = allModels.filter(m => enabledModels.includes(m.value));

  const handleSave = async () => {
    try {
      const vals = await form.validateFields(); setSaving(true);
      await request.put('/config/models', { config: vals, enabled_models: enabledModels });
      message.success('已保存，即时生效');
    } catch (err) {
      if (err?.errorFields) return;
      message.error('保存失败');
    } finally { setSaving(false); }
  };

  const toggleModel = (value) => {
    const next = enabledModels.includes(value)
      ? enabledModels.filter(v => v !== value)
      : [...enabledModels, value];
    setData({ ...data, enabled_models: next });
  };

  return (
    <div style={{ maxWidth: 500 }}>
      <div style={{ marginBottom: 16, fontSize: 13, fontWeight: 500 }}>启用模型</div>
      <Space wrap style={{ marginBottom: 20 }}>
        {allModels.map(m => {
          const on = enabledModels.includes(m.value);
          return (
            <Tag key={m.value} color={on ? 'green' : 'default'}
              style={{ cursor: 'pointer', fontSize: 13, padding: '4px 10px' }}
              onClick={() => toggleModel(m.value)}>
              <Switch size="small" checked={on} style={{ marginRight: 6 }} />
              {m.label}
            </Tag>
          );
        })}
      </Space>

      <Form form={form} layout="vertical">
        <Form.Item name="default_model" label="默认模型" help="通用查询和分析使用的模型">
          <Select options={availableModels} />
        </Form.Item>
        <Form.Item name="decision_model" label="决策模型" help="意图分类和路由决策使用，建议选快速模型">
          <Select options={availableModels} />
        </Form.Item>
        <Form.Item name="summary_model" label="汇总模型" help="报告汇总和格式化使用的模型">
          <Select options={availableModels} />
        </Form.Item>
        <Button type="primary" icon={<SaveOutlined />} loading={saving} onClick={handleSave}>保存</Button>
      </Form>
    </div>
  );
}
