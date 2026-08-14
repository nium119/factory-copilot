import React, { useState, useEffect } from 'react';
import { Form, Select, Button, Switch, Input, InputNumber, message, Spin, Table, Drawer, Space, Popconfirm } from 'antd';
import { EditOutlined, PlusOutlined, DeleteOutlined, ApiOutlined } from '@ant-design/icons';
import request from '../../services/request';

export default function ModelConfigTab() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editingModel, setEditingModel] = useState(null);
  const [form] = Form.useForm();
  const [selForm] = Form.useForm();
  const [selection, setSelection] = useState({});

  useEffect(() => {
    setLoading(true);
    request.get('/config/models').then(d => {
      if (d.ok) { setData(d); setSelection(d.selection || {}); selForm.setFieldsValue(d.selection); }
    }).catch(() => message.error('加载失败'))
    .finally(() => setLoading(false));
  }, [selForm]);

  if (loading || !data) return <Spin style={{ display: 'block', margin: '60px auto' }} />;

  const models = data.models || [];
  const enabledModels = models.filter(m => m.enabled);

  const handleToggle = async (name, enabled) => {
    const model = models.find(m => m.name === name);
    if (enabled) {
      // 启用前验证：未配置 Key 拒绝启用
      if (!model?.api_key) {
        message.warning(`${model?.label || name} 未配置 API Key，请先编辑填写后再启用`);
        return;
      }
      // 启用前验证连接：Key 有效才允许启用
      const hide = message.loading('验证连接...', 0);
      try {
        const res = await request.post(`/config/models/${encodeURIComponent(name)}/test`);
        hide();
        if (!res.ok) {
          message.warning(`连接验证失败：${res.message || 'Key 无效'}`);
          return;
        }
      } catch { hide(); message.error('连接验证失败'); return; }
    }
    const updated = models.map(m => m.name === name ? { ...m, enabled } : m);
    setData({ ...data, models: updated });
    const selVals = selForm.getFieldsValue();
    try {
      // 模型分配 Select 用 state（非 Form.Item 绑定），需与表单字段合并后保存，避免覆盖成空
      await request.put('/config/models', { models: updated, selection: { ...selVals, ...selection } });
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

  const saveSel = async (key, val) => {
    const sel = { ...selection, [key]: val };
    setSelection(sel);
    try { await request.put('/config/models', { models, selection: sel }); message.success('已更新'); } catch { message.error('保存失败'); }
  };

  const ModelRoleSelect = ({ label, help, value, options, onChange }) => (
    <div>
      <div style={{ fontSize: 12, color: '#888', marginBottom: 4 }}>{label}</div>
      <Select size="small" style={{ width: 240 }} value={value} options={options}
        onChange={onChange} />
      <div style={{ fontSize: 10, color: '#bbb', marginTop: 2 }}>{help}</div>
    </div>
  );

  const columns = [
    { title: '类型', dataIndex: 'type', width: 60, render: v => v === 'embedding' ? <span style={{ color: '#6c5ce7', fontSize: 12 }}>向量</span> : v === 'asr' ? <span style={{ color: '#fa8c16', fontSize: 12 }}>语音</span> : <span style={{ color: '#1890ff', fontSize: 12 }}>聊天</span> },
    { title: '标识', dataIndex: 'name', width: 140, render: v => <code style={{ fontSize: 12 }}>{v}</code> },
    { title: '显示名', dataIndex: 'label', width: 160 },
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
        <div style={{ fontWeight: 500, marginBottom: 12 }}>模型分配</div>
        <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', alignItems: 'flex-start' }}>
          <ModelRoleSelect label="决策模型" help="路由分类、DynamicPlanner 下一步决策"
            value={selection.decision_model}
            options={enabledModels.filter(m => m.type !== 'embedding').map(m => ({ value: m.name, label: m.label }))}
            onChange={(val) => saveSel('decision_model', val)}
          />
          <ModelRoleSelect label="汇总模型" help="DynamicPlanner 综合分析报告"
            value={selection.summary_model}
            options={enabledModels.filter(m => m.type !== 'embedding').map(m => ({ value: m.name, label: m.label }))}
            onChange={(val) => saveSel('summary_model', val)}
          />
          <ModelRoleSelect label="向量模型" help="RAG 语义检索 / Skill Embedding"
            value={selection.embedding_model}
            options={enabledModels.filter(m => m.type === 'embedding').map(m => ({ value: m.name, label: m.label }))}
            onChange={(val) => saveSel('embedding_model', val)}
          />
          <ModelRoleSelect label="语音识别模型" help="录音转文字（type=asr）"
            value={selection.asr_model}
            options={enabledModels.filter(m => m.type === 'asr').map(m => ({ value: m.name, label: m.label }))}
            onChange={(val) => saveSel('asr_model', val)}
          />
          <div>
            <div style={{ fontSize: 12, color: '#888', marginBottom: 4 }}>关键词匹配</div>
            <Switch size="small" checked={selection.enable_bm25 !== false}
              onChange={(val) => saveSel('enable_bm25', val)} />
            <div style={{ fontSize: 10, color: '#bbb', marginTop: 2 }}>BM25 全文检索</div>
          </div>
        </div>
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
          <Form.Item name="provider" label="Provider" help="qwen/openai/deepseek/ollama 等，决定 API 协议"
            rules={[{ required: true }]}>
            <Input placeholder="如 openai（Ollama/vLLM 也是这个）" />
          </Form.Item>
          <Form.Item name="api_key" label="API Key">
            <Input.Password placeholder="sk-xxx" />
          </Form.Item>
          <Form.Item name="api_url" label="API 地址">
            <Input placeholder="https://api.xxx.com/v1" />
          </Form.Item>
          <Form.Item name="type" label="模型类型" help="聊天模型用于对话，Embedding 用于向量化">
            <Select options={[{ value: 'chat', label: '聊天 (Chat)' }, { value: 'embedding', label: '向量 (Embedding)' }]} />
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
