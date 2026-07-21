import React, { useState, useEffect } from 'react';
import { Form, Select, Button, Switch, Input, message, Spin, Table, Drawer, Space } from 'antd';
import { SaveOutlined, EditOutlined } from '@ant-design/icons';
import request from '../../services/request';

export default function ModelConfigTab() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editingModel, setEditingModel] = useState(null);
  const [form] = Form.useForm();
  const [selForm] = Form.useForm();

  useEffect(() => {
    setLoading(true);
    request.get('/config/models').then(d => {
      if (d.ok) { setData(d); selForm.setFieldsValue(d.selection); }
    }).catch(() => message.error('加载失败'))
    .finally(() => setLoading(false));
  }, [selForm]);

  if (loading || !data) return <Spin style={{ display: 'block', margin: '60px auto' }} />;

  const models = data.models || [];
  const enabledModels = models.filter(m => m.enabled);

  const handleToggle = async (name, enabled) => {
    const updated = models.map(m => m.name === name ? { ...m, enabled } : m);
    setData({ ...data, models: updated });
  };

  const handleEdit = (model) => {
    setEditingModel(model);
    form.setFieldsValue(model);
    setDrawerOpen(true);
  };

  const handleModelSave = () => {
    const vals = form.getFieldsValue();
    const updated = models.map(m => m.name === editingModel.name ? { ...m, ...vals } : m);
    setData({ ...data, models: updated });
    setDrawerOpen(false);
  };

  const handleSave = async () => {
    try {
      const selVals = await selForm.validateFields(); setSaving(true);
      await request.put('/config/models', { models, selection: selVals });
      message.success('已保存，即时生效');
    } catch (err) {
      if (err?.errorFields) return;
      message.error('保存失败');
    } finally { setSaving(false); }
  };

  const columns = [
    { title: '模型', dataIndex: 'label', width: 200 },
    { title: 'API Key', dataIndex: 'api_key', width: 160, ellipsis: true,
      render: v => v ? <code style={{ fontSize: 11 }}>{v.slice(0, 10)}...</code> : <span style={{ color: '#ccc' }}>未配置</span> },
    { title: '地址', dataIndex: 'api_url', width: 200, ellipsis: true, render: v => v ? <span style={{ fontSize: 12 }}>{v}</span> : '-' },
    { title: '启用', dataIndex: 'enabled', width: 60, align: 'center',
      render: (v, r) => <Switch size="small" checked={v} onChange={on => handleToggle(r.name, on)} /> },
    { title: '', key: 'actions', width: 50,
      render: (_, r) => <Button size="small" icon={<EditOutlined />} onClick={() => handleEdit(r)} /> },
  ];

  return (
    <div style={{ maxWidth: 800 }}>
      <Table size="small" dataSource={models} rowKey="name" pagination={false} columns={columns}
        locale={{ emptyText: '无模型' }} />

      <div style={{ margin: '20px 0', padding: '16px', background: '#fafafa', borderRadius: 8 }}>
        <div style={{ fontWeight: 500, marginBottom: 12 }}>默认模型选择</div>
        <Form form={selForm} layout="inline">
          <Form.Item name="default_model" label="默认">
            <Select size="small" style={{ width: 160 }} options={enabledModels.map(m => ({ value: m.name, label: m.label }))} />
          </Form.Item>
          <Form.Item name="decision_model" label="决策">
            <Select size="small" style={{ width: 160 }} options={enabledModels.map(m => ({ value: m.name, label: m.label }))} />
          </Form.Item>
          <Form.Item name="summary_model" label="汇总">
            <Select size="small" style={{ width: 160 }} options={enabledModels.map(m => ({ value: m.name, label: m.label }))} />
          </Form.Item>
        </Form>
        <Button type="primary" icon={<SaveOutlined />} loading={saving} onClick={handleSave} style={{ marginTop: 12 }}>
          保存全部配置
        </Button>
      </div>

      <Drawer title={`编辑: ${editingModel?.label || ''}`} open={drawerOpen} onClose={() => setDrawerOpen(false)} width={480}
        extra={<Space><Button onClick={() => setDrawerOpen(false)}>取消</Button><Button type="primary" onClick={handleModelSave}>确定</Button></Space>}>
        <Form form={form} layout="vertical">
          <Form.Item name="api_key" label="API Key">
            <Input.Password placeholder="sk-xxx" />
          </Form.Item>
          <Form.Item name="api_url" label="API 地址">
            <Input placeholder="https://api.xxx.com/v1" />
          </Form.Item>
        </Form>
      </Drawer>
    </div>
  );
}
