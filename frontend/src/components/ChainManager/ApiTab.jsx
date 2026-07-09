import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  Button, Card, Form, Input, Select, Switch, Space, Tag, Popconfirm, message,
  Spin, Empty, Typography, Table, Popover, Row, Col, Divider,
} from 'antd';
import { PlusOutlined, DeleteOutlined, ReloadOutlined, CloudServerOutlined } from '@ant-design/icons';
import { ProTable, EditableProTable } from '@ant-design/pro-components';
import request from '../../services/request';

const { Text } = Typography;

export default function ApiTab() {
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
      }
    } catch { message.error('加载失败'); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); }, [load]);

  const updConfig = (updater) => {
    const nc = JSON.parse(JSON.stringify(config));
    updater(nc);
    setConfig(nc);
  };

  const handleApply = async () => {
    try {
      await request.put('/chains/compile/systems', { config });
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
          })}>添加 API</Button>
          <Button size='small' onClick={async () => {
            try { await request.put('/chains/compile/systems', { config }); message.success('已保存'); }
            catch { message.error('保存失败'); }
          }}>保存</Button>
          <Button type='primary' size='small' onClick={handleApply}>应用</Button>
        </Space>
      </div>
      {Object.keys(config.systems || {}).length === 0 && skillData.ok && (
          <Card size='small' style={{ background: '#f6f8fa', border: '1px solid #e8e8e8' }}>
            <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
              <span style={{ fontSize: 24 }}>🔌</span>
              <div style={{ lineHeight: 1.8, fontSize: 13, color: '#555' }}>
                <div style={{ fontWeight: 600, marginBottom: 4, color: '#333' }}>暂无 API 接口</div>
                <div>当前所有概念的数据都走 Neo4j 查询。如需从外部 API 获取实时数据，请点击「添加 API」。</div>
              </div>
            </div>
          </Card>
        )}
        {Object.entries(config.systems || {}).map(([sysName, cfg]) => (
          <SystemCard key={sysName} sysName={sysName} cfg={cfg} config={config} updConfig={updConfig}
            skillData={skillData} allConcepts={allConcepts} />
        ))}
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
        <Space.Compact style={{ width: '100%' }}>
          <Button disabled style={{ width: 80, borderRight: 0 }}>Base URL</Button>
          <Input value={cfg.baseUrl || ''} placeholder='https://api.company.com'
            onChange={e => updConfig(nc => { if (nc.systems?.[sysName]) nc.systems[sysName].baseUrl = e.target.value; })} />
        </Space.Compact>
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
  const eps = ((config.systems || {})[sysName]?.endpoints || []).map((ep, i) => ({ ...ep, _idx: i }));
  const cm = skillData?.concept_map || {};
  const [editableKeys, setEditableKeys] = useState(() => eps.map(r => r._idx));
  const [expandedKeys, setExpandedKeys] = useState([]);
  const conceptOpts = allConcepts.map(c => {
    const s = (skillData?.skills || []).find(x => x.concept === c);
    return { value: c, label: s?.concept_label || c };
  });
  const handleChange = (data) => updConfig(nc => {
    if (nc.systems?.[sysName]) nc.systems[sysName].endpoints = data.map(({ _idx, ...ep }) => ep);
  });
  useEffect(() => { setEditableKeys(eps.map(r => r._idx)); }, [eps.length]);

  const columns = [
    { title: '启用', width: 50, editable: () => false,
      render: (_, r) => <Switch size='small' checked={r.enabled !== false}
        onChange={v => updConfig(nc => { const e = nc.systems?.[sysName]?.endpoints?.[r._idx]; if (e) e.enabled = v; })} /> },
    { title: '概念', dataIndex: 'concept', width: 200,
      renderFormItem: () => <Select showSearch style={{ width: '100%' }} placeholder='选择概念'
        filterOption={(input, option) => (option?.label || '').toLowerCase().includes(input.toLowerCase())}
        options={conceptOpts} /> },
    { title: '操作', dataIndex: 'action', width: 200,
      renderFormItem: (_, { record }) => {
        const ci = cm[record?.concept] || {}; const actions = ci.actions || [];
        return <Select style={{ width: '100%' }} placeholder='操作' options={actions.map(a => ({ value: a.name, label: a.label || a.name }))} />;
      }},
    { title: '方法', dataIndex: 'method', width: 70,
      renderFormItem: () => <Select style={{ width: '100%' }}>
        <Select.Option value='GET'>GET</Select.Option>
        <Select.Option value='POST'>POST</Select.Option>
        <Select.Option value='PUT'>PUT</Select.Option>
      </Select> },
    { title: '路径', dataIndex: 'path',
      renderFormItem: () => <Input placeholder='/api/path' /> },
    { title: '测试', width: 60, editable: () => false,
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
        { title: '', width: 40, editable: () => false,
          render: (_, r) => <Button size='small' type='text' danger icon={<DeleteOutlined />}
            onClick={(e) => { e.stopPropagation(); handleChange(eps.filter(ep => ep._idx !== r._idx)); }} /> },
  ];

  return (
    <div style={{ marginTop: 12 }}>
      <Text strong style={{ fontSize: 12, display: 'block', marginBottom: 8 }}>接口 ({eps.length})</Text>
      <EditableProTable
        rowKey='_idx'
        columns={columns}
        value={eps}
        onChange={handleChange}
        ghost
        locale={{ emptyText: '暂无接口' }}
        recordCreatorProps={{
          newRecordType: 'dataSource',
          creatorButtonText: '添加接口',
          record: () => ({ 
            _idx: Date.now(),
            _key: Date.now() + '_endpoints',
            concept: '', 
            action: '', 
            method: 'GET', 
            path: '', 
            enabled: true, 
            pageParam: '', 
            sizeParam: '', 
            sortParam: '', 
            orderParam: '', 
            params: [], 
            response: { 
              type: 'array', 
              root: '', 
              fields: [], 
              format: 'json', 
              errorField: '', 
              totalField: '', 
              successConditions: [{ 
                type: 'http', 
                field: 'status', 
                operator: 'eq', 
                value: '200' 
              }] 
            } 
          }) 
        }}
        editable={{ 
          type: 'multiple', 
          editableKeys, 
          onChange: setEditableKeys,
          onValuesChange: (_, list) => handleChange(list)
        }}
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
                  <Row gutter={[8, 4]} style={{ marginBottom: 12 }}>
                    <Col span={12}><div style={{ display: 'flex', alignItems: 'center', gap: 4 }}><Text style={{ fontSize: 11, color: '#888', width: 56 }}>页码</Text><Input style={{ flex: 1 }} placeholder='page' value={ep.pageParam || ''} onChange={e => update('pageParam', e.target.value)} /></div></Col>
                    <Col span={12}><div style={{ display: 'flex', alignItems: 'center', gap: 4 }}><Text style={{ fontSize: 11, color: '#888', width: 56 }}>每页数</Text><Input style={{ flex: 1 }} placeholder='size' value={ep.sizeParam || ''} onChange={e => update('sizeParam', e.target.value)} /></div></Col>
                    <Col span={12}><div style={{ display: 'flex', alignItems: 'center', gap: 4 }}><Text style={{ fontSize: 11, color: '#888', width: 56 }}>排序字段</Text><Input style={{ flex: 1 }} placeholder='sort' value={ep.sortParam || ''} onChange={e => update('sortParam', e.target.value)} /></div></Col>
                    <Col span={12}><div style={{ display: 'flex', alignItems: 'center', gap: 4 }}><Text style={{ fontSize: 11, color: '#888', width: 56 }}>排序方式</Text><Input style={{ flex: 1 }} placeholder='asc/desc' value={ep.orderParam || ''} onChange={e => update('orderParam', e.target.value)} /></div></Col>
                  </Row>
                  <EditableParamTable params={ep.params || []} sk={sk} sysName={sysName} idx={idx} updConfig={updConfig} />
                </DetailSection>
                <Divider plain orientationMargin={0}>响应处理</Divider>
                <DetailSection title='响应配置'>
                  <SuccessConditions conds={ep.response?.successConditions || [{ type: 'http', field: 'status', operator: 'eq', value: '200' }]}
                    sysName={sysName} idx={idx} updConfig={updConfig} />
                  <Row gutter={[8, 4]} style={{ marginBottom: 8 }}>
                    <Col span={12}><div style={{ display: 'flex', alignItems: 'center', gap: 4 }}><Text style={{ fontSize: 11, color: '#888', width: 56 }}>错误字段</Text><Input style={{ flex: 1 }} placeholder='error' value={ep.response?.errorField || ''}
                      onChange={e => updConfig(nc => { const ep = nc.systems?.[sysName]?.endpoints?.[idx]; if (ep) { ep.response = ep.response || {}; ep.response.errorField = e.target.value; } })} /></div></Col>
                    <Col span={12}><div style={{ display: 'flex', alignItems: 'center', gap: 4 }}><Text style={{ fontSize: 11, color: '#888', width: 56 }}>格式</Text><Select style={{ flex: 1 }} value={ep.response?.format || 'json'}
                      onChange={v => updConfig(nc => { const e = nc.systems?.[sysName]?.endpoints?.[idx]; if (e) { e.response = e.response || {}; e.response.format = v; } })}>
                      <Select.Option value='json'>JSON</Select.Option><Select.Option value='xml'>XML</Select.Option></Select></div></Col>
                    <Col span={12}><div style={{ display: 'flex', alignItems: 'center', gap: 4 }}><Text style={{ fontSize: 11, color: '#888', width: 56 }}>数据路径</Text><Input style={{ flex: 1 }} placeholder='data.items' value={ep.response?.root || ''}
                      onChange={ev => updConfig(nc => { const ept = nc.systems?.[sysName]?.endpoints?.[idx]; if (ept) { ept.response = ept.response || {}; ept.response.root = ev.target.value; } })} /></div></Col>
                    <Col span={12}><div style={{ display: 'flex', alignItems: 'center', gap: 4 }}><Text style={{ fontSize: 11, color: '#888', width: 56 }}>总数字段</Text><Input style={{ flex: 1 }} placeholder='total' value={ep.response?.totalField || ''}
                      onChange={ev => updConfig(nc => { const ept = nc.systems?.[sysName]?.endpoints?.[idx]; if (ept) { ept.response = ept.response || {}; ept.response.totalField = ev.target.value; } })} /></div></Col>
                  </Row>
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
    <Card size='small' style={{ marginBottom: 8 }} title={title}
      extra={onAdd && <Button size='small' type='dashed' icon={<PlusOutlined />} onClick={(e) => { e.stopPropagation(); onAdd(); }}>添加</Button>}>
      {children}
    </Card>
  );
}

function EditableParamTable({ params, sk, sysName, idx, updConfig }) {
  const editableFormRef = useRef();
  const outputOpts = (sk?.output_fields || []).map(f => ({ value: f.name, label: f.label || f.name }));
  const apiOpts = (sk?.output_fields || []).map(f => ({ value: f.name, label: f.name }));

  const [editableKeys, setEditableKeys] = useState(() => params.map(r => r._key));

  const handleChange = (data) => {
    updConfig(nc => {
      const e = nc.systems?.[sysName]?.endpoints?.[idx];
      if (e) e.params = data.map(p => ({ ...p }));
    });
  };

  useEffect(() => {
    setEditableKeys(params.map(r => r._key));
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
        options={[{ value: 'string', label: 'string' }, { value: 'integer', label: 'integer' }, { value: 'number', label: 'number' }, { value: 'boolean', label: 'boolean' }]} /> },
    { title: '位置', dataIndex: 'in', width: 70,
      renderFormItem: () => <Select style={{ width: '100%' }}
        options={[{ value: 'query', label: 'Query' }, { value: 'body', label: 'Body' }]} /> },
    { title: '', width: 40, editable: () => false,
      render: (_, r) => <Button size='small' type='text' danger icon={<DeleteOutlined />}
        onClick={(e) => { e.stopPropagation(); handleChange(params.filter(d => d._key !== r._key)); }} /> },
  ];

  return (
    <EditableProTable
      editableFormRef={editableFormRef}
      rowKey='_key'
      columns={columns}
      value={params}
      onChange={handleChange}
      ghost
      locale={{ emptyText: '无参数' }}
      recordCreatorProps={{
        newRecordType: 'dataSource',
        record: () => ({ _key: Date.now() + '_params', name: '', apiName: '', type: 'string', in: 'query' }),
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
  const condsWithKey = conds.map((c, i) => ({ ...c, _key: c._key || i }));
  const [editableKeys, setEditableKeys] = useState(() => condsWithKey.map(r => r._key));

  const handleChange = (data) => updConfig(nc => {
    const e = nc.systems?.[sysName]?.endpoints?.[idx];
    if (e) { e.response = e.response || {}; e.response.successConditions = data.map(c => ({ ...c })); }
  });

  useEffect(() => { setEditableKeys(condsWithKey.map(r => r._key)); }, [conds.length]);

  const columns = [
    { title: 'type', dataIndex: 'type', width: 80,
      renderFormItem: () => <Select style={{ width: '100%' }} options={[
        { value: 'http', label: 'HTTP' }, { value: 'field', label: 'field' },
      ]} /> },
    { title: 'operator', dataIndex: 'operator', width: 80,
      renderFormItem: (_, { record, isEditable }) => {
        const fieldMode = record?.type === 'field' || condsWithKey.find(d => d._key === record?._key)?.type === 'field';
        return <Select style={{ width: '100%' }} options={[
          ...(fieldMode ? [{ value: 'exists', label: 'exists' }] : []),
          { value: 'eq', label: '=' },
          { value: 'gte', label: '>=' },
          { value: 'lte', label: '<=' },
        ]} />;
      }},
    { title: 'field', dataIndex: 'field', width: 140, renderFormItem: (_, { record }) => {
      const isHttp = record?.type !== 'field';
      return <Input placeholder={isHttp ? '状态码如200' : '字段路径如code'} />;
    }},
    { title: 'value', dataIndex: 'value', width: 100, editable: (_, r) => r?.operator !== 'exists',
      renderFormItem: () => <Input placeholder='期望值' /> },
    { title: '', width: 40, editable: () => false,
      render: (_, r) => <Button size='small' type='text' danger icon={<DeleteOutlined />}
        onClick={(e) => { e.stopPropagation(); handleChange(conds.filter(d => d._key !== r._key)); }} /> },
  ];

  return (
    <div style={{ marginBottom: 8 }}>
      <Text style={{ fontSize: 12, display: 'block', marginBottom: 6 }}>成功条件（全部满足）</Text>
      <EditableProTable
        rowKey='_key'
        columns={columns}
        value={condsWithKey}
        onChange={handleChange}
        ghost
        recordCreatorProps={{ newRecordType: 'dataSource', record: () => ({ _key: Date.now() + '_conds', type: 'http', field: 'status', operator: 'eq', value: '200' }) }}
        editable={{ type: 'multiple', editableKeys, onChange: setEditableKeys, actionRender: () => [], onValuesChange: (_, list) => handleChange(list) }}
      />
    </div>
  );
}

function RespFieldTable({ fields, sk, sysName, epIdx, updConfig, testFields }) {
  const editableFormRef = useRef();
  const [editableKeys, setEditableKeys] = useState(() => fields.map(r => r._key));

  const handleChange = (data) => updConfig(nc => {
    const e = nc.systems?.[sysName]?.endpoints?.[epIdx];
    if (e) e.response.fields = data.map(f => ({ ...f }));
  });

  useEffect(() => { setEditableKeys(fields.map(r => r._key)); }, [fields.length]);

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
        onClick={(e) => { e.stopPropagation(); handleChange(fields.filter(d => d._key !== r._key)); }} /> },
  ];

  return (
    <EditableProTable
      editableFormRef={editableFormRef}
      rowKey='_key'
      columns={columns}
      value={fields}
      onChange={handleChange}
      ghost
      locale={{ emptyText: '无映射' }}
      recordCreatorProps={{
        newRecordType: 'dataSource',
        record: () => ({ _key: Date.now() + '_fields', apiName: '', name: '' }),
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
