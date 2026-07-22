import React, { useState, useEffect } from 'react';
import { Form, Select, Button, Switch, Input, InputNumber, message, Spin, Table, Drawer, Space, Popconfirm } from 'antd';
import { SaveOutlined, EditOutlined, PlusOutlined, DeleteOutlined, ApiOutlined } from '@ant-design/icons';
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
    const selVals = selForm.getFieldsValue();
    try {
      await request.put('/config/models', { models: updated, selection: selVals });
      message.success(`${enabled ? '已启用' : '已禁用'}`);
    } catch { message.error('保存失败'); }
  };

  const handleEdit = (model) => {
    setEditingModel(model);
    form.setFieldsValue(model);
    setDrawerOpen(true);
  };

  const handleAdd = () => {
    setEditingModel(null);
    form.resetFields();
    setDrawerOpen(true);
  };

  const handleDelete = (name) => {
    const updated = models.filter(m => m.name !== name);
    setData({ ...data, models: updated });
    message.success('已删除（保存后生效）');
  };

  const handleModelSave = () => {
    const vals = form.getFieldsValue();
    if (!vals.name) { message.warning('请输入模型标识'); return; }
    if (editingModel) {
      const updated = models.map(m => m.name === editingModel.name ? { ...m, ...vals } : m);
      setData({ ...data, models: updated });
    } else {
      if (models.find(m => m.name === vals.name)) { message.warning('模型标识重复'); return; }
      setData({ ...data, models: [...models, vals] });
    }
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
    { title: '标识', dataIndex: 'name', width: 120, render: v => <code>{v}</code> },
    { title: '显示名', dataIndex: 'label', width: 140 },
    { title: 'API Key', dataIndex: 'api_key', width: 140, ellipsis: true,
      render: v => v ? <span style={{ color: '#52c41a', fontSize: 12 }}>已配置</span> : <span style={{ color: '#ccc' }}>未配置</span> },
    { title: '地址', dataIndex: 'api_url', width: 180, ellipsis: true, render: v => v ? <span style={{ fontSize: 12 }}>{v}</span> : '-' },
    { title: '思考', dataIndex: 'enable_thinking', width: 50, align: 'center',
      render: (v, r) => v ? <span style={{ color: '#6c5ce7', fontSize: 14 }} title="支持思考模式">🧠</span> : <span style={{ color: '#ccc', fontSize: 12 }}>—</span> },
    { title: '启用', dataIndex: 'enabled', width: 60, align: 'center',
      render: (v, r) => <Switch size="small" checked={v} onChange={on => handleToggle(r.name, on)} /> },
    { title: '操作', width: 140, render: (_, r) => (
      <Space>
        <Button size="small" icon={<ApiOutlined />}
          onClick={async () => {
            const hide = message.loading('测试中...', 0);
            try {
              const res = await request.post(`/config/models/${encodeURIComponent(r.name)}/test`);
              hide();
              message[res.ok ? 'success' : 'warning'](res.message);
            } catch { hide(); message.error('测试失败'); }
          }}>测试</Button>
        <Button size="small" icon={<EditOutlined />} onClick={() => handleEdit(r)} />
        <Popconfirm title="确定删除？" onConfirm={() => handleDelete(r.name)}>
          <Button size="small" danger icon={<DeleteOutlined />} />
        </Popconfirm>
      </Space>
    )},
  ];

  return (
    <div>
      <div style={{ marginBottom: 12, textAlign: 'right' }}>
        <Button type="primary" icon={<PlusOutlined />} onClick={handleAdd}>添加模型</Button>
      </div>
      <Table size="small" dataSource={models} rowKey="name" pagination={false} columns={columns}
        locale={{ emptyText: '无模型' }} />

      <div style={{ margin: '20px 0', padding: '16px', background: '#fafafa', borderRadius: 8 }}>
        <div style={{ fontWeight: 500, marginBottom: 12 }}>默认模型选择</div>
        <Form form={selForm} layout="inline">
          <Form.Item name="decision_model" label="决策模型" help="意图分类和路由决策，建议选快速模型">
            <Select size="small" style={{ width: 200 }} options={enabledModels.map(m => ({ value: m.name, label: m.label }))} />
          </Form.Item>
          <Form.Item name="embedding_provider" label="Embedding 服务" help="已注册: qwen, openai。需在模型列表中配对应 Key">
            <Input size="small" style={{ width: 120 }} placeholder="如 qwen" />
          </Form.Item>
          <Form.Item name="embedding_model" label="Embedding 模型" help="留空则用 provider 默认模型">
            <Input size="small" style={{ width: 200 }} placeholder="如 text-embedding-v3" />
          </Form.Item>
        </Form>
        <Button type="primary" icon={<SaveOutlined />} loading={saving} onClick={handleSave} style={{ marginTop: 12 }}>
          保存全部配置
        </Button>
      </div>

      <Drawer
        title={editingModel ? `编辑: ${editingModel.label || editingModel.name}` : '添加模型'}
        open={drawerOpen} onClose={() => setDrawerOpen(false)} width={480}
        extra={<Space><Button onClick={() => setDrawerOpen(false)}>取消</Button><Button type="primary" onClick={handleModelSave}>确定</Button></Space>}>
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="模型标识" rules={[{ required: true }]}
            help="英文唯一标识，如 qwen-turbo、gpt-4o">
            <Input placeholder="model_id" />
          </Form.Item>
          <Form.Item name="label" label="显示名称">
            <Input placeholder="如：千问 Turbo" />
          </Form.Item>
          <Form.Item name="api_key" label="API Key">
            <Input.Password placeholder="sk-xxx" />
          </Form.Item>
          <Form.Item name="api_url" label="API 地址">
            <Input placeholder="https://api.xxx.com/v1" />
          </Form.Item>
          <Form.Item name="max_tokens" label="最大 Token">
            <InputNumber min={100} max={256000} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="enable_thinking" label="支持思考模式" valuePropName="checked"
            help="开启后对话时可选择深度思考；仅推理模型（如 qwen3.6-plus）建议开启">
            <Switch />
          </Form.Item>
        </Form>
      </Drawer>
    </div>
  );
}
