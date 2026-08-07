import React, { useState, useEffect, useCallback } from 'react';
import {
  Table, Button, Modal, Form, Input, Select, Switch, Tag, Space, message, Popconfirm,
} from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import request from '../../services/request';

const TYPE_OPTS = [
  { value: 'concept_query', label: '概念查询' },
  { value: 'aggregate', label: '聚合统计' },
  { value: 'transform', label: '数据变换' },
];
const RISK_OPTS = [
  { value: 'READ', label: '只读' },
  { value: 'WRITE_AUDIT', label: '写（走治理）' },
];
const KIND_OPTS = [
  { value: 'cypher_template', label: 'Cypher 模板（只读）' },
  { value: 'aggregate', label: '聚合（只读）' },
  { value: 'map_to_action', label: '映射本体 Action（写）' },
];

const riskColor = { READ: 'green', WRITE_AUDIT: 'orange' };
const typeLabel = (t) => (TYPE_OPTS.find(o => o.value === t) || {}).label || t;

export default function DynamicSkillsPanel() {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [form] = Form.useForm();
  const kind = Form.useWatch('kind', form);

  const load = useCallback(async () => {
    setLoading(true);
    try { const d = await request.get('/skills'); setItems(d.items || []); } catch { /* silent */ }
    setLoading(false);
  }, []);
  useEffect(() => { load(); }, [load]);

  const openCreate = () => {
    setEditing(null);
    form.resetFields();
    form.setFieldsValue({ kind: 'cypher_template', risk: 'READ', type: 'concept_query', enabled: true, param_schema: '[]' });
    setModalOpen(true);
  };
  const openEdit = (r) => {
    setEditing(r);
    const impl = r.implementation || {};
    form.setFieldsValue({
      name: r.name, display_name: r.display_name, description: r.description,
      type: r.type, concept: r.concept, risk: r.risk, enabled: r.enabled,
      kind: impl.kind || 'cypher_template',
      template: impl.template || '',
      action_name: impl.action_name || '',
      param_schema: JSON.stringify(r.param_schema || []),
    });
    setModalOpen(true);
  };

  const submit = async () => {
    const v = await form.validateFields();
    let param_schema = [];
    try { param_schema = JSON.parse(v.param_schema || '[]'); }
    catch { message.error('参数 Schema 必须是合法 JSON 数组'); return; }
    if (!Array.isArray(param_schema)) { message.error('参数 Schema 必须是 JSON 数组'); return; }
    const impl = {};
    if (v.kind === 'map_to_action') { impl.kind = 'map_to_action'; impl.action_name = v.action_name || ''; }
    else { impl.kind = v.kind; impl.template = v.template || ''; }
    const body = {
      name: v.name, display_name: v.display_name || '', description: v.description || '',
      type: v.type, concept: v.concept || '', risk: v.risk, enabled: v.enabled ?? true,
      implementation: impl, param_schema,
    };
    try {
      if (editing) await request.put(`/skills/${editing.name}`, body);
      else await request.post('/skills', body);
      message.success('已保存');
      setModalOpen(false);
      load();
    } catch (e) { message.error(e?.response?.data?.detail || '保存失败'); }
  };

  const testRun = async (r) => {
    try {
      const d = await request.post(`/skills/${r.name}/execute`, { params: {} });
      Modal.info({
        title: `${r.display_name || r.name} 执行结果`, width: 620,
        content: <pre style={{ maxHeight: 320, overflow: 'auto', fontSize: 12 }}>{JSON.stringify(d, null, 2)}</pre>,
      });
    } catch (e) { message.error(e?.response?.data?.detail || '执行失败'); }
  };

  const toggle = async (r, enabled) => {
    try {
      await request.put(`/skills/${r.name}`, { ...r, enabled });
      message.success(enabled ? '已启用' : '已停用');
      load();
    } catch (e) { message.error(e?.response?.data?.detail || '操作失败'); }
  };

  const remove = async (r) => {
    try { await request.delete(`/skills/${r.name}`); message.success('已删除'); load(); }
    catch (e) { message.error(e?.response?.data?.detail || '删除失败'); }
  };

  const columns = [
    { title: '名称', dataIndex: 'name', width: 150, render: (v) => <code style={{ fontSize: 12 }}>{v}</code> },
    { title: '显示名', dataIndex: 'display_name', width: 120, render: (v) => v || '-' },
    { title: '类型', dataIndex: 'type', width: 90, render: (v) => <Tag>{typeLabel(v)}</Tag> },
    { title: '实现', dataIndex: ['implementation', 'kind'], width: 140,
      render: (_, r) => <code style={{ fontSize: 12 }}>{(r.implementation || {}).kind}</code> },
    { title: '风险', dataIndex: 'risk', width: 90,
      render: (v) => <Tag color={riskColor[v] || 'default'}>{v}</Tag> },
    { title: '状态', dataIndex: 'enabled', width: 80,
      render: (v, r) => <Switch size="small" checked={!!v} onChange={(c) => toggle(r, c)} /> },
    { title: '操作', width: 170, render: (_, r) => (
      <Space size={4}>
        <Button size="small" type="link" onClick={() => openEdit(r)}>编辑</Button>
        <Button size="small" type="link" onClick={() => testRun(r)}>测试</Button>
        <Popconfirm title={`删除 ${r.name}?`} onConfirm={() => remove(r)}>
          <Button size="small" type="link" danger>删除</Button>
        </Popconfirm>
      </Space>
    ) },
  ];

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        <span style={{ fontSize: 13, color: '#666' }}>
          声明式工具：运行时配置、即时生效。写操作必须映射本体 Action（走统一治理）。
        </span>
        <Button type="primary" size="small" icon={<PlusOutlined />} onClick={openCreate}>新建 Skill</Button>
      </div>
      <Table
        rowKey="name" size="small" loading={loading}
        dataSource={items} columns={columns}
        pagination={{ pageSize: 20, showTotal: (t) => `共 ${t} 个` }}
        locale={{ emptyText: '暂无动态 Skill，点击右上角新建' }}
      />
      <Modal
        title={editing ? `编辑 Skill: ${editing.name}` : '新建 Skill'} open={modalOpen}
        onCancel={() => setModalOpen(false)} onOk={submit} width={640}
        okText="保存" cancelText="取消"
        destroyOnClose
      >
        <Form form={form} layout="vertical" size="small">
          <Form.Item name="name" label="名称（唯一标识）" rules={[{ required: true, message: '必填' }]}>
            <Input disabled={!!editing} placeholder="如 workorder_summary" />
          </Form.Item>
          <Form.Item name="display_name" label="显示名"><Input placeholder="如 工单汇总" /></Form.Item>
          <Form.Item name="description" label="描述"><Input.TextArea rows={2} /></Form.Item>
          <Form.Item name="type" label="类型" rules={[{ required: true }]}><Select options={TYPE_OPTS} /></Form.Item>
          <Form.Item name="concept" label="关联概念（可选）"><Input placeholder="如 WorkOrder" /></Form.Item>
          <Form.Item name="kind" label="实现类型" rules={[{ required: true }]}><Select options={KIND_OPTS} /></Form.Item>
          {kind === 'map_to_action' ? (
            <>
              <Form.Item name="action_name" label="本体 Action 名" rules={[{ required: true, message: '写操作必须映射到已建模 Action' }]}>
                <Input placeholder="如 create_work_order" />
              </Form.Item>
              <Form.Item name="risk" label="风险等级" extra="写操作固定 WRITE_AUDIT（走 action 统一治理）">
                <Select options={RISK_OPTS.filter(o => o.value === 'WRITE_AUDIT')} />
              </Form.Item>
            </>
          ) : (
            <>
              <Form.Item name="template" label="Cypher 模板" rules={[{ required: true, message: '必填（只读 MATCH 开头）' }]}>
                <Input.TextArea rows={4} placeholder="MATCH (n:WorkOrder) WHERE $status IS NULL OR n.status = $status RETURN n.status, count(*) AS cnt" />
              </Form.Item>
              <Form.Item name="risk" label="风险等级" extra="只读固定 READ">
                <Select options={RISK_OPTS.filter(o => o.value === 'READ')} />
              </Form.Item>
            </>
          )}
          <Form.Item name="param_schema" label="参数 Schema（JSON 数组）"
            extra='[{ "name": "status", "label": "状态", "type": "string", "required": false }]'>
            <Input.TextArea rows={3} />
          </Form.Item>
          <Form.Item name="enabled" label="启用" valuePropName="checked"><Switch /></Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
