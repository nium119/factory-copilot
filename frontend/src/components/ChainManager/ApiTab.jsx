import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  Button, Card, Form, Input, Select, Switch, Space, Tag, Popconfirm, message,
  Spin, Empty, Typography, Table, Popover,
} from 'antd';
import { PlusOutlined, DeleteOutlined, ReloadOutlined, CloudServerOutlined } from '@ant-design/icons';
import { ProTable, EditableProTable } from '@ant-design/pro-components';
import request from '../../services/request';

const { Text } = Typography;

export default function ApiTab() {
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(false);
  const [skillData, setSkillData] = useState(null);
  const [config, setConfig] = useState({});

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [sysRes, statusRes] = await Promise.all([
        request.get('/chains/compile/systems').catch(() => ({ ok: false })),
        request.get('/chains/compile/status').catch(() => ({ ok: false })),
      ]);
      setSkillData(statusRes);
      if (sysRes.ok) {
        setConfig(sysRes.config || {});
        form.setFieldsValue(sysRes.config || {});
      }
    } catch { message.error('加载失败'); }
    finally { setLoading(false); }
  }, [form]);

  useEffect(() => { load(); }, [load]);

  const updConfig = (updater) => {
    const nc = JSON.parse(JSON.stringify(config));
    updater(nc);
    setConfig(nc);
  };

  const handleApply = async () => {
    try {
      const vals = await form.validateFields().catch(() => ({}));
      const systems = { ...config.systems, ...(vals.systems || {}) };
      await request.put('/chains/compile/systems', { config: { systems } });
      const r = await request.post('/chains/compile/reload');
      message.success(r.message || '已应用'); load();
    } catch { message.error('应用失败'); }
  };

  const allConcepts = (skillData?.skills || []).map(s => s.concept).filter(Boolean);
  if (loading) return <Spin style={{ display: 'block', margin: '60px auto' }} />;
  if (!skillData) return <Empty description='暂无数据' />;

  return (
    <div>
      {!skillData.ok && (
        <Card size='small' style={{ marginBottom: 16, background: '#fffbe6', border: '1px solid #ffe58f' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, color: '#886a00' }}>
            <span>⚠️</span>
            <span>编译器尚未运行，概念列表为空。请先在「业务域配置」tab 中执行推导，再回来配置 API 接口。</span>
          </div>
        </Card>
      )}
      <div style={{ marginBottom: 16 }}>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
          <Button size='small' icon={<PlusOutlined />} onClick={() => updConfig(nc => {
            const systems = nc.systems || {};
            const key = `system_${Object.keys(systems).length + 1}`;
            systems[key] = { baseUrl: '', authType: 'bearer', authConfig: {}, endpoints: [] };
            nc.systems = systems;
          })}>添加接口</Button>
          <Button type='primary' size='small' onClick={handleApply}>应用</Button>
        </Space>
      </div>
      <Form form={form} initialValues={config}>
        {Object.keys(config.systems || {}).length === 0 && skillData.ok && (
          <Card size='small' style={{ background: '#f6f8fa', border: '1px solid #e8e8e8' }}>
            <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
              <span style={{ fontSize: 24 }}>🔌</span>
              <div style={{ lineHeight: 1.8, fontSize: 13, color: '#555' }}>
                <div style={{ fontWeight: 600, marginBottom: 4, color: '#333' }}>暂无 API 接口</div>
                <div>当前所有概念的数据都走 Neo4j 查询。如需从外部 API 获取实时数据，请点击「添加接口」。</div>
              </div>
            </div>
          </Card>
        )}
        {Object.entries(config.systems || {}).map(([sysName, cfg]) => (
          <SystemCard key={sysName} sysName={sysName} cfg={cfg} config={config} updConfig={updConfig}
            skillData={skillData} allConcepts={allConcepts} />
        ))}
      </Form>
    </div>
  );
}

// ── 系统卡片 ──
function SystemCard({ sysName, cfg, config, updConfig, skillData, allConcepts }) {
  const [testFields, setTestFields] = useState({});

  return (
    <Card size='small' style={{ marginBottom: 16 }}
      title={
        <Space>
          <CloudServerOutlined />
          <Input style={{ width: 140, fontWeight: 600 }} defaultValue={sysName} key={sysName} placeholder='系统名称'
            onBlur={e => {
              const val = e.target.value.trim();
              if (val && val !== sysName) updConfig(nc => {
                const s = nc.systems || {};
                s[val] = s[sysName]; delete s[sysName]; nc.systems = s;
              });
            }} />
          <Tag>API</Tag>
        </Space>
      } extra={
        <Popconfirm title='确定删除?' onConfirm={() => updConfig(nc => {
          const s = nc.systems || {}; delete s[sysName]; nc.systems = s;
        })}>
          <Button size='small' danger icon={<DeleteOutlined />} />
        </Popconfirm>
      }>
      <Space direction='vertical' size={4} style={{ width: '100%' }}>
        <Input addonBefore={<span>Base URL</span>} value={cfg.baseUrl || ''} placeholder='https://api.company.com'
          onChange={e => updConfig(nc => { if (nc.systems?.[sysName]) nc.systems[sysName].baseUrl = e.target.value; })} />
        <Space size={4} style={{ width: '100%' }}>
          <Select value={cfg.authType || 'bearer'} style={{ width: 90 }}
            onChange={v => updConfig(nc => { if (nc.systems?.[sysName]) nc.systems[sysName].authType = v; })}>
            <Select.Option value='bearer'>Bearer</Select.Option>
            <Select.Option value='apikey'>API Key</Select.Option>
            <Select.Option value='basic'>Basic</Select.Option>
          </Select>
          <Input placeholder='Token' style={{ flex: 1 }} value={cfg.authConfig?.token || ''}
            onChange={e => updConfig(nc => {
              const s = nc.systems?.[sysName]; if (s) s.authConfig = { ...s.authConfig, token: e.target.value };
            })} />
          <Input placeholder='超时(秒)' style={{ width: 80 }} value={cfg.authConfig?.timeout || ''}
            onChange={e => updConfig(nc => {
              const s = nc.systems?.[sysName]; if (s) s.authConfig = { ...s.authConfig, timeout: e.target.value };
            })} />
          <Input placeholder='重试次数' style={{ width: 80 }} value={cfg.authConfig?.retries || ''}
            onChange={e => updConfig(nc => {
              const s = nc.systems?.[sysName]; if (s) s.authConfig = { ...s.authConfig, retries: e.target.value };
            })} />
          <Button size='small' onClick={async () => {
            try {
              const r = await request.post(`/chains/compile/systems/${encodeURIComponent(sysName)}/test`);
              message[r.ok ? 'success' : 'warning'](r.ok ? `连接成功 HTTP ${r.status} (${r.elapsed_ms}ms)` : r.message);
            } catch { message.error('测试失败'); }
          }}>测试连接</Button>
        </Space>
      </Space>
      <EndpointList sysName={sysName} config={config} updConfig={updConfig}
        skillData={skillData} allConcepts={allConcepts} testFields={testFields} setTestFields={setTestFields} />
    </Card>
  );
}

// ── 端点列表 (EditableProTable) ──
function EndpointList({ sysName, config, updConfig, skillData, allConcepts, testFields, setTestFields }) {
  const actionRef = useRef();
  const eps = ((config.systems || {})[sysName]?.endpoints || []).map((ep, i) => ({ ...ep, id: i, _idx: i }));
  const cm = skillData?.concept_map || {};

  const columns = [
    { title: '启用', width: 50, search: false,
      render: (_, r) => <Switch size='small' checked={r.enabled !== false}
        onChange={v => updConfig(nc => { const e = nc.systems?.[sysName]?.endpoints?.[r._idx]; if (e) e.enabled = v; })} /> },
    { title: '概念', width: 110, search: false,
      render: (_, r) => {
        const s = (skillData?.skills || []).find(x => x.concept === r.concept);
        return <Tag color='green'>{s?.concept_label || r.concept}</Tag>;
      }},
    { title: '操作', width: 140, search: false,
      render: (_, r) => {
        const ci = cm[r.concept] || {};
        const actions = ci.actions || [];
        return <Select value={r.action || (actions[0]?.name || '')} style={{ width: '100%' }}
          options={actions.map(a => ({ value: a.name, label: a.label || a.name }))}
          onChange={v => updConfig(nc => { const e = nc.systems?.[sysName]?.endpoints?.[r._idx]; if (e) e.action = v; })} />;
      }},
    { title: '方法', width: 70, search: false,
      render: (_, r) => <Select value={r.method || 'GET'} style={{ width: '100%' }}
        onChange={v => updConfig(nc => { const e = nc.systems?.[sysName]?.endpoints?.[r._idx]; if (e) e.method = v; })}>
        <Select.Option value='GET'>GET</Select.Option>
        <Select.Option value='POST'>POST</Select.Option>
        <Select.Option value='PUT'>PUT</Select.Option>
      </Select>},
    { title: '路径', search: false,
      render: (_, r) => <Input value={r.path || ''} placeholder='/api/path'
        onChange={e => updConfig(nc => { const e = nc.systems?.[sysName]?.endpoints?.[r._idx]; if (e) e.path = e.target.value; })} /> },
    { title: '测试', width: 60, search: false,
      render: (_, r) => <Button size='small'
        onClick={async () => {
          try {
            const r2 = await request.post(`/chains/compile/systems/${encodeURIComponent(sysName)}/test-endpoint`,
              { concept: r.concept, ep_idx: r._idx });
            if (r2.ok) {
              const cacheKey = `${sysName}_${r._idx}`;
              setTestFields({ ...testFields, [cacheKey]: r2.fields || [] });
              message.success(`${r2.status} (${r2.elapsed_ms}ms)`);
              if (r2.fields?.length > 0 && (!r.response?.fields || r.response.fields.length === 0)) {
                updConfig(nc => {
                  const e = nc.systems?.[sysName]?.endpoints?.[r._idx];
                  if (e) e.response = { ...(e.response || {}), fields: r2.fields.map(f => ({ apiName: f, name: '' })) };
                });
              }
            } else { message.warning(r2.message); }
          } catch { message.error('测试失败'); }
        }}>▶</Button>},
    { title: '', width: 50, search: false,
      render: (_, r, idx) => (
        <Popconfirm title='确定删除?' onConfirm={() => updConfig(nc => {
          nc.systems?.[sysName]?.endpoints?.splice(idx, 1);
        })}>
          <Button size='small' type='text' danger icon={<DeleteOutlined />} />
        </Popconfirm>
      )},
  ];

  return (
    <div style={{ marginTop: 12 }}>
      <Space style={{ width: '100%', justifyContent: 'space-between', marginBottom: 8 }}>
        <Text strong style={{ fontSize: 12 }}>接口 ({eps.length})</Text>
        <Select style={{ width: 200 }} placeholder='+ 添加接口' value={undefined} showSearch
          filterOption={(input, option) => (option?.label || '').toLowerCase().includes(input.toLowerCase())}
          options={allConcepts.map(c => {
            const s = (skillData?.skills || []).find(x => x.concept === c);
            return { value: c, label: s?.concept_label || c };
          })}
          onChange={val => add(val)} />
      </Space>
      <Table size='small' pagination={false} rowKey='_idx' dataSource={eps}
        locale={{ emptyText: '暂无接口，点击上方下拉添加' }}
        columns={columns}
        expandable={{
          expandedRowRender: (ep) => {
            const idx = ep._idx;
            const sk = (skillData?.skills || []).find(x => x.concept === ep.concept);
            const update = (f, v) => updConfig(nc => {
              const e = nc.systems?.[sysName]?.endpoints?.[idx];
              if (e) e[f] = v;
            });
            return (
              <div style={{ padding: 8 }}>
                <DetailSection title='请求参数'>
                  <Space size={8} wrap style={{ marginBottom: 12 }}>
                    <Space size={4}><Text style={{ fontSize: 11, color: '#888' }}>页码</Text><Input style={{ width: 80 }} size='small' placeholder='page' value={ep.pageParam || ''} onChange={e => update('pageParam', e.target.value)} /></Space>
                    <Space size={4}><Text style={{ fontSize: 11, color: '#888' }}>每页数</Text><Input style={{ width: 80 }} size='small' placeholder='size' value={ep.sizeParam || ''} onChange={e => update('sizeParam', e.target.value)} /></Space>
                    <Space size={4}><Text style={{ fontSize: 11, color: '#888' }}>排序字段</Text><Input style={{ width: 80 }} size='small' placeholder='sort' value={ep.sortParam || ''} onChange={e => update('sortParam', e.target.value)} /></Space>
                    <Space size={4}><Text style={{ fontSize: 11, color: '#888' }}>排序方式</Text><Input style={{ width: 80 }} size='small' placeholder='asc/desc' value={ep.orderParam || ''} onChange={e => update('orderParam', e.target.value)} /></Space>
                  </Space>
                  <EditableParamTable params={ep.params || []} sk={sk} sysName={sysName} idx={idx} updConfig={updConfig} />
                </DetailSection>
                <DetailSection title='响应配置'>
                  <SuccessConditions conds={ep.response?.successConditions || [{ type: 'http', field: 'status', operator: 'eq', value: '200' }]}
                    sysName={sysName} idx={idx} updConfig={updConfig} />
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 8, alignItems: 'center' }}>
                    <Space size={4}><Text style={{ fontSize: 11, color: '#888' }}>错误字段</Text>
                      <Input style={{ width: 100 }} placeholder='error' value={ep.response?.errorField || ''}
                        onChange={e => updConfig(nc => { const e = nc.systems?.[sysName]?.endpoints?.[idx]; if (e) { e.response = e.response || {}; e.response.errorField = e.target.value; } })} /></Space>
                    <Space size={4}><Text style={{ fontSize: 11, color: '#888' }}>格式</Text>
                      <Select style={{ width: 80 }} value={ep.response?.format || 'json'}
                        onChange={v => updConfig(nc => { const e = nc.systems?.[sysName]?.endpoints?.[idx]; if (e) { e.response = e.response || {}; e.response.format = v; } })}>
                        <Select.Option value='json'>JSON</Select.Option><Select.Option value='xml'>XML</Select.Option></Select></Space>
                    <Space size={4}><Text style={{ fontSize: 11, color: '#888' }}>数据路径</Text>
                      <Input style={{ width: 120 }} placeholder='data.items' value={ep.response?.root || ''}
                        onChange={e => updConfig(nc => { const e = nc.systems?.[sysName]?.endpoints?.[idx]; if (e) { e.response = e.response || {}; e.response.root = e.target.value; } })} /></Space>
                    <Space size={4}><Text style={{ fontSize: 11, color: '#888' }}>总数字段</Text>
                      <Input style={{ width: 100 }} placeholder='total' value={ep.response?.totalField || ''}
                        onChange={e => updConfig(nc => { const e = nc.systems?.[sysName]?.endpoints?.[idx]; if (e) { e.response = e.response || {}; e.response.totalField = e.target.value; } })} /></Space>
                  </div>
                  <RespFieldTable fields={ep.response?.fields || []} sk={sk} sysName={sysName} epIdx={idx}
                    updConfig={updConfig} testFields={testFields} />
                </DetailSection>
              </div>
            );
          },
        }}
      />
    </div>
  );
}

// ── 小部件 ──

function DetailSection({ title, onAdd, children }) {
  return (
    <div style={{ marginBottom: 12 }}>
      {title && <Text type='secondary' style={{ fontSize: 12, display: 'block', marginBottom: 6 }}>{title}</Text>}
      {children}
      {onAdd && <Button size='small' type='dashed' icon={<PlusOutlined />} onClick={onAdd} block style={{ marginTop: 6 }}>添加</Button>}
    </div>
  );
}

function EditableParamTable({ params, sk, sysName, idx, updConfig }) {
  const editableFormRef = useRef();
  const outputOpts = (sk?.output_fields || []).map(f => ({ value: f.name, label: f.label || f.name }));
  const apiOpts = (sk?.output_fields || []).map(f => ({ value: f.name, label: f.name }));

  const dataWithId = params.map((p, i) => ({ ...p, id: i }));
  const allKeys = dataWithId.map(r => r.id);
  const [editableKeys, setEditableKeys] = useState(() => allKeys);

  const handleChange = (data) => {
    updConfig(nc => {
      const e = nc.systems?.[sysName]?.endpoints?.[idx];
      if (e) e.params = data.map(({ id, ...p }) => p);
    });
  };

  useEffect(() => {
    setEditableKeys(dataWithId.map(r => r.id));
  }, [params.length]);

  const columns = [
    { title: '属性名', dataIndex: 'name', width: 140,
      renderFormItem: () => <Select placeholder='选择' showSearch style={{ width: '100%' }}
        filterOption={(input, option) => (option?.label || '').includes(input)} options={outputOpts} /> },
    { title: '接口参数', dataIndex: 'apiName', width: 120,
      renderFormItem: () => <Select placeholder='输入或选择' showSearch allowClear style={{ width: '100%' }}
        filterOption={(input, option) => (option?.label || '').includes(input)} options={apiOpts} /> },
    { title: '类型', dataIndex: 'type', width: 70,
      renderFormItem: () => <Select style={{ width: '100%' }}
        options={[{ value: 'string', label: '字符串' }, { value: 'integer', label: '整数' }, { value: 'number', label: '小数' }, { value: 'boolean', label: '布尔' }]} /> },
    { title: '位置', dataIndex: 'in', width: 70,
      renderFormItem: () => <Select style={{ width: '100%' }}
        options={[{ value: 'query', label: 'Query' }, { value: 'body', label: 'Body' }]} /> },
    { title: '', width: 40, editable: () => false,
      render: (_, r) => <Button size='small' type='text' danger icon={<DeleteOutlined />}
        onClick={() => handleChange(dataWithId.filter(d => d.id !== r.id))} /> },
  ];

  return (
    <EditableProTable
      editableFormRef={editableFormRef}
      rowKey='id'
      columns={columns}
      value={dataWithId}
      onChange={handleChange}
      ghost
      locale={{ emptyText: '无参数' }}
      recordCreatorProps={{
        newRecordType: 'dataSource',
        record: () => ({ id: Date.now(), name: '', apiName: '', type: 'string', in: 'query' }),
      }}
      editable={{
        type: 'multiple',
        editableKeys,
        actionRender: () => [],
        onChange: setEditableKeys,
        onValuesChange: (_, list) => handleChange(list),
      }}
    />
  );
}

function SuccessConditions({ conds, sysName, idx, updConfig }) {
  const dataWithId = conds.map((c, i) => ({ ...c, id: i }));
  const [editableKeys, setEditableKeys] = useState(() => dataWithId.map(r => r.id));

  const handleChange = (data) => updConfig(nc => {
    const e = nc.systems?.[sysName]?.endpoints?.[idx];
    if (e) { e.response = e.response || {}; e.response.successConditions = data.map(({ id, ...c }) => c); }
  });

  useEffect(() => { setEditableKeys(dataWithId.map(r => r.id)); }, [conds.length]);

  const columns = [
    { title: '类型', dataIndex: 'type', width: 80,
      renderFormItem: () => <Select style={{ width: '100%' }} options={[
        { value: 'http', label: 'HTTP' }, { value: 'field', label: '字段' },
      ]} /> },
    { title: '运算符', dataIndex: 'operator', width: 80,
      renderFormItem: (_, { record, isEditable }) => {
        const fieldMode = record?.type === 'field' || dataWithId.find(d => d.id === record?.id)?.type === 'field';
        return <Select style={{ width: '100%' }} options={[
          ...(fieldMode ? [{ value: 'exists', label: '存在' }] : []),
          { value: 'eq', label: '=' },
          { value: 'gte', label: '>=' },
          { value: 'lte', label: '<=' },
        ]} />;
      }},
    { title: '字段', dataIndex: 'field', width: 140, renderFormItem: (_, { record }) => {
      const isHttp = record?.type !== 'field';
      return <Input placeholder={isHttp ? '状态码如200' : '字段路径如code'} />;
    }},
    { title: '值', dataIndex: 'value', width: 100, editable: (_, r) => r?.operator !== 'exists',
      renderFormItem: () => <Input placeholder='期望值' /> },
    { title: '', width: 40, editable: () => false,
      render: (_, r) => <Button size='small' type='text' danger icon={<DeleteOutlined />}
        onClick={() => handleChange(dataWithId.filter(d => d.id !== r.id))} /> },
  ];

  return (
    <div style={{ marginBottom: 8 }}>
      <Text style={{ fontSize: 12, display: 'block', marginBottom: 6 }}>成功条件（全部满足）</Text>
      <EditableProTable
        rowKey='id'
        columns={columns}
        value={dataWithId}
        onChange={handleChange}
        ghost
        recordCreatorProps={{ newRecordType: 'dataSource', record: () => ({ id: Date.now(), type: 'http', field: 'status', operator: 'eq', value: '200' }) }}
        editable={{ type: 'multiple', editableKeys, onChange: setEditableKeys, actionRender: () => [], onValuesChange: (_, list) => handleChange(list) }}
      />
    </div>
  );
}

function RespFieldTable({ fields, sk, sysName, epIdx, updConfig, testFields }) {
  const editableFormRef = useRef();
  const dataWithId = fields.map((f, i) => ({ ...f, id: i }));
  const [editableKeys, setEditableKeys] = useState(() => dataWithId.map(r => r.id));

  const handleChange = (data) => updConfig(nc => {
    const e = nc.systems?.[sysName]?.endpoints?.[epIdx];
    if (e) e.response.fields = data.map(({ id, ...f }) => f);
  });

  useEffect(() => { setEditableKeys(dataWithId.map(r => r.id)); }, [fields.length]);

  const cacheKey = `${sysName}_${epIdx}`;
  const cached = testFields[cacheKey] || [];
  const outputOpts = (sk?.output_fields || []).map(f => ({ value: f.name, label: f.label || f.name }));

  const columns = [
    { title: '接口字段', dataIndex: 'apiName', width: 130,
      renderFormItem: () => <Select placeholder={cached.length > 0 ? '选择' : '先点▶测试'} style={{ width: '100%' }} showSearch allowClear
        filterOption={(input, option) => (option?.label || '').includes(input)}
        options={cached.map(f => ({ value: f, label: f }))} /> },
    { title: '→ 本体属性', dataIndex: 'name', width: 130,
      renderFormItem: () => <Select placeholder='选择' style={{ width: '100%' }} showSearch
        filterOption={(input, option) => (option?.label || '').includes(input)} options={outputOpts} /> },
    { title: '', width: 40, editable: () => false,
      render: (_, r) => <Button size='small' type='text' danger icon={<DeleteOutlined />}
        onClick={() => handleChange(dataWithId.filter(d => d.id !== r.id))} /> },
  ];

  return (
    <EditableProTable
      editableFormRef={editableFormRef}
      rowKey='id'
      columns={columns}
      value={dataWithId}
      onChange={handleChange}
      ghost
      locale={{ emptyText: '无映射' }}
      recordCreatorProps={{
        newRecordType: 'dataSource',
        record: () => ({ id: Date.now(), apiName: '', name: '' }),
      }}
      editable={{
        type: 'multiple',
        editableKeys,
        onChange: setEditableKeys,
        onValuesChange: (_, list) => handleChange(list),
        actionRender: () => [],
      }}
    />
  );
}
