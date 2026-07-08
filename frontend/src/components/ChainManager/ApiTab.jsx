import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  Button, Card, Form, Input, Select, Switch, Space, Tag, Popconfirm, message,
  Spin, Empty, Typography, Table, Popover,
} from 'antd';
import { PlusOutlined, DeleteOutlined, ReloadOutlined, CloudServerOutlined } from '@ant-design/icons';
import { ProTable } from '@ant-design/pro-table';
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

  const handleChange = (newData) => updConfig(nc => {
    nc.systems[sysName].endpoints = newData.map(({ id, _idx, ...ep }) => ep);
  });

  const columns = [
    { title: '启用', dataIndex: 'enabled', width: 50, editable: true,
      render: (_, r) => <Switch size='small' checked={r.enabled !== false}
        onChange={v => updConfig(nc => { const e = nc.systems?.[sysName]?.endpoints?.[r._idx]; if (e) e.enabled = v; })} />,
      renderFormItem: () => null },
    { title: '概念', dataIndex: 'concept', width: 110, editable: false,
      render: (_, r) => {
        const s = (skillData?.skills || []).find(x => x.concept === r.concept);
        return <Tag color='green'>{s?.concept_label || r.concept}</Tag>;
      }},
    { title: '操作', dataIndex: 'action', width: 140, editable: false,
      render: (_, r) => {
        const ci = cm[r.concept] || {};
        const actions = ci.actions || [];
        return <Select value={r.action || (actions[0]?.name || '')} style={{ width: '100%' }}
          options={actions.map(a => ({ value: a.name, label: a.label || a.name }))}
          onChange={v => updConfig(nc => { const e = nc.systems?.[sysName]?.endpoints?.[r._idx]; if (e) e.action = v; })} />;
      }},
    { title: '方法', dataIndex: 'method', width: 70, editable: false,
      render: (_, r) => <Select value={r.method || 'GET'} style={{ width: '100%' }}
        onChange={v => updConfig(nc => { const e = nc.systems?.[sysName]?.endpoints?.[r._idx]; if (e) e.method = v; })}>
        <Select.Option value='GET'>GET</Select.Option>
        <Select.Option value='POST'>POST</Select.Option>
        <Select.Option value='PUT'>PUT</Select.Option>
      </Select>},
    { title: '路径', dataIndex: 'path', editable: true },
    { title: '测试', width: 70, editable: false,
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
    { title: '操作', width: 50, editable: false,
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
      <ProTable
        actionRef={actionRef}
        columns={columns}
        rowKey='id'
        search={false}
        options={false}
        pagination={false}
        dataSource={eps}
        onChange={handleChange}
        ghost
        locale={{ emptyText: '暂无接口' }}
        toolbar={{
          actions: [
            <Select key='add' style={{ width: 200 }} placeholder='+ 添加接口' value={undefined} showSearch
              filterOption={(input, option) => (option?.label || '').toLowerCase().includes(input.toLowerCase())}
              options={allConcepts.map(c => {
                const s = (skillData?.skills || []).find(x => x.concept === c);
                return { value: c, label: s?.concept_label || c };
              })}
              onChange={val => {
                const cur = ((config.systems || {})[sysName]?.endpoints || []);
                if (!cur.find(e => e.concept === val)) {
                  updConfig(nc => {
                    const sys = nc.systems?.[sysName]; if (!sys) return;
                    sys.endpoints = [...(sys.endpoints || []), { concept: val, action: '', method: 'GET', path: '', enabled: true,
                      pageParam: '', sizeParam: '', sortParam: '', orderParam: '', params: [],
                      response: { type: 'array', root: '', fields: [], format: 'json', errorField: '', totalField: '',
                        successConditions: [{ type: 'http', field: 'status', operator: 'eq', value: '200' }] } }];
                  });
                }
              }} />,
          ],
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
                  <Space size={4} wrap style={{ marginBottom: 6 }}>
                    <Text style={{ fontSize: 10 }}>分页:</Text>
                    <Input style={{ width: 80 }} placeholder='页码' value={ep.pageParam || ''} onChange={e => update('pageParam', e.target.value)} />
                    <Input style={{ width: 80 }} placeholder='每页数' value={ep.sizeParam || ''} onChange={e => update('sizeParam', e.target.value)} />
                    <Text style={{ fontSize: 10 }}>排序:</Text>
                    <Input style={{ width: 80 }} placeholder='排序字段' value={ep.sortParam || ''} onChange={e => update('sortParam', e.target.value)} />
                    <Input style={{ width: 80 }} placeholder='排序方式' value={ep.orderParam || ''} onChange={e => update('orderParam', e.target.value)} />
                  </Space>
                  <EditableParamTable params={ep.params || []} sk={sk} sysName={sysName} idx={idx} updConfig={updConfig} />
                </DetailSection>
                <DetailSection title='响应配置' onAdd={() => updConfig(nc => {
                  const e = nc.systems?.[sysName]?.endpoints?.[idx];
                  if (e) { e.response = e.response || {}; e.response.fields = [...(e.response.fields || []), { apiName: '', name: '' }]; }
                })}>
                  <SuccessConditions conds={ep.response?.successConditions || [{ type: 'http', field: 'status', operator: 'eq', value: '200' }]}
                    sysName={sysName} idx={idx} updConfig={updConfig} />
                  <Space size={4} wrap style={{ marginBottom: 6 }}>
                    <Text style={{ fontSize: 10 }}>错误:</Text>
                    <Input style={{ width: 100 }} placeholder='字段名' value={ep.response?.errorField || ''}
                      onChange={e => updConfig(nc => { const e = nc.systems?.[sysName]?.endpoints?.[idx]; if (e) { e.response = e.response || {}; e.response.errorField = e.target.value; } })} />
                    <Text style={{ fontSize: 10 }}>格式:</Text>
                    <Select style={{ width: 80 }} value={ep.response?.format || 'json'}
                      onChange={v => updConfig(nc => { const e = nc.systems?.[sysName]?.endpoints?.[idx]; if (e) { e.response = e.response || {}; e.response.format = v; } })}>
                      <Select.Option value='json'>JSON</Select.Option><Select.Option value='xml'>XML</Select.Option></Select>
                    <Text style={{ fontSize: 10 }}>数据路径:</Text>
                    <Input style={{ width: 120 }} placeholder='data.items' value={ep.response?.root || ''}
                      onChange={e => updConfig(nc => { const e = nc.systems?.[sysName]?.endpoints?.[idx]; if (e) { e.response = e.response || {}; e.response.root = e.target.value; } })} />
                    <Text style={{ fontSize: 10 }}>总数:</Text>
                    <Input style={{ width: 100 }} placeholder='total' value={ep.response?.totalField || ''}
                      onChange={e => updConfig(nc => { const e = nc.systems?.[sysName]?.endpoints?.[idx]; if (e) { e.response = e.response || {}; e.response.totalField = e.target.value; } })} />
                  </Space>
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
  const [form] = Form.useForm();
  const handleChange = (data) => updConfig(nc => {
    const e = nc.systems?.[sysName]?.endpoints?.[idx];
    if (e) e.params = data.map(({ id, ...p }) => p);
  });
  const outputOpts = (sk?.output_fields || []).map(f => ({ value: f.name, label: f.label || f.name }));
  const apiOpts = (sk?.output_fields || []).map(f => ({ value: f.name, label: f.name }));

  const columns = [
    { title: '属性名', dataIndex: 'name', width: 130, valueType: 'select', fieldProps: { showSearch: true },
      formItemProps: { rules: [] },
      renderFormItem: () => <Select placeholder='选择' showSearch
        filterOption={(input, option) => (option?.label || '').includes(input)}
        options={outputOpts} /> },
    { title: '接口参数', dataIndex: 'apiName', width: 120, valueType: 'select', fieldProps: { allowClear: true },
      formItemProps: { rules: [] },
      renderFormItem: () => <Select placeholder='输入或选择' showSearch allowClear
        filterOption={(input, option) => (option?.label || '').includes(input)}
        options={apiOpts} /> },
    { title: '类型', dataIndex: 'type', width: 70, valueType: 'select',
      fieldProps: { options: [{ value: 'string', label: '字符串' }, { value: 'integer', label: '整数' }, { value: 'number', label: '小数' }, { value: 'boolean', label: '布尔' }] },
      formItemProps: { rules: [] } },
    { title: '位置', dataIndex: 'in', width: 70, valueType: 'select',
      fieldProps: { options: [{ value: 'query', label: 'Query' }, { value: 'body', label: 'Body' }] },
      formItemProps: { rules: [] } },
    { title: '', dataIndex: 'option', width: 40, valueType: 'option' },
  ];

  return (
    <ProTable
      columns={columns}
      rowKey={(_, i) => i}
      search={false} options={false} pagination={false} ghost
      dataSource={params.map((p, i) => ({ ...p, id: i }))}
      onChange={handleChange}
      locale={{ emptyText: '无参数' }}
      editable={{
        type: 'multiple', form,
        onSave: async () => true,
        onDelete: async () => true,
        actionRender: (_, __, dom) => [dom.save, dom.delete],
        recordCreatorProps: { creatorButtonText: '添加参数', record: () => ({ name: '', apiName: '', type: 'string', in: 'query' }) },
      }}
    />
  );
}

function SuccessConditions({ conds, sysName, idx, updConfig }) {
  const addOne = () => updConfig(nc => {
    const e = nc.systems?.[sysName]?.endpoints?.[idx];
    if (e) { e.response = e.response || {}; e.response.successConditions = [...(e.response.successConditions || []), { type: 'http', field: 'status', operator: 'eq', value: '200' }]; }
  });
  return (
    <div style={{ marginBottom: 8 }}>
      <Text style={{ fontSize: 12, display: 'block', marginBottom: 6 }}>成功条件（全部满足）</Text>
      {conds.map((cond, cIdx) => (
        <Space key={cIdx} size={4} style={{ marginBottom: 2 }}>
          <Select style={{ width: 80 }} value={cond.type || 'http'}
            onChange={v => updConfig(nc => {
              const arr = nc.systems?.[sysName]?.endpoints?.[idx]?.response?.successConditions || [];
              if (arr[cIdx]) arr[cIdx].type = v;
            })}>
            <Select.Option value='http'>HTTP</Select.Option>
            <Select.Option value='field'>字段</Select.Option>
          </Select>
          <Select style={{ width: 70 }} value={cond.operator || 'eq'}
            onChange={v => updConfig(nc => {
              const arr = nc.systems?.[sysName]?.endpoints?.[idx]?.response?.successConditions || [];
              if (arr[cIdx]) arr[cIdx].operator = v;
            })}>
            {cond.type === 'field' && <Select.Option value='exists'>存在</Select.Option>}
            <Select.Option value='eq'>=</Select.Option>
            <Select.Option value='gte'>&gt;=</Select.Option>
            <Select.Option value='lte'>&lt;=</Select.Option>
          </Select>
          <Input style={{ width: cond.type === 'http' ? 60 : 100 }} placeholder={cond.type === 'http' ? '状态码' : '字段路径'}
            value={cond.field || ''} onChange={e => updConfig(nc => {
              const arr = nc.systems?.[sysName]?.endpoints?.[idx]?.response?.successConditions || [];
              if (arr[cIdx]) arr[cIdx].field = e.target.value;
            })} />
          {cond.operator !== 'exists' && (
            <Input style={{ width: 80 }} placeholder='值' value={cond.value || ''} onChange={e => updConfig(nc => {
              const arr = nc.systems?.[sysName]?.endpoints?.[idx]?.response?.successConditions || [];
              if (arr[cIdx]) arr[cIdx].value = e.target.value;
            })} />
          )}
          <Button size='small' type='text' danger icon={<DeleteOutlined />} onClick={() => updConfig(nc => {
            nc.systems?.[sysName]?.endpoints?.[idx]?.response?.successConditions?.splice(cIdx, 1);
          })} />
        </Space>
      ))}
      <Button size='small' type='dashed' icon={<PlusOutlined />} onClick={addOne} block style={{ marginTop: 4 }}>添加条件</Button>
    </div>
  );
}

function RespFieldTable({ fields, sk, sysName, epIdx, updConfig, testFields }) {
  return (
    <Table size='small' pagination={false} rowKey='__idx2' dataSource={fields.map((f, i) => ({ ...f, __idx2: i }))}
      locale={{ emptyText: '无映射' }}
      columns={[
        { title: '接口字段', width: 130, render: (v, _, fIdx) => {
          const cacheKey = `${sysName}_${epIdx}`;
          const cached = testFields[cacheKey] || [];
          return <Select value={v || undefined} placeholder={cached.length > 0 ? '选择' : '先点▶测试'} style={{ width: '100%' }} showSearch allowClear
            filterOption={(input, option) => (option?.label || '').toLowerCase().includes(input.toLowerCase())}
            options={cached.map(f => ({ value: f, label: f }))}
            onChange={val => updConfig(nc => {
              const e = nc.systems?.[sysName]?.endpoints?.[epIdx];
              if (e?.response?.fields?.[fIdx]) e.response.fields[fIdx].apiName = val || '';
            })} />;
        }},
        { title: '→ 本体属性', width: 130, render: (v, _, fIdx) => {
          const opts = (sk?.output_fields || []).map(f => ({ value: f.name, label: f.label || f.name }));
          return <Select value={v || undefined} placeholder='选择' style={{ width: '100%' }} showSearch
            filterOption={(input, option) => (option?.label || '').toLowerCase().includes(input.toLowerCase())}
            options={opts} onChange={val => updConfig(nc => {
              const e = nc.systems?.[sysName]?.endpoints?.[epIdx];
              if (e?.response?.fields?.[fIdx]) e.response.fields[fIdx].name = val;
            })} />;
        }},
        { title: '', width: 40, render: (_, __, fIdx) => <Button size='small' type='text' danger icon={<DeleteOutlined />} onClick={() => updConfig(nc => {
          nc.systems?.[sysName]?.endpoints?.[epIdx]?.response?.fields?.splice(fIdx, 1);
        })} /> },
      ]} />
  );
}
