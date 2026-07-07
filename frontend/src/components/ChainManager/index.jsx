import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import {
  Button, Table, Drawer, Form, Input, Select, Switch, Space, Tag, Popconfirm, Radio,
  message, Empty, Tabs, ColorPicker, Spin, Tree, Typography, TreeSelect, Card,
} from 'antd';

const { Text } = Typography;
import {
  PlusOutlined, EditOutlined, DeleteOutlined, ReloadOutlined,
  ArrowLeftOutlined, LinkOutlined, RobotOutlined, ApiOutlined,
  DashboardOutlined, AlertOutlined, ControlOutlined, CloudServerOutlined,
} from '@ant-design/icons';
import request from '../../services/request';

const AGENT_COLORS = {
  analysis_monitor: '#6c5ce7',
  quality_equipment: '#00b894',
  production_management: '#fdcb6e',
  production_execution: '#0984e3',
  warehouse_logistics: '#e17055',
};

const TRIGGER_EXAMPLES = {
  fault_diagnosis: ['故障.*诊断', '设备.*故障', '设备.*坏', '设备.*异常', '停机.*原因', '诊断.*故障'],
  quality_analysis: ['质量.*分析', '缺陷.*分析', '不良.*分析', '质检.*分析', '质量.*改善', '质量.*改进'],
  work_order_readiness: ['生产准备', '投产准备', '齐套检查', '开工检查', '准备检查', '工单.*准备'],
  production_report: ['生产.*报告', '综合.*报告', '生产.*总结', '车间.*报告', '产线.*报告', '综合分析.*生产'],
};

const TRIGGER_PRESET_NAMES = {
  fault_diagnosis: '设备故障诊断',
  quality_analysis: '质量分析',
  work_order_readiness: '工单准备检查',
  production_report: '生产综合报告',
};

const TEMPLATE_PRESETS = {
  daily: `根据以下实时KPI数据输出一份生产综合日报。

## 数据摘要
{data_context}

## 用户问题
{message}

## 输出要求
先写一段3句以内的整体概览（用🔴🟡🟢标记风险等级）。
然后分三个小节，每节用 Markdown 表格列出3条发现：
### 📊 排产进度
### 🔍 质量状况
### ⚙️ 设备/安灯
最后 ### 📋 今日行动项，按 P0/P1/P2 优先级列出3-5条。
语言简洁专业，像日报不是论文。`,

  diagnosis: `根据以下实时数据诊断问题并给出处理建议。

## 数据摘要
{data_context}

## 用户问题
{message}

## 输出要求
先一句话概述核心问题。然后分三个小节：
### 🔧 问题诊断（可能原因、严重程度、排查方向）
### 📦 资源状态（备件/物料库存，标注不足项）
### 📅 影响评估（受影响工单、预计停机、替代方案）
最后 ### 📋 处理步骤，按 P0/P1/P2 列出，含预期时间线。`,

  readiness: `根据以下实时数据检查准备工作并给出综合判断。

## 数据摘要
{data_context}

## 用户问题
{message}

## 输出要求
用结构化清单输出，每项用 ✅/⚠️/🔴 标注：
### 📦 物料齐套（到位率、缺料明细）
### ⚙️ 设备状态（可用率、维保中）
### 🔬 质检标准（关键项目、判定标准）
### 📋 SOP就绪（版本、培训状态）
最后给出综合结论：「可投产」/「条件投产」/「不可投产」。`,
};

export default function ChainManager({ onBack }) {
  const [activeTab, setActiveTab] = useState('chains');
  const [chainDrawerOpen, setChainDrawerOpen] = useState(false);
  const [editingChain, setEditingChain] = useState(null);
  const [chainDrawerKey, setChainDrawerKey] = useState(0);
  const [chainsRefreshKey, setChainsRefreshKey] = useState(0);

  const [agentsForDrawer, setAgentsForDrawer] = useState([]);

  useEffect(() => {
    request.get('/chains/agents/list').then(data => {
      setAgentsForDrawer(Array.isArray(data) ? data : []);
    }).catch(() => {});
  }, []);

  const handleEditChain = (chain) => {
    setEditingChain(chain);
    setChainDrawerKey(k => k + 1);
    setChainDrawerOpen(true);
  };
  const handleChainsSaved = () => {
    setChainDrawerOpen(false);
    setChainsRefreshKey(k => k + 1);
  };

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column', background: '#fff' }}>
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '12px 20px', borderBottom: '1px solid #f0f0f0', background: '#fff',
      }}>
        <Space>
          <Button type="text" icon={<ArrowLeftOutlined />} onClick={onBack} style={{ fontSize: 16 }}>
            返回对话
          </Button>
          <span style={{ fontSize: 16, fontWeight: 600, color: '#1a1a2e' }}>系统配置</span>
        </Space>
      </div>

      <Tabs
        activeKey={activeTab}
        onChange={setActiveTab}
        style={{ flex: 1, overflow: 'hidden' }}
        tabBarStyle={{ padding: '0 20px', marginBottom: 0 }}
        items={[
          { key: 'chains', label: <span><LinkOutlined />链条配置</span>,
            children: <div style={{ height: 'calc(100vh - 120px)', overflow: 'auto', padding: 20 }}><ChainsTab key={chainsRefreshKey} onEditChain={handleEditChain} drawerOpen={chainDrawerOpen} editingChain={editingChain} formKey={chainDrawerKey} onDrawerClose={handleChainsSaved} onDrawerSaved={handleChainsSaved} agents={agentsForDrawer} /></div> },
          { key: 'systems', label: <span><CloudServerOutlined />数据源</span>,
            children: <div style={{ height: 'calc(100vh - 120px)', overflow: 'auto', padding: 20 }}><SystemsTab /></div> },
          { key: 'skills', label: <span><ApiOutlined />Skill 目录</span>,
            children: <div style={{ height: 'calc(100vh - 120px)', overflow: 'auto', padding: 20 }}><SkillsTab /></div> },
          { key: 'agents', label: <span><ControlOutlined />业务域配置</span>,
            children: <div style={{ height: 'calc(100vh - 120px)', overflow: 'auto', padding: 20 }}><AgentConfigTab onSwitchTab={setActiveTab} onEditChain={handleEditChain} /></div> },
          { key: 'mcp', label: <span><ApiOutlined />MCP 服务器</span>,
            children: <div style={{ height: 'calc(100vh - 120px)', overflow: 'auto', padding: 20 }}><MCPServersTab /></div> },
          { key: 'a2a', label: <span><RobotOutlined />外部 Agent</span>,
            children: <div style={{ height: 'calc(100vh - 120px)', overflow: 'auto', padding: 20 }}><A2AAgentsTab /></div> },
          { key: 'kpi', label: <span><DashboardOutlined />KPI 指标</span>,
            children: <div style={{ height: 'calc(100vh - 120px)', overflow: 'auto', padding: 20 }}><KPIAdminTab /></div> },
          { key: 'explorer_rules', label: <span><AlertOutlined />检测规则</span>,
            children: <div style={{ height: 'calc(100vh - 120px)', overflow: 'auto', padding: 20 }}><ExplorerRulesTab /></div> },
          { key: 'resources', label: <span><ControlOutlined />资源阈值</span>,
            children: <div style={{ height: 'calc(100vh - 120px)', overflow: 'auto', padding: 20 }}><ResourceThresholdsTab /></div> },
        ]}
      />
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════
// Chains Tab
// ═══════════════════════════════════════════════════════════════════

function ChainsTab({ onEditChain, drawerOpen: extDrawerOpen, editingChain: extEditingChain, formKey: extFormKey, onDrawerClose, onDrawerSaved, agents: externalAgents }) {
  const [chains, setChains] = useState([]);
  const [localAgents, setLocalAgents] = useState([]);
  const [loading, setLoading] = useState(false);
  const [localDrawerOpen, setLocalDrawerOpen] = useState(false);
  const [localEditingChain, setLocalEditingChain] = useState(null);
  const [localFormKey, setLocalFormKey] = useState(0);

  const useExternal = !!onEditChain;
  const drawerOpen = useExternal ? extDrawerOpen : localDrawerOpen;
  const editingChain = useExternal ? extEditingChain : localEditingChain;
  const formKey = useExternal ? extFormKey : localFormKey;
  const agents = externalAgents?.length ? externalAgents : localAgents;

  const loadChains = useCallback(async () => {
    setLoading(true);
    try {
      const data = await request.get('/chains');
      setChains(Array.isArray(data) ? data : []);
    } catch { message.error('加载失败'); }
    finally { setLoading(false); }
  }, []);

  const loadAgents = useCallback(async () => {
    try {
      const data = await request.get('/chains/agents/list');
      setLocalAgents(Array.isArray(data) ? data : []);
    } catch { /* silent */ }
  }, []);

  useEffect(() => { loadChains(); loadAgents(); }, [loadChains, loadAgents]);

  const handleCreate = () => {
    if (useExternal) { onEditChain(null); return; }
    setLocalEditingChain(null); setLocalFormKey(k => k + 1); setLocalDrawerOpen(true);
  };
  const handleEdit = (chain) => {
    if (useExternal) { onEditChain(chain); return; }
    setLocalEditingChain(chain); setLocalFormKey(k => k + 1); setLocalDrawerOpen(true);
  };
  const handleClose = () => {
    if (useExternal) { onDrawerClose(); return; }
    setLocalDrawerOpen(false);
  };
  const handleSaved = () => {
    if (useExternal) { onDrawerSaved(); return; }
    setLocalDrawerOpen(false); loadChains();
  };

  const handleDelete = async (chainId) => {
    try { await request.delete(`/chains/${encodeURIComponent(chainId)}`); message.success('已删除'); loadChains(); }
    catch { message.error('删除失败'); }
  };

  const handleReload = async () => {
    try { await request.post('/chains/reload'); message.success('缓存已刷新'); }
    catch { message.error('刷新失败'); }
  };

  const columns = [
    { title: '链条ID', dataIndex: 'chain_id', width: 170, render: t => <code style={{ fontSize: 12, color: '#6c5ce7' }}>{t}</code> },
    { title: '名称', dataIndex: 'name', width: 140 },
    { title: '描述', dataIndex: 'description', ellipsis: true },
    { title: '模式', key: 'mode', width: 80, align: 'center', render: (_, r) => ((r.steps || []).length > 0 ? <Tag color="purple">链式</Tag> : <Tag color="blue">合并</Tag>) },
    { title: '启用', dataIndex: 'enabled', width: 60, align: 'center', render: v => <Tag color={v ? 'green' : 'default'}>{v ? '是' : '否'}</Tag> },
    { title: '操作', key: 'actions', width: 100, render: (_, r) => (
      <Space>
        <Button size="small" icon={<EditOutlined />} onClick={() => handleEdit(r)} />
        <Popconfirm title="确定删除？" onConfirm={() => handleDelete(r.chain_id)}>
          <Button size="small" danger icon={<DeleteOutlined />} />
        </Popconfirm>
      </Space>
    )},
  ];

  return (
    <>
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between' }}>
        <Button icon={<ReloadOutlined />} onClick={handleReload}>刷新缓存</Button>
        <Button type="primary" icon={<PlusOutlined />} onClick={handleCreate}>新建链条</Button>
      </div>
      <Table columns={columns} dataSource={chains} rowKey="chain_id" loading={loading}
        size="middle" pagination={false}
        locale={{ emptyText: <Empty description="暂无链条配置" /> }} />

      <ChainDrawer
        key={formKey}
        open={drawerOpen}
        editingChain={editingChain}
        agents={agents}
        onClose={handleClose}
        onSaved={handleSaved}
      />
    </>
  );
}

// ═══════════════════════════════════════════════════════════════════
// 数据源 Tab — API 数据源配置
// ═══════════════════════════════════════════════════════════════════

function SystemsTab() {
  const [config, setConfig] = useState(null);
  const [loading, setLoading] = useState(false);
  const [skillData, setSkillData] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [sysRes, statusRes] = await Promise.all([
        request.get('/chains/compile/systems'),
        request.get('/chains/compile/status'),
      ]);
      if (sysRes.ok) setConfig(sysRes.config);
      if (statusRes.ok) setSkillData(statusRes);
    } catch { message.error('加载失败'); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); }, [load]);

  const save = async (newConfig) => {
    try { await request.put('/chains/compile/systems', { config: newConfig }); setConfig(newConfig); }
    catch { message.error('保存失败'); }
  };

  const updEndpoint = (sysName, idx, field, value) => {
    const nc = JSON.parse(JSON.stringify(config));
    nc.systems[sysName].endpoints[idx][field] = value;
    setConfig(nc); save(nc);
  };

  const addEndpoint = (sysName, concept) => {
    const nc = JSON.parse(JSON.stringify(config));
    if (!nc.systems[sysName]) nc.systems[sysName] = { type: 'api', endpoints: [] };
    if (!nc.systems[sysName].endpoints.find(e => e.concept === concept)) {
      nc.systems[sysName].endpoints.push({ concept, action: '', method: 'GET', format: 'json', path: '', params: [], response: { type: 'array', root: '', fields: [] } });
      setConfig(nc); save(nc);
    }
  };

  const removeEndpoint = (sysName, idx) => {
    const nc = JSON.parse(JSON.stringify(config));
    nc.systems[sysName].endpoints.splice(idx, 1);
    setConfig(nc); save(nc);
  };

  const addParam = (sysName, epIdx) => {
    const nc = JSON.parse(JSON.stringify(config));
    const ep = nc.systems[sysName].endpoints[epIdx];
    ep.params.push({ name: '', apiName: '', type: 'string', in: 'query', required: false });
    setConfig(nc); save(nc);
  };

  const updParam = (sysName, epIdx, pIdx, field, value) => {
    const nc = JSON.parse(JSON.stringify(config));
    nc.systems[sysName].endpoints[epIdx].params[pIdx][field] = value;
    setConfig(nc); save(nc);
  };

  const removeParam = (sysName, epIdx, pIdx) => {
    const nc = JSON.parse(JSON.stringify(config));
    nc.systems[sysName].endpoints[epIdx].params.splice(pIdx, 1);
    setConfig(nc); save(nc);
  };

  const addRespField = (sysName, epIdx) => {
    const nc = JSON.parse(JSON.stringify(config));
    const ep = nc.systems[sysName].endpoints[epIdx];
    if (!ep.response) ep.response = { type: 'array', root: '', fields: [] };
    ep.response.fields.push({ apiName: '', name: '' });
    setConfig(nc); save(nc);
  };

  const updRespField = (sysName, epIdx, fIdx, field, value) => {
    const nc = JSON.parse(JSON.stringify(config));
    nc.systems[sysName].endpoints[epIdx].response.fields[fIdx][field] = value;
    setConfig(nc); save(nc);
  };

  const removeRespField = (sysName, epIdx, fIdx) => {
    const nc = JSON.parse(JSON.stringify(config));
    nc.systems[sysName].endpoints[epIdx].response.fields.splice(fIdx, 1);
    setConfig(nc); save(nc);
  };

  if (!config || !skillData) return <Spin style={{ display: 'block', margin: '60px auto' }} />;

  const systems = config.systems || {};
  const allConcepts = (skillData.skills || []).map(s => s.concept).filter(Boolean);
  const assigned = new Set();
  Object.values(systems).forEach(cfg => (cfg.endpoints || []).forEach(e => assigned.add(e.concept)));

  return (
    <div>
      <div style={{ marginBottom: 16 }}>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
          <Button type="dashed" icon={<PlusOutlined />} onClick={() => {
            const nc = JSON.parse(JSON.stringify(config));
            nc.systems[`system_${Date.now()}`] = { type: 'api', baseUrl: '', authType: 'bearer', endpoints: [] };
            setConfig(nc); save(nc);
          }}>添加数据源</Button>
        </Space>
      </div>

      {Object.entries(systems).map(([sysName, cfg]) => {
        const eps = cfg.endpoints || [];
        return (
          <Card key={sysName} size="small" style={{ marginBottom: 16 }}
            title={
              <Space>
                <CloudServerOutlined />
                <Input size="small" style={{ width: 120, fontWeight: 600 }} value={sysName}
                  onBlur={e => { if (e.target.value !== sysName) { const nc = JSON.parse(JSON.stringify(config)); nc.systems[e.target.value] = nc.systems[sysName]; delete nc.systems[sysName]; setConfig(nc); save(nc); } }} />
                <Select size="small" value={cfg.type || 'api'} style={{ width: 70 }}
                  onChange={v => { const nc = JSON.parse(JSON.stringify(config)); nc.systems[sysName].type = v; setConfig(nc); save(nc); }}
                  options={[{ value: 'api', label: 'API' }, { value: 'neo4j', label: 'Neo4j' }]} />
              </Space>
            } extra={
              <Popconfirm title="确定删除?" onConfirm={() => { const nc = JSON.parse(JSON.stringify(config)); delete nc.systems[sysName]; setConfig(nc); save(nc); }}>
                <Button size="small" danger icon={<DeleteOutlined />} />
              </Popconfirm>
            }>
            {/* URL + Auth */}
            <Space direction="vertical" size={4} style={{ width: '100%' }}>
              <Input addonBefore={<span style={{ fontSize: 11 }}>Base URL</span>} size="small"
                value={cfg.baseUrl || ''} placeholder="https://api.company.com"
                onChange={e => { const nc = JSON.parse(JSON.stringify(config)); nc.systems[sysName].baseUrl = e.target.value; setConfig(nc); save(nc); }} />
              <Space size={4}>
                <Select size="small" value={cfg.authType || 'bearer'} style={{ width: 90 }}
                  onChange={v => { const nc = JSON.parse(JSON.stringify(config)); nc.systems[sysName].authType = v; setConfig(nc); save(nc); }}>
                  <Select.Option value="bearer">Bearer</Select.Option>
                  <Select.Option value="apikey">API Key</Select.Option>
                  <Select.Option value="basic">Basic</Select.Option>
                </Select>
                <Input size="small" placeholder="Token" style={{ flex: 1 }} value={cfg.authConfig?.token || ''}
                  onChange={e => { const nc = JSON.parse(JSON.stringify(config)); nc.systems[sysName].authConfig = { token: e.target.value }; setConfig(nc); save(nc); }} />
              </Space>
            </Space>

            {/* 接口列表 — Postman 风格 */}
            <div style={{ marginTop: 12 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                <Text strong style={{ fontSize: 12 }}>接口 ({eps.length})</Text>
                <Select size="small" style={{ width: 200 }} placeholder="+ 添加接口" value={undefined}
                  showSearch
                  filterOption={(input, option) => (option?.label || '').toLowerCase().includes(input.toLowerCase())}
                  options={allConcepts.map(c => {
                    const s = (skillData?.skills || []).find(x => x.concept === c);
                    return { value: c, label: `${s?.concept_label || c}` };
                  })}
                  onChange={val => addEndpoint(sysName, val)} />
              </div>

              <Table size="small" pagination={false} rowKey="concept"
                dataSource={eps.map((ep, i) => ({ ...ep, _idx: i }))}
                locale={{ emptyText: '暂无接口，点击上方下拉添加' }}
                expandable={{
                  expandedRowRender: (ep) => {
                    const epIdx = ep._idx;
                    return (
                      <div style={{ padding: 8 }}>
                        <div style={{ marginBottom: 8 }}>
                          <Space style={{ width: '100%', justifyContent: 'space-between', marginBottom: 4 }}>
                            <Text type="secondary" style={{ fontSize: 12 }}>请求参数</Text>
                            <Button size="small" type="dashed" icon={<PlusOutlined />} onClick={() => addParam(sysName, epIdx)}>添加</Button>
                          </Space>
                          <Table size="small" pagination={false} rowKey="__idx" dataSource={(ep.params || []).map((p, i) => ({ ...p, __idx: i }))}
                            locale={{ emptyText: '无参数，属性名原样传递' }}
                            columns={[
                              { title: '属性名', dataIndex: 'name', width: 140, render: (v, _, idx) => {
                                const sk = (skillData?.skills || []).find(s => s.concept === ep.concept);
                                const opts = (sk?.output_fields || []).map(f => ({ value: f.name, label: `${f.label || f.name}` }));
                                return <Select size="small" value={v || undefined} placeholder="选择" style={{ width: '100%' }} showSearch
                                  filterOption={(input, option) => (option?.label || '').toLowerCase().includes(input.toLowerCase())}
                                  options={opts} onChange={val => updParam(sysName, epIdx, idx, 'name', val)} />;
                              }},
                              { title: '接口参数', dataIndex: 'apiName', width: 130, render: (v, _, idx) => {
                                const sk = (skillData?.skills || []).find(s => s.concept === ep.concept);
                                const opts = (sk?.output_fields || []).map(f => ({ value: f.name, label: f.name }));
                                return <Select size="small" value={v || undefined} placeholder="输入或选择"
                                  style={{ width: '100%' }} showSearch allowClear
                                  filterOption={(input, option) => (option?.label || '').toLowerCase().includes(input.toLowerCase())}
                                  options={opts}
                                  onChange={val => updParam(sysName, epIdx, idx, 'apiName', val || '')} />;
                              }},
                              { title: '类型', dataIndex: 'type', width: 70, render: (v, _, idx) => <Select size="small"  value={v || 'string'} onChange={v2 => updParam(sysName, epIdx, idx, 'type', v2)} style={{ width: '100%' }}><Select.Option value="string">字符串</Select.Option><Select.Option value="integer">整数</Select.Option><Select.Option value="number">小数</Select.Option><Select.Option value="boolean">布尔</Select.Option></Select> },
                              { title: '位置', dataIndex: 'in', width: 70, render: (v, _, idx) => <Select size="small"  value={v || 'query'} onChange={v2 => updParam(sysName, epIdx, idx, 'in', v2)} style={{ width: '100%' }}><Select.Option value="query">Query</Select.Option><Select.Option value="body">Body</Select.Option></Select> },
                              { title: '', width: 40, render: (_, __, idx) => <Button size="small" type="text" danger icon={<DeleteOutlined />} onClick={() => removeParam(sysName, epIdx, idx)} /> },
                            ]} />
                        </div>
                        <div>
                          <Space style={{ width: '100%', justifyContent: 'space-between', marginBottom: 4 }}>
                            <Text type="secondary" style={{ fontSize: 12 }}>响应映射</Text>
                            <Space size={4}>
                              <Text style={{ fontSize: 10, color: '#999' }}>根路径:</Text>
                              <Input size="small" style={{ width: 80 }} placeholder="如 data" value={ep.response?.root || ''}
                                onChange={e => { const nc = JSON.parse(JSON.stringify(config)); if (!nc.systems[sysName].endpoints[epIdx].response) nc.systems[sysName].endpoints[epIdx].response = {}; nc.systems[sysName].endpoints[epIdx].response.root = e.target.value; setConfig(nc); save(nc); }} />
                              <Button size="small" type="dashed" icon={<PlusOutlined />} onClick={() => addRespField(sysName, epIdx)}>添加</Button>
                            </Space>
                          </Space>
                          <Table size="small" pagination={false} rowKey="__idx2" dataSource={(ep.response?.fields || []).map((f, i) => ({ ...f, __idx2: i }))}
                            locale={{ emptyText: '无映射，接口字段名原样保留' }}
                            columns={[
                              { title: '接口字段', dataIndex: 'apiName', render: (v, _, idx) => {
                                const s3 = (skillData?.skills || []).find(s => s.concept === ep.concept);
                                const o3 = (s3?.output_fields || []).map(f => ({ value: f.name, label: f.name }));
                                return <Select size="small" value={v || undefined} placeholder="输入或选择"
                                  style={{ width: '100%' }} showSearch allowClear
                                  filterOption={(input, option) => (option?.label || '').toLowerCase().includes(input.toLowerCase())}
                                  options={o3} onChange={val => updRespField(sysName, epIdx, idx, 'apiName', val || '')} />;
                              }},
                              { title: '→ 本体属性', dataIndex: 'name', render: (v, _, idx) => {
                                const sk2 = (skillData?.skills || []).find(s => s.concept === ep.concept);
                                const opts2 = (sk2?.output_fields || []).map(f => ({ value: f.name, label: `${f.label || f.name}` }));
                                return <Select size="small" value={v || undefined} placeholder="选择" style={{ width: '100%' }} showSearch
                                  filterOption={(input, option) => (option?.label || '').toLowerCase().includes(input.toLowerCase())}
                                  options={opts2} onChange={val => updRespField(sysName, epIdx, idx, 'name', val)} />;
                              }},
                              { title: '', width: 40, render: (_, __, idx) => <Button size="small" type="text" danger icon={<DeleteOutlined />} onClick={() => removeRespField(sysName, epIdx, idx)} /> },
                            ]} />
                        </div>
                      </div>
                    );
                  },
                }}
                columns={[
                  { title: '概念', width: 90, render: (_, ep) => {
                    const s = (skillData?.skills || []).find(x => x.concept === ep.concept);
                    return <Tag color="green">{s?.concept_label || ep.concept}</Tag>;
                  }},
                  { title: '操作', width: 100, render: (_, ep) => (
                    <Input size="small" value={ep.action || ''} placeholder="操作名"
                      onChange={e => updEndpoint(sysName, ep._idx, 'action', e.target.value)} />
                  )},
                  { title: '方法', width: 72, render: (_, ep) => (
                    <Select size="small" value={ep.method || 'GET'} style={{ width: '100%' }}
                      onChange={v => updEndpoint(sysName, ep._idx, 'method', v)}>
                      <Select.Option value="GET">GET</Select.Option>
                      <Select.Option value="POST">POST</Select.Option>
                      <Select.Option value="PUT">PUT</Select.Option>
                    </Select>
                  )},
                  { title: '格式', width: 72, render: (_, ep) => (
                    <Select size="small" value={ep.format || 'json'} style={{ width: '100%' }}
                      onChange={v => updEndpoint(sysName, ep._idx, 'format', v)}>
                      <Select.Option value="json">JSON</Select.Option>
                      <Select.Option value="form">Form</Select.Option>
                    </Select>
                  )},
                  { title: '路径', render: (_, ep) => (
                    <Input size="small" value={ep.path || ''}
                      placeholder={`/api/${(ep.concept || '').toLowerCase()}`}
                      onChange={e => updEndpoint(sysName, ep._idx, 'path', e.target.value)} />
                  )},
                  { title: '', width: 40, render: (_, ep) => (
                    <Button size="small" type="text" danger icon={<DeleteOutlined />}
                      onClick={() => removeEndpoint(sysName, ep._idx)} />
                  )},
                ]} />
            </div>
          </Card>
        );
      })}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════
// Skills Tab
// ═══════════════════════════════════════════════════════════════════

function SkillsTab() {
  const [skills, setSkills] = useState([]);
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState(null);

  const loadSkills = useCallback(async () => {
    setLoading(true);
    try {
      const data = await request.get('/chains/compile/status');
      setStatus(data);
      setSkills(data.skills || []);
    } catch { /* silent */ }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { loadSkills(); }, [loadSkills]);

  const dsColors = { neo4j: 'blue', api: 'green', db: 'orange' };

  const columns = [
    { title: 'Skill 名', dataIndex: 'display_name', width: 140 },
    { title: '概念', dataIndex: 'concept_label', width: 100,
      render: (t, r) => t || <code style={{ fontSize: 12 }}>{r.concept}</code> },
    { title: '数据源', dataIndex: 'data_source_type', width: 80, align: 'center',
      render: v => <Tag color={dsColors[v] || 'default'}>{v}</Tag> },
    { title: '所属 Agent', dataIndex: 'agent', width: 160, render: t => t ? <Tag>{t}</Tag> : <Tag color="default">-</Tag> },
  ];

  return (
    <>
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={loadSkills}>刷新</Button>
          {status?.ok && (
            <Tag color="green">
              编译时间: {status.compiled_at?.slice(0, 19) || '-'} | {status.concept_count}概念 → {status.skill_count}Skill → {status.agent_count}Agent
            </Tag>
          )}
        </Space>
      </div>
      <Table columns={columns} dataSource={skills} rowKey="name" loading={loading}
        size="small" pagination={{ pageSize: 50 }}
        locale={{ emptyText: <Empty description="暂无 Skill 数据 (编译器是否已运行?)" /> }} />
    </>
  );
}

// ── Chain Drawer (独立组件，打开时才创建 form) ──

function ChainDrawer({ open, editingChain, agents, onClose, onSaved }) {
  const [form] = Form.useForm();
  const [saving, setSaving] = useState(false);
  const watchMode = Form.useWatch('mode', form);
  const [conceptList, setConceptList] = useState([]);

  useEffect(() => {
    if (!open) return;
    request.get('/chains/concepts').then(data => {
      const list = data || [];
      // 构建树结构
      const map = {};
      for (const c of list) map[c.name] = { value: c.name, title: `${c.label || c.name} (${c.name})`, children: [] };
      const roots = [];
      for (const c of list) {
        const node = map[c.name];
        if (c.parents && c.parents.length > 0) {
          const parent = map[c.parents[0]];
          if (parent) parent.children.push(node);
          else roots.push(node);
        } else {
          roots.push(node);
        }
      }
      setConceptList(roots);
    }).catch(() => {});
  }, [open]);

  useEffect(() => {
    if (!open) return;
    if (editingChain) {
      const hasSteps = (editingChain.steps || []).length > 0;
      form.setFieldsValue({
        chain_id: editingChain.chain_id, name: editingChain.name, description: editingChain.description,
        triggers: (editingChain.triggers || []).join('\n'),
        final_prompt_template: editingChain.final_prompt_template || '',
        focus_concepts: editingChain.focus_concepts || '',
        enabled: editingChain.enabled,
        mode: hasSteps ? 'chained' : 'merged',
        steps: editingChain.steps || [],
      });
    } else {
      form.resetFields();
      form.setFieldsValue({ enabled: true, mode: 'merged', steps: [], focus_concepts: '' });
    }
  }, [open, editingChain, form]);

  const handleSave = async () => {
    try {
      const values = await form.validateFields(); setSaving(true);
      const payload = {
        chain_id: values.chain_id, name: values.name || '', description: values.description || '',
        triggers: (values.triggers || '').split('\n').map(s => s.trim()).filter(Boolean),
        final_prompt_template: values.final_prompt_template || '',
        focus_concepts: values.focus_concepts || '',
        enabled: values.enabled,
        steps: (values.steps || []).map((s, i) => ({ ...s, step_order: i })),
      };
      if (editingChain) {
        await request.put(`/chains/${encodeURIComponent(editingChain.chain_id)}`, payload);
        message.success('已更新');
      } else {
        await request.post('/chains', payload);
        message.success('已创建');
      }
      onSaved();
    } catch (err) {
      if (err?.errorFields) return;
      message.error(err?.response?.data?.detail || '保存失败');
    } finally { setSaving(false); }
  };

  const handleTriggerPreset = (k) => {
    if (TRIGGER_EXAMPLES[k]) form.setFieldsValue({ triggers: TRIGGER_EXAMPLES[k].join('\n') });
  };

  return (
    <Drawer
      title={editingChain ? `编辑: ${editingChain.name || editingChain.chain_id}` : '新建链条'}
      open={open}
      onClose={onClose}
      width={720}
      extra={
        <Space>
          <Button onClick={onClose}>取消</Button>
          <Button type="primary" loading={saving} onClick={handleSave}>保存</Button>
        </Space>
      }
    >
      <Form form={form} layout="vertical">
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          <Space.Compact block>
            <Form.Item name="chain_id" label="链条标识" rules={[{ required: true }]} style={{ flex: 1 }}>
              <Input placeholder="英文标识，如 fault_diagnosis" disabled={!!editingChain} />
            </Form.Item>
            <Form.Item name="name" label="显示名称" rules={[{ required: true }]} style={{ flex: 2 }}>
              <Input placeholder="中文名称，如 设备故障诊断" />
            </Form.Item>
          </Space.Compact>
          <Form.Item name="description" label="功能描述">
            <Input.TextArea rows={2} placeholder="简要说明这条链条的用途和触发场景" />
          </Form.Item>
          <Form.Item name="focus_concepts" label="数据范围" help="选择要查询哪些概念的数据。留空则自动从用户消息中提取。"
            getValueFromEvent={(v) => Array.isArray(v) ? v.join(',') : v}
            getValueProps={(v) => ({ value: v ? v.split(',').filter(Boolean) : [] })}>
            <TreeSelect treeData={conceptList} size="small" placeholder="选择概念..."
              treeCheckable showSearch treeNodeFilterProp="title"
              style={{ minWidth: 200 }} maxTagCount={3}
            />
          </Form.Item>
          <Form.Item name="mode" label="推理模式" initialValue="merged"
            help="合并模式：一次 LLM 调用输出完整报告。链式模式：逐步推理，每步输出作为下一步输入。">
            <Radio.Group>
              <Radio.Button value="merged">合并（全景报告）</Radio.Button>
              <Radio.Button value="chained">链式（逐步推理）</Radio.Button>
            </Radio.Group>
          </Form.Item>
          <Form.Item name="enabled" label="启用" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Form.Item name="triggers" label={
            <Space>
              <span>触发条件</span>
              <Space size={4}>
                {Object.keys(TRIGGER_EXAMPLES).map(k => (
                  <Button key={k} size="small" type="dashed" onClick={() => handleTriggerPreset(k)}>{TRIGGER_PRESET_NAMES[k] || k}</Button>
                ))}
              </Space>
            </Space>
          } help="正则表达式，每行一个。用户消息匹配任一即触发。留空则不自动触发。">
            <Input.TextArea rows={4} placeholder="生产.*报告&#10;故障.*诊断" style={{ fontFamily: 'monospace' }} />
          </Form.Item>
          <Form.Item shouldUpdate noStyle>
            {({ getFieldValue }) => {
              const triggers = (getFieldValue('triggers') || '').split('\n').filter(Boolean);
              if (triggers.length === 0) return null;
              return (
                <div style={{ marginTop: -16, marginBottom: 16 }}>
                  <Input.Search size="small" placeholder="输入测试语句，如：帮我生成一份生产报告"
                    enterButton="测试匹配"
                    onSearch={(val) => {
                      const matched = triggers.some(t => { try { return new RegExp(t).test(val) } catch { return false } });
                      const triggersStr = triggers.join(', ');
                      message.info(`${matched ? '✅ 匹配成功！' : '❌ 未匹配'} | 触发词: ${triggersStr}  | 输入: "${val}"`);
                    }}
                  />
                </div>
              );
            }}
          </Form.Item>
          <Form.Item name="final_prompt_template"
            label={
              <Space>
                <span>{watchMode === 'chained' ? '最终汇总提示词' : '分析提示词'}</span>
                {watchMode !== 'chained' && (
                  <Space size={4}>
                    {[
                      { k: 'daily', label: '日报模板' },
                      { k: 'diagnosis', label: '诊断模板' },
                      { k: 'readiness', label: '准备检查' },
                    ].map(p => (
                      <Button key={p.k} size="small" type="link"
                        onClick={() => form.setFieldValue('final_prompt_template', TEMPLATE_PRESETS[p.k])}>
                        {p.label}
                      </Button>
                    ))}
                  </Space>
                )}
              </Space>
            }
            help={watchMode === 'chained'
              ? "固定变量: {message} {data_context}。各推理步骤的 output_key 也可用。"
              : "固定变量: {message} 用户消息、{data_context} 数据查询结果。点击模板快捷填入。"}>
            <Input.TextArea rows={watchMode === 'chained' ? 6 : 10}
              placeholder={watchMode === 'chained'
                ? "步骤1: 设备诊断报告...&#10;&#10;用户消息: {message}&#10;&#10;数据: {data_context}&#10;&#10;请给出诊断结论。"
                : "点击上方「日报模板」快捷填入，或自定义格式。&#10;可用变量：{message} = 用户问题，{data_context} = 查询到的数据"}
              style={{ fontFamily: 'monospace' }} />
          </Form.Item>
          {watchMode === 'chained' && (
          <Form.List name="steps">
            {(fields, { add, remove, move }) => (
              <>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
                  <strong>推理步骤</strong>
                  <Button type="dashed" size="small" icon={<PlusOutlined />} onClick={() => add({ step_id: '', description: '', agent_name: 'analysis_monitor', prompt_template: '', output_key: '' })}>添加步骤</Button>
                </div>
                {fields.length === 0 && <div style={{ color: '#999', fontSize: 13, marginBottom: 12 }}>暂未添加推理步骤</div>}
                {fields.map(({ key, name, ...rest }) => (
                  <div key={key} style={{ border: '1px solid #e8e8ec', borderRadius: 8, padding: 16, marginBottom: 12, background: '#fafafa', position: 'relative' }}>
                    <div style={{ position: 'absolute', top: 8, right: 8, zIndex: 1 }}>
                      <Space size={4}>
                        {name > 0 && <Button size="small" onClick={() => move(name, name - 1)}>↑ 上移</Button>}
                        {name < fields.length - 1 && <Button size="small" onClick={() => move(name, name + 1)}>↓ 下移</Button>}
                        <Button size="small" danger onClick={() => remove(name)}>删除</Button>
                      </Space>
                    </div>
                    <div style={{ fontSize: 12, color: '#999', marginBottom: 8 }}>步骤 {name + 1}</div>
                    <Space.Compact block style={{ marginBottom: 8 }}>
                      <Form.Item {...rest} name={[name, 'step_id']} label="步骤标识" style={{ flex: 1, marginBottom: 0 }}>
                        <Input placeholder="英文标识，如 fault_check" />
                      </Form.Item>
                      <Form.Item {...rest} name={[name, 'description']} label="步骤说明" style={{ flex: 2, marginBottom: 0 }}>
                        <Input placeholder="中文说明，如 故障情况检查" />
                      </Form.Item>
                      <Form.Item {...rest} name={[name, 'agent_name']} label="执行 Agent" style={{ flex: 1, marginBottom: 0 }}>
                        <Select showSearch optionFilterProp="label" options={agents.map(a => ({ value: a.name, label: a.display_name }))} />
                      </Form.Item>
                    </Space.Compact>
                    <Form.Item {...rest} name={[name, 'output_key']} label="输出变量" style={{ marginBottom: 8 }}
                      help="步骤执行结果存入此变量，后续步骤或汇总提示词中通过此名称引用">
                      <Input placeholder="例如: fault_check_result" style={{ fontFamily: 'monospace' }} />
                    </Form.Item>
                    <Form.Item {...rest} name={[name, 'focus_concepts']} label="数据范围" style={{ marginBottom: 8 }}
                      help="该步骤专属数据源。留空则使用链条全局数据范围。"
                      getValueFromEvent={(v) => Array.isArray(v) ? v.join(',') : v}
                      getValueProps={(v) => ({ value: v ? v.split(',').filter(Boolean) : [] })}>
                      <TreeSelect treeData={conceptList} size="small" placeholder="留空=使用全局数据范围..."
                        treeCheckable showSearch treeNodeFilterProp="title"
                        style={{ minWidth: 200 }} maxTagCount={3}
                      />
                    </Form.Item>
                    <Form.Item {...rest} name={[name, 'prompt_template']} label="推理提示词" style={{ marginBottom: 0 }}
                      help="固定变量: {message} 用户消息、{data_context} 数据查询结果。之前步骤的 output_key 也可作为变量">
                      <Input.TextArea rows={4} placeholder="根据以下数据检查设备故障情况:&#10;&#10;数据: {data_context}&#10;用户问题: {message}&#10;&#10;请给出诊断结论。" style={{ fontFamily: 'monospace' }} />
                    </Form.Item>
                  </div>
                ))}
              </>
            )}
          </Form.List>
          )}
        </Space>
      </Form>
    </Drawer>
  );
}

// ═══════════════════════════════════════════════════════════════════
// 业务域配置 Tab — 概念→域分组 + 编译状态
// ═══════════════════════════════════════════════════════════════════

function AgentConfigTab({ onSwitchTab, onEditChain }) {
  const [domainConfig, setDomainConfig] = useState(null);
  const [compileStatus, setCompileStatus] = useState(null);
  const [loading, setLoading] = useState(false);
  const [compiling, setCompiling] = useState(false);

  const loadAll = useCallback(async () => {
    setLoading(true);
    try {
      const [cfg, status] = await Promise.all([
        request.get('/chains/compile/config'),
        request.get('/chains/compile/status'),
      ]);
      if (cfg.ok) setDomainConfig(cfg.config);
      if (status.ok) setCompileStatus(status);
    } catch { message.error('加载失败'); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { loadAll(); }, [loadAll]);

  const handleSaveConfig = async (newConfig) => {
    try { await request.put('/chains/compile/config', { config: newConfig }); setDomainConfig(newConfig); message.success('已保存'); }
    catch { message.error('保存失败'); }
  };

  const handleCompile = async () => {
    try { setCompiling(true); const d = await request.post('/chains/compile/reload'); message.success(d.message); await loadAll(); }
    catch { message.error('编译失败'); }
    finally { setCompiling(false); }
  };

  const [dragName, setDragName] = useState(null);
  const [dragOverName, setDragOverName] = useState(null);

  const handleDragStart = (e, name) => {
    setDragName(name);
    e.dataTransfer.effectAllowed = 'move';
  };
  const handleDragOver = (e, name) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    if (name !== dragOverName) setDragOverName(name);
  };
  const handleDrop = (e, targetName) => {
    e.preventDefault();
    setDragOverName(null); setDragName(null);
    if (!dragName || dragName === targetName) return;
    const entries = Object.entries(domainConfig);
    const fromIdx = entries.findIndex(([n]) => n === dragName);
    const toIdx = entries.findIndex(([n]) => n === targetName);
    if (fromIdx < 0 || toIdx < 0) return;
    const [moved] = entries.splice(fromIdx, 1);
    entries.splice(toIdx, 0, moved);
    const newConfig = Object.fromEntries(entries);
    setDomainConfig(newConfig);
    handleSaveConfig(newConfig);
  };
  const handleDragEnd = () => { setDragName(null); setDragOverName(null); };

  const handleMoveConcept = (concept, fromAgent, toAgent) => {
    if (!domainConfig) return;
    const newConfig = JSON.parse(JSON.stringify(domainConfig));
    if (fromAgent && newConfig[fromAgent]) {
      newConfig[fromAgent].concepts = (newConfig[fromAgent].concepts || []).filter(c => c !== concept);
    }
    if (toAgent && newConfig[toAgent]) {
      newConfig[toAgent].concepts = [...(newConfig[toAgent].concepts || []), concept];
    }
    setDomainConfig(newConfig);
    handleSaveConfig(newConfig);
  };

  if (!domainConfig || !compileStatus) return <Spin style={{ display: 'block', margin: '60px auto' }} />;

  const allConcepts = [...new Set((compileStatus.skills || []).map(s => s.concept).filter(Boolean))];
  const assigned = new Set();
  Object.values(domainConfig).forEach(cfg => (cfg.concepts || []).forEach(c => assigned.add(c)));
  const unassigned = allConcepts.filter(c => !assigned.has(c));
  const agents = compileStatus.agents || [];

  return (
    <div>
      {/* 状态栏 */}
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
        <Space wrap>
          <Button icon={<ReloadOutlined />} onClick={loadAll}>刷新</Button>
          <Button type="primary" icon={<ApiOutlined />} loading={compiling} onClick={handleCompile}>重新编译</Button>
          <Tag color="green">{compileStatus.concept_count} 概念 → {compileStatus.skill_count} Skill → {agents.length} 业务域</Tag>
          {compileStatus.compiled_at && <span style={{ fontSize: 11, color: '#999' }}>编译时间: {compileStatus.compiled_at.slice(0, 19)}</span>}
        </Space>
      </div>

      {/* 未分配概念警告 */}
      {unassigned.length > 0 && (
        <div style={{ border: '1px dashed #faad14', borderRadius: 8, padding: 12, marginBottom: 16, background: '#fffbe6' }}>
          <div style={{ fontSize: 13, fontWeight: 500, color: '#d48806', marginBottom: 8 }}>
            ⚠️ {unassigned.length} 个概念未分配 — 不属于任何业务域
          </div>
          <Space wrap size={[4, 4]}>
            {unassigned.map(c => {
              const s = (compileStatus.skills || []).find(x => x.concept === c);
              return <Tag key={c} color="orange" closable onClose={() => handleMoveConcept(c, null, Object.keys(domainConfig)[0])}>{s?.concept_label || c}</Tag>;
            })}
          </Space>
        </div>
      )}

      {/* Agent 卡片 */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(480px, 1fr))', gap: 16 }}>
        {(() => { const entries = Object.entries(domainConfig); return entries.map(([name, cfg], idx) => {
          const concepts = cfg.concepts || [];
          const isDefault = idx === 0;
          const agentInfo = agents.find(a => a.name === name) || {};
          return (
            <Card key={name} size="small"
              draggable
              onDragStart={(e) => handleDragStart(e, name)}
              onDragOver={(e) => handleDragOver(e, name)}
              onDrop={(e) => handleDrop(e, name)}
              onDragEnd={handleDragEnd}
              style={{
                border: `2px solid ${dragOverName === name ? '#6c5ce7' : isDefault ? '#6c5ce7' : '#e8e8e8'}`,
                background: dragName === name ? '#f0f0f0' : isDefault ? '#f8f7ff' : '#fff',
                opacity: dragName === name ? 0.5 : 1,
                cursor: 'grab',
                transition: 'all 0.15s',
              }} title={
              <Space align="center" size={12} style={{ padding: '4px 0' }}>
                <Text style={{ fontSize: 20 }}>{cfg.icon || '🤖'}</Text>
                <div style={{ lineHeight: 1.3 }}>
                  <Text strong>{cfg.display_name || name}</Text>
                  <br />
                  <Text type="secondary" style={{ fontSize: 11 }}><code>{name}</code></Text>
                </div>
                {isDefault && <Tag color="purple">默认</Tag>}
              </Space>
            } extra={
              <Space size={4}>
                <Tag color="blue">{agentInfo.skill_count || concepts.length} Skill</Tag>
                <Tag color="purple">{agentInfo.chain_count || 0} 链</Tag>
              </Space>
            }>
              <div style={{ fontSize: 12, color: '#666', marginBottom: 10 }}>{cfg.description}</div>
              <div style={{ fontSize: 11, color: '#999', marginBottom: 4 }}>负责概念:</div>
              <Space wrap size={[4, 4]} style={{ marginBottom: 8, minHeight: 24 }}>
                {concepts.map(c => {
                  const s = (compileStatus.skills || []).find(x => x.concept === c);
                  return <Tag key={c} closable color="blue" onClose={() => handleMoveConcept(c, name, null)}>{s?.concept_label || c}</Tag>;
                })}
                {concepts.length === 0 && <span style={{ color: '#ccc', fontSize: 11 }}>无</span>}
              </Space>
              <Select size="small" style={{ width: '100%' }} placeholder="+ 添加概念"
                showSearch value={undefined}
                filterOption={(input, option) => (option?.label || '').includes(input)}
                options={allConcepts.filter(c => !assigned.has(c) || concepts.includes(c)).map(c => {
                  const s = (compileStatus.skills || []).find(x => x.concept === c);
                  return { value: c, label: `${s?.concept_label || ''} (${c})` };
                })}
                onChange={(val) => handleMoveConcept(val, null, name)}
              />
              {/* 多跳分析链 */}
              {agentInfo.chains && agentInfo.chains.length > 0 && (
                <div style={{ marginTop: 12, borderTop: '1px solid #f0f0f0', paddingTop: 8 }}>
                  <div style={{ fontSize: 11, color: '#999', marginBottom: 4 }}>
                    多跳分析链 ({agentInfo.chains.length}):
                    <span style={{ fontSize: 10, color: '#bbb' }}>点击编辑 → 链条配置页</span>
                  </div>
                  {agentInfo.chains.map(ch => (
                    <div key={ch.name} style={{ fontSize: 11, color: '#6c5ce7', marginBottom: 2, cursor: 'pointer' }}
                      onClick={() => { onEditChain?.({ chain_id: ch.name, name: ch.display_name, description: ch.description }); }}>
                      <EditOutlined style={{ marginRight: 4, fontSize: 10 }} />
                      <strong>{ch.display_name}</strong>
                      <span style={{ color: '#bbb', marginLeft: 4 }}>
                        {ch.path?.join(' → ')}
                      </span>
                    </div>
                  ))}
                </div>
              )}
              {/* API 数据源信息 */}
              {(() => {
                const apiSkills = (compileStatus.skills || []).filter(
                  s => concepts.includes(s.concept) && s.data_source_type === 'api'
                );
                if (apiSkills.length === 0) return null;
                return (
                  <div style={{ marginTop: 8, borderTop: '1px solid #f0f0f0', paddingTop: 8 }}>
                    <div style={{ fontSize: 11, color: '#999', marginBottom: 4 }}>API 直连:</div>
                    {apiSkills.map(s => (
                      <Tag key={s.name} color="green" style={{ fontSize: 10 }}>{s.display_name}</Tag>
                    ))}
                  </div>
                );
              })()}
            </Card>
          );
        })})()}
      </div>
    </div>
  );
}

// ── (旧 AgentDrawer 保留, 从 AgentConfigTab 不再引用) ──

function AgentDrawer({ open, editingAgent, onClose, onSaved }) {
  const [form] = Form.useForm();
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    if (editingAgent) {
      form.setFieldsValue({
        name: editingAgent.name, display_name: editingAgent.display_name, icon: editingAgent.icon,
        color: editingAgent.color, description: editingAgent.description, enabled: editingAgent.enabled,
        roles: editingAgent.roles || [], keywords: (editingAgent.keywords || []).join('\n'),
        system_prompt: editingAgent.system_prompt || '', sort_order: editingAgent.sort_order,
      });
    } else {
      form.resetFields();
      form.setFieldsValue({ enabled: true, color: '#6c5ce7', sort_order: 0, roles: [], keywords: '' });
    }
  }, [open, editingAgent, form]);

  const handleSave = async () => {
    try {
      const values = await form.validateFields(); setSaving(true);
      const payload = {
        name: values.name, display_name: values.display_name || '', icon: values.icon || '',
        color: typeof values.color === 'string' ? values.color : (values.color?.toHexString?.() || '#6c5ce7'),
        description: values.description || '', enabled: values.enabled,
        roles: values.roles || [],
        keywords: (values.keywords || '').split('\n').map(s => s.trim()).filter(Boolean),
        system_prompt: values.system_prompt || '',
        sort_order: values.sort_order || 0,
      };
      if (editingAgent) {
        await request.put(`/agents/${encodeURIComponent(editingAgent.name)}`, payload);
        message.success('已更新');
      } else {
        await request.post('/agents', payload);
        message.success('已创建');
      }
      onSaved();
    } catch (err) {
      if (err?.errorFields) return;
      message.error(err?.response?.data?.detail || '保存失败');
    } finally { setSaving(false); }
  };

  return (
    <Drawer
      title={editingAgent ? `编辑: ${editingAgent.display_name || editingAgent.name}` : '新建 Agent'}
      open={open}
      onClose={onClose}
      width={600}
      extra={
        <Space>
          <Button onClick={onClose}>取消</Button>
          <Button type="primary" loading={saving} onClick={handleSave}>保存</Button>
        </Space>
      }
    >
      <Form form={form} layout="vertical">
        <Space.Compact block>
          <Form.Item name="name" label="内部标识" rules={[{ required: true }]} style={{ flex: 1 }}
            help={editingAgent ? '' : '英文标识，创建后不可修改'}>
            <Input placeholder="如 production_execution" disabled={!!editingAgent} />
          </Form.Item>
          <Form.Item name="display_name" label="显示名称" rules={[{ required: true }]} style={{ flex: 2 }}>
            <Input placeholder="中文名称，如 生产执行" />
          </Form.Item>
        </Space.Compact>
        <Space.Compact block>
          <Form.Item name="icon" label="图标(emoji)" style={{ flex: 1 }}>
            <Input placeholder="如 🖥️" maxLength={2} />
          </Form.Item>
          <Form.Item name="color" label="标识颜色" style={{ flex: 1 }}>
            <ColorPicker format="hex" />
          </Form.Item>
          <Form.Item name="sort_order" label="排序权重" style={{ flex: 1 }}
            help="数值越大越靠前">
            <Input type="number" />
          </Form.Item>
        </Space.Compact>
        <Form.Item name="description" label="角色描述">
          <Input.TextArea rows={2} placeholder="简要说明该 Agent 的职责和擅长领域" />
        </Form.Item>
        <Form.Item name="roles" label="可见角色" help="仅勾选的角色可看到此 Agent，不选则所有角色可见">
          <Select mode="multiple" placeholder="不选 = 所有角色可见"
            options={AGENT_ROLES.map(r => ({ value: r, label: r }))} />
        </Form.Item>
        <Form.Item name="keywords" label="关键词" help="用户消息命中这些关键词时会优先匹配该 Agent，每行一个">
          <Input.TextArea rows={3} placeholder="生产&#10;报工&#10;工单" style={{ fontFamily: 'monospace' }} />
        </Form.Item>
        <Form.Item name="system_prompt" label="自定义系统提示词" help="留空则使用该 Agent 的默认提示词。可用变量: {用户名}, {用户角色}, {当前时间}">
          <Input.TextArea rows={5} placeholder="你是一个生产执行助手，负责处理工单报工、工序流转等操作..." style={{ fontFamily: 'monospace' }} />
        </Form.Item>
        <Form.Item name="enabled" label="启用状态" valuePropName="checked">
          <Switch checkedChildren="开" unCheckedChildren="关" />
        </Form.Item>
      </Form>
    </Drawer>
  );
}

// ═══════════════════════════════════════════════════════════════════
// MCP Servers Tab
// ═══════════════════════════════════════════════════════════════════

function MCPServersTab() {
  const [servers, setServers] = useState([]);
  const [loading, setLoading] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editingServer, setEditingServer] = useState(null);
  const [formKey, setFormKey] = useState(0);

  const loadServers = useCallback(async () => {
    setLoading(true);
    try {
      const data = await request.get('/mcp/servers');
      setServers(Array.isArray(data) ? data : []);
    } catch { message.error('加载失败'); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { loadServers(); }, [loadServers]);

  const handleCreate = () => {
    setEditingServer(null);
    setFormKey(k => k + 1);
    setDrawerOpen(true);
  };

  const handleEdit = (server) => {
    setEditingServer(server);
    setFormKey(k => k + 1);
    setDrawerOpen(true);
  };

  const handleDelete = async (name) => {
    try { await request.delete(`/mcp/servers/${encodeURIComponent(name)}`); message.success('已删除'); loadServers(); }
    catch { message.error('删除失败'); }
  };

  const handleConnect = async (name) => {
    try { await request.post(`/mcp/servers/${encodeURIComponent(name)}/connect`); message.success('已连接'); loadServers(); }
    catch { message.error('连接失败'); }
  };

  const handleDisconnect = async (name) => {
    try { await request.post(`/mcp/servers/${encodeURIComponent(name)}/disconnect`); message.success('已断开'); loadServers(); }
    catch { message.error('断开失败'); }
  };

  const columns = [
    { title: '服务器名称', dataIndex: 'name', width: 150, render: t => <code style={{ fontSize: 12, color: '#6c5ce7' }}>{t}</code> },
    { title: '启动命令', dataIndex: 'command', width: 160, ellipsis: true },
    { title: '描述', dataIndex: 'description', ellipsis: true },
    { title: '工具数', dataIndex: 'tool_count', width: 70, align: 'center' },
    { title: '连接', dataIndex: 'connected', width: 80, align: 'center',
      render: v => <Tag color={v ? 'green' : 'red'}>{v ? '已连接' : '未连接'}</Tag> },
    { title: '启用', dataIndex: 'enabled', width: 60, align: 'center',
      render: v => <Tag color={v ? 'green' : 'default'}>{v ? '是' : '否'}</Tag> },
    { title: '操作', key: 'actions', width: 200, render: (_, r) => (
      <Space>
        {r.connected
          ? <Button size="small" onClick={() => handleDisconnect(r.name)}>断开</Button>
          : <Button size="small" type="primary" ghost onClick={() => handleConnect(r.name)} disabled={!r.enabled}>连接</Button>
        }
        <Button size="small" icon={<EditOutlined />} onClick={() => handleEdit(r)} />
        <Popconfirm title="确定删除？" onConfirm={() => handleDelete(r.name)}>
          <Button size="small" danger icon={<DeleteOutlined />} />
        </Popconfirm>
      </Space>
    )},
  ];

  return (
    <>
      <div style={{ marginBottom: 16, textAlign: 'right' }}>
        <Button type="primary" icon={<PlusOutlined />} onClick={handleCreate}>添加 MCP 服务器</Button>
      </div>
      <Table columns={columns} dataSource={servers} rowKey="name" loading={loading}
        size="middle" pagination={false}
        locale={{ emptyText: <Empty description="暂无 MCP 服务器" /> }} />

      <MCPDrawer
        key={formKey}
        open={drawerOpen}
        editingServer={editingServer}
        onClose={() => setDrawerOpen(false)}
        onSaved={() => { setDrawerOpen(false); loadServers(); }}
      />
    </>
  );
}

function MCPDrawer({ open, editingServer, onClose, onSaved }) {
  const [form] = Form.useForm();
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    if (editingServer) {
      form.setFieldsValue({
        name: editingServer.name, command: editingServer.command,
        args: (editingServer.args || []).join('\n'),
        description: editingServer.description || '', enabled: editingServer.enabled,
      });
    } else {
      form.resetFields();
      form.setFieldsValue({ enabled: true, args: '' });
    }
  }, [open, editingServer, form]);

  const handleSave = async () => {
    try {
      const values = await form.validateFields(); setSaving(true);
      const payload = {
        name: values.name, command: values.command,
        args: (values.args || '').split('\n').map(s => s.trim()).filter(Boolean),
        description: values.description || '', enabled: values.enabled,
      };
      if (editingServer) {
        await request.put(`/mcp/servers/${encodeURIComponent(editingServer.name)}`, payload);
        message.success('已更新');
      } else {
        await request.post('/mcp/servers', payload);
        message.success('已创建');
      }
      onSaved();
    } catch (err) {
      if (err?.errorFields) return;
      message.error(err?.response?.data?.detail || '保存失败');
    } finally { setSaving(false); }
  };

  return (
    <Drawer
      title={editingServer ? `编辑: ${editingServer.name}` : '添加 MCP 服务器'}
      open={open}
      onClose={onClose}
      width={600}
      extra={
        <Space>
          <Button onClick={onClose}>取消</Button>
          <Button type="primary" loading={saving} onClick={handleSave}>保存</Button>
        </Space>
      }
    >
      <Form form={form} layout="vertical">
        <Space.Compact block>
          <Form.Item name="name" label="服务标识" rules={[{ required: true }]} style={{ flex: 1 }}
            help={editingServer ? '' : '英文标识，创建后不可修改'}>
            <Input placeholder="如 mes_tools" disabled={!!editingServer} />
          </Form.Item>
          <Form.Item name="command" label="启动命令" rules={[{ required: true }]} style={{ flex: 2 }}>
            <Input placeholder="如 mes-cli 或 python mcp_server.py" />
          </Form.Item>
        </Space.Compact>
        <Form.Item name="args" label="命令参数" help="每行一个参数，如 --port 8080">
          <Input.TextArea rows={3} placeholder="--verbose&#10;--timeout=30" style={{ fontFamily: 'monospace' }} />
        </Form.Item>
        <Form.Item name="description" label="功能描述">
          <Input.TextArea rows={2} placeholder="简要说明该 MCP 服务器提供的工具用途" />
        </Form.Item>
        <Form.Item name="enabled" label="启用状态" valuePropName="checked">
          <Switch checkedChildren="开" unCheckedChildren="关" />
        </Form.Item>
      </Form>
    </Drawer>
  );
}

// ═══════════════════════════════════════════════════════════════════
// A2A External Agents Tab
// ═══════════════════════════════════════════════════════════════════

function A2AAgentsTab() {
  const [agents, setAgents] = useState([]);
  const [loading, setLoading] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editingAgent, setEditingAgent] = useState(null);
  const [formKey, setFormKey] = useState(0);

  const loadAgents = useCallback(async () => {
    setLoading(true);
    try {
      const data = await request.get('/a2a/agents');
      setAgents(Array.isArray(data) ? data : []);
    } catch { message.error('加载失败'); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { loadAgents(); }, [loadAgents]);

  const handleCreate = () => {
    setEditingAgent(null);
    setFormKey(k => k + 1);
    setDrawerOpen(true);
  };

  const handleEdit = (agent) => {
    setEditingAgent(agent);
    setFormKey(k => k + 1);
    setDrawerOpen(true);
  };

  const handleDelete = async (name) => {
    try { await request.delete(`/a2a/agents/${encodeURIComponent(name)}`); message.success('已删除'); loadAgents(); }
    catch { message.error('删除失败'); }
  };

  const columns = [
    { title: '标识', dataIndex: 'name', width: 140, render: t => <code style={{ fontSize: 12, color: '#6c5ce7' }}>{t}</code> },
    { title: '显示名称', dataIndex: 'display_name', width: 120 },
    { title: '启动命令', dataIndex: 'command', width: 150, ellipsis: true },
    { title: '描述', dataIndex: 'description', ellipsis: true },
    { title: '注册状态', dataIndex: 'registered', width: 90, align: 'center',
      render: v => <Tag color={v ? 'green' : 'orange'}>{v ? '已注册' : '未注册'}</Tag> },
    { title: '启用', dataIndex: 'enabled', width: 60, align: 'center',
      render: v => <Tag color={v ? 'green' : 'default'}>{v ? '是' : '否'}</Tag> },
    { title: '操作', key: 'actions', width: 100, render: (_, r) => (
      <Space>
        <Button size="small" icon={<EditOutlined />} onClick={() => handleEdit(r)} />
        <Popconfirm title="确定删除？" onConfirm={() => handleDelete(r.name)}>
          <Button size="small" danger icon={<DeleteOutlined />} />
        </Popconfirm>
      </Space>
    )},
  ];

  return (
    <>
      <div style={{ marginBottom: 16, textAlign: 'right' }}>
        <Button type="primary" icon={<PlusOutlined />} onClick={handleCreate}>添加外部 Agent</Button>
      </div>
      <Table columns={columns} dataSource={agents} rowKey="name" loading={loading}
        size="middle" pagination={false}
        locale={{ emptyText: <Empty description="暂无外部 Agent" /> }} />

      <A2ADrawer
        key={formKey}
        open={drawerOpen}
        editingAgent={editingAgent}
        onClose={() => setDrawerOpen(false)}
        onSaved={() => { setDrawerOpen(false); loadAgents(); }}
      />
    </>
  );
}

function A2ADrawer({ open, editingAgent, onClose, onSaved }) {
  const [form] = Form.useForm();
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    if (editingAgent) {
      form.setFieldsValue({
        name: editingAgent.name, display_name: editingAgent.display_name,
        command: editingAgent.command, args: (editingAgent.args || []).join('\n'),
        description: editingAgent.description || '', enabled: editingAgent.enabled,
      });
    } else {
      form.resetFields();
      form.setFieldsValue({ enabled: true, args: '' });
    }
  }, [open, editingAgent, form]);

  const handleSave = async () => {
    try {
      const values = await form.validateFields(); setSaving(true);
      const payload = {
        name: values.name, display_name: values.display_name || '',
        command: values.command,
        args: (values.args || '').split('\n').map(s => s.trim()).filter(Boolean),
        description: values.description || '', enabled: values.enabled,
      };
      if (editingAgent) {
        await request.put(`/a2a/agents/${encodeURIComponent(editingAgent.name)}`, payload);
        message.success('已更新');
      } else {
        await request.post('/a2a/agents', payload);
        message.success('已创建');
      }
      onSaved();
    } catch (err) {
      if (err?.errorFields) return;
      message.error(err?.response?.data?.detail || '保存失败');
    } finally { setSaving(false); }
  };

  return (
    <Drawer
      title={editingAgent ? `编辑: ${editingAgent.display_name || editingAgent.name}` : '添加外部 Agent'}
      open={open}
      onClose={onClose}
      width={600}
      extra={
        <Space>
          <Button onClick={onClose}>取消</Button>
          <Button type="primary" loading={saving} onClick={handleSave}>保存</Button>
        </Space>
      }
    >
      <Form form={form} layout="vertical">
        <Space.Compact block>
          <Form.Item name="name" label="内部标识" rules={[{ required: true }]} style={{ flex: 1 }}
            help={editingAgent ? '' : '英文标识，创建后不可修改'}>
            <Input placeholder="如 erp_agent" disabled={!!editingAgent} />
          </Form.Item>
          <Form.Item name="display_name" label="显示名称" style={{ flex: 2 }}>
            <Input placeholder="中文名称，如 ERP 查询助手" />
          </Form.Item>
        </Space.Compact>
        <Form.Item name="command" label="启动命令" rules={[{ required: true }]}>
          <Input placeholder="如 python external_agent.py 或 mes-cli agent" />
        </Form.Item>
        <Form.Item name="args" label="命令参数" help="每行一个参数">
          <Input.TextArea rows={3} placeholder="--name=erp&#10;--port=9100" style={{ fontFamily: 'monospace' }} />
        </Form.Item>
        <Form.Item name="description" label="功能描述">
          <Input.TextArea rows={2} placeholder="说明该外部 Agent 提供的功能和用途" />
        </Form.Item>
        <Form.Item name="enabled" label="启用状态" valuePropName="checked">
          <Switch checkedChildren="开" unCheckedChildren="关" />
        </Form.Item>
      </Form>
    </Drawer>
  );
}

// ═══════════════════════════════════════════════════════════════════
// KPI Admin Tab
// ═══════════════════════════════════════════════════════════════════

const KPI_DIRECTION_LABELS = { higher_better: '越高越好', lower_better: '越低越好' };
const KPI_DOMAIN_LABELS = {
  equipment: '设备', quality: '质量', scheduling: '排产',
  inventory: '库存', andon: '安灯', production: '生产',
};

function KPIAdminTab() {
  const [kpis, setKpis] = useState([]);
  const [loading, setLoading] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editingKPI, setEditingKPI] = useState(null);
  const [formKey, setFormKey] = useState(0);

  const loadKPIs = useCallback(async () => {
    setLoading(true);
    try {
      const data = await request.get('/admin/kpis');
      setKpis(Array.isArray(data) ? data : []);
    } catch { message.error('加载失败'); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { loadKPIs(); }, [loadKPIs]);

  const handleCreate = () => {
    setEditingKPI(null);
    setFormKey(k => k + 1);
    setDrawerOpen(true);
  };

  const handleEdit = (kpi) => {
    setEditingKPI(kpi);
    setFormKey(k => k + 1);
    setDrawerOpen(true);
  };

  const handleDelete = async (kpiKey) => {
    try { await request.delete(`/admin/kpis/${encodeURIComponent(kpiKey)}`); message.success('已删除'); loadKPIs(); }
    catch { message.error('删除失败'); }
  };

  const handleReload = async () => {
    try { await request.post('/admin/kpis/reload'); message.success('KPI 已重新加载'); }
    catch { message.error('加载失败'); }
  };

  const columns = [
    { title: '标识', dataIndex: 'kpi_key', width: 140, render: t => <code style={{ fontSize: 12 }}>{t}</code> },
    { title: '指标名称', dataIndex: 'name', width: 160 },
    { title: '目标值', dataIndex: 'target', width: 80, align: 'right', render: (v, r) => `${v} ${r.unit}` },
    { title: '方向', dataIndex: 'direction', width: 80, render: v => KPI_DIRECTION_LABELS[v] || v },
    { title: '预警阈值', dataIndex: 'warning_threshold', width: 80, align: 'right' },
    { title: '严重阈值', dataIndex: 'critical_threshold', width: 80, align: 'right' },
    { title: '领域', dataIndex: 'domain', width: 80, render: v => <Tag>{KPI_DOMAIN_LABELS[v] || v}</Tag> },
    { title: '启用', dataIndex: 'enabled', width: 60, align: 'center',
      render: v => <Tag color={v ? 'green' : 'default'}>{v ? '是' : '否'}</Tag> },
    { title: '操作', key: 'actions', width: 100, render: (_, r) => (
      <Space>
        <Button size="small" icon={<EditOutlined />} onClick={() => handleEdit(r)} />
        <Popconfirm title="确定删除？" onConfirm={() => handleDelete(r.kpi_key)}>
          <Button size="small" danger icon={<DeleteOutlined />} />
        </Popconfirm>
      </Space>
    )},
  ];

  return (
    <>
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between' }}>
        <Button icon={<ReloadOutlined />} onClick={handleReload}>重新加载</Button>
        <Button type="primary" icon={<PlusOutlined />} onClick={handleCreate}>新增 KPI 指标</Button>
      </div>
      <Table columns={columns} dataSource={kpis} rowKey="kpi_key" loading={loading}
        size="middle" pagination={false}
        locale={{ emptyText: <Empty description="暂无 KPI 指标" /> }} />

      <KPIDrawer
        key={formKey}
        open={drawerOpen}
        editingKPI={editingKPI}
        onClose={() => setDrawerOpen(false)}
        onSaved={() => { setDrawerOpen(false); loadKPIs(); }}
      />
    </>
  );
}

function KPIDrawer({ open, editingKPI, onClose, onSaved }) {
  const [form] = Form.useForm();
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    if (editingKPI) {
      form.setFieldsValue(editingKPI);
    } else {
      form.resetFields();
      form.setFieldsValue({ enabled: true, direction: 'higher_better' });
    }
  }, [open, editingKPI, form]);

  const handleSave = async () => {
    try {
      const values = await form.validateFields(); setSaving(true);
      if (editingKPI) {
        await request.put(`/admin/kpis/${encodeURIComponent(editingKPI.kpi_key)}`, values);
        message.success('已更新');
      } else {
        await request.post('/admin/kpis', values);
        message.success('已创建');
      }
      onSaved();
    } catch (err) {
      if (err?.errorFields) return;
      message.error(err?.response?.data?.detail || '保存失败');
    } finally { setSaving(false); }
  };

  return (
    <Drawer
      title={editingKPI ? `编辑: ${editingKPI.name || editingKPI.kpi_key}` : '新增 KPI 指标'}
      open={open}
      onClose={onClose}
      width={550}
      extra={
        <Space>
          <Button onClick={onClose}>取消</Button>
          <Button type="primary" loading={saving} onClick={handleSave}>保存</Button>
        </Space>
      }
    >
      <Form form={form} layout="vertical">
        <Space.Compact block>
          <Form.Item name="kpi_key" label="指标标识" rules={[{ required: true }]} style={{ flex: 1 }}
            help={editingKPI ? '' : '英文标识，创建后不可修改'}>
            <Input placeholder="如 oee" disabled={!!editingKPI} />
          </Form.Item>
          <Form.Item name="name" label="指标名称" rules={[{ required: true }]} style={{ flex: 2 }}>
            <Input placeholder="如 OEE 设备综合效率" />
          </Form.Item>
        </Space.Compact>
        <Space.Compact block>
          <Form.Item name="target" label="目标值" rules={[{ required: true }]} style={{ flex: 1 }}>
            <Input type="number" />
          </Form.Item>
          <Form.Item name="unit" label="单位" style={{ flex: 1 }}>
            <Input placeholder="如 %" />
          </Form.Item>
          <Form.Item name="direction" label="优化方向" style={{ flex: 1 }}>
            <Select options={[
              { value: 'higher_better', label: '越高越好' },
              { value: 'lower_better', label: '越低越好' },
            ]} />
          </Form.Item>
        </Space.Compact>
        <Space.Compact block>
          <Form.Item name="warning_threshold" label="预警阈值" rules={[{ required: true }]} style={{ flex: 1 }}>
            <Input type="number" />
          </Form.Item>
          <Form.Item name="critical_threshold" label="严重阈值" rules={[{ required: true }]} style={{ flex: 1 }}>
            <Input type="number" />
          </Form.Item>
        </Space.Compact>
        <Form.Item name="domain" label="所属领域">
          <Select options={Object.entries(KPI_DOMAIN_LABELS).map(([v, l]) => ({ value: v, label: l }))} />
        </Form.Item>
        <Form.Item name="enabled" label="启用状态" valuePropName="checked">
          <Switch checkedChildren="开" unCheckedChildren="关" />
        </Form.Item>
      </Form>
    </Drawer>
  );
}

// ═══════════════════════════════════════════════════════════════════
// Explorer Rules Tab
// ═══════════════════════════════════════════════════════════════════

const RULE_TYPE_LABELS = { threshold: '阈值检测', graph_pattern: '图模式' };
const SEVERITY_LABELS = { high: '严重', medium: '警告', low: '提示' };

function ExplorerRulesTab() {
  const [rules, setRules] = useState([]);
  const [loading, setLoading] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editingRule, setEditingRule] = useState(null);
  const [formKey, setFormKey] = useState(0);

  const loadRules = useCallback(async () => {
    setLoading(true);
    try {
      const data = await request.get('/admin/explorer-rules');
      setRules(Array.isArray(data) ? data : []);
    } catch { message.error('加载失败'); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { loadRules(); }, [loadRules]);

  const handleCreate = () => {
    setEditingRule(null);
    setFormKey(k => k + 1);
    setDrawerOpen(true);
  };

  const handleEdit = (rule) => {
    setEditingRule(rule);
    setFormKey(k => k + 1);
    setDrawerOpen(true);
  };

  const handleDelete = async (name) => {
    try { await request.delete(`/admin/explorer-rules/${encodeURIComponent(name)}`); message.success('已删除'); loadRules(); }
    catch { message.error('删除失败'); }
  };

  const handleReload = async () => {
    try { await request.post('/admin/explorer-rules/reload'); message.success('规则已重载'); }
    catch { message.error('重载失败'); }
  };

  const columns = [
    { title: '规则名', dataIndex: 'name', width: 150, render: t => <code style={{ fontSize: 12 }}>{t}</code> },
    { title: '类型', dataIndex: 'rule_type', width: 80, render: v => RULE_TYPE_LABELS[v] || v },
    { title: '检测对象', dataIndex: 'concept', width: 100, render: (v, r) => r.rule_type === 'threshold' ? v : '-' },
    { title: '条件', key: 'condition', width: 140,
      render: (_, r) => r.rule_type === 'threshold'
        ? <code style={{ fontSize: 12 }}>{r.check_property} {r.check_op} {r.check_value}</code>
        : <span style={{ fontSize: 12, color: '#999' }}>Cypher 查询</span> },
    { title: '严重度', dataIndex: 'severity', width: 70, align: 'center',
      render: v => <Tag color={v === 'high' ? 'red' : v === 'medium' ? 'orange' : 'blue'}>{SEVERITY_LABELS[v] || v}</Tag> },
    { title: '启用', dataIndex: 'enabled', width: 60, align: 'center',
      render: v => <Tag color={v ? 'green' : 'default'}>{v ? '是' : '否'}</Tag> },
    { title: '操作', key: 'actions', width: 100, render: (_, r) => (
      <Space>
        <Button size="small" icon={<EditOutlined />} onClick={() => handleEdit(r)} />
        <Popconfirm title="确定删除？" onConfirm={() => handleDelete(r.name)}>
          <Button size="small" danger icon={<DeleteOutlined />} />
        </Popconfirm>
      </Space>
    )},
  ];

  return (
    <>
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between' }}>
        <Button icon={<ReloadOutlined />} onClick={handleReload}>重载到检测器</Button>
        <Button type="primary" icon={<PlusOutlined />} onClick={handleCreate}>新增规则</Button>
      </div>
      <Table columns={columns} dataSource={rules} rowKey="name" loading={loading}
        size="middle" pagination={false}
        locale={{ emptyText: <Empty description="暂无检测规则" /> }} />

      <ExplorerRuleDrawer
        key={formKey}
        open={drawerOpen}
        editingRule={editingRule}
        onClose={() => setDrawerOpen(false)}
        onSaved={() => { setDrawerOpen(false); loadRules(); }}
      />
    </>
  );
}

function ExplorerRuleDrawer({ open, editingRule, onClose, onSaved }) {
  const [form] = Form.useForm();
  const [saving, setSaving] = useState(false);
  const [ruleType, setRuleType] = useState('threshold');

  useEffect(() => {
    if (!open) return;
    if (editingRule) {
      setRuleType(editingRule.rule_type);
      form.setFieldsValue({
        ...editingRule,
        graph_params: typeof editingRule.graph_params === 'string'
          ? editingRule.graph_params
          : JSON.stringify(editingRule.graph_params || {}, null, 2),
      });
    } else {
      form.resetFields();
      setRuleType('threshold');
      form.setFieldsValue({ enabled: true, rule_type: 'threshold', severity: 'medium', check_op: '>' });
    }
  }, [open, editingRule, form]);

  const handleSave = async () => {
    try {
      const values = await form.validateFields(); setSaving(true);
      if (editingRule) {
        await request.put(`/admin/explorer-rules/${encodeURIComponent(editingRule.name)}`, values);
        message.success('已更新');
      } else {
        await request.post('/admin/explorer-rules', values);
        message.success('已创建');
      }
      onSaved();
    } catch (err) {
      if (err?.errorFields) return;
      message.error(err?.response?.data?.detail || '保存失败');
    } finally { setSaving(false); }
  };

  return (
    <Drawer
      title={editingRule ? `编辑: ${editingRule.name}` : '新增检测规则'}
      open={open}
      onClose={onClose}
      width={600}
      extra={
        <Space>
          <Button onClick={onClose}>取消</Button>
          <Button type="primary" loading={saving} onClick={handleSave}>保存</Button>
        </Space>
      }
    >
      <Form form={form} layout="vertical">
        <Space.Compact block>
          <Form.Item name="name" label="规则标识" rules={[{ required: true }]} style={{ flex: 1 }}
            help={editingRule ? '' : '英文标识，创建后不可修改'}>
            <Input placeholder="如 defect_rate_high" disabled={!!editingRule} />
          </Form.Item>
          <Form.Item name="rule_type" label="规则类型" style={{ flex: 1 }}>
            <Select onChange={setRuleType} options={[
              { value: 'threshold', label: '阈值检测' },
              { value: 'graph_pattern', label: '图模式' },
            ]} />
          </Form.Item>
        </Space.Compact>
        {ruleType === 'threshold' ? (
          <>
            <Space.Compact block>
              <Form.Item name="concept" label="检测概念" style={{ flex: 1 }}>
                <Input placeholder="如 QualityCheck" />
              </Form.Item>
              <Form.Item name="check_property" label="属性" style={{ flex: 1 }}>
                <Input placeholder="如 defectRate" />
              </Form.Item>
            </Space.Compact>
            <Space.Compact block>
              <Form.Item name="check_op" label="运算符" style={{ flex: 1 }}>
                <Select options={['>','<','>=','<=','==','!='].map(v => ({ value: v, label: v }))} />
              </Form.Item>
              <Form.Item name="check_value" label="阈值" style={{ flex: 1 }}>
                <Input placeholder="如 3.0 或 safetyStock" />
              </Form.Item>
            </Space.Compact>
          </>
        ) : (
          <>
            <Form.Item name="graph_query" label="Cypher 查询语句" rules={[{ required: true }]}>
              <Input.TextArea rows={6} placeholder="MATCH (a:AndonEvent) ..." style={{ fontFamily: 'monospace' }} />
            </Form.Item>
            <Form.Item name="graph_params" label="查询参数 (JSON)">
              <Input.TextArea rows={3} placeholder='{"expected_max": 3}' style={{ fontFamily: 'monospace' }} />
            </Form.Item>
          </>
        )}
        <Form.Item name="severity" label="严重程度">
          <Select options={[
            { value: 'high', label: '严重 - 红色' },
            { value: 'medium', label: '警告 - 橙色' },
            { value: 'low', label: '提示 - 蓝色' },
          ]} />
        </Form.Item>
        <Form.Item name="title_template" label="告警标题模板">
          <Input placeholder="如 {concept_label} 缺陷率偏高" />
        </Form.Item>
        <Form.Item name="description_template" label="告警描述模板">
          <Input.TextArea rows={3} placeholder="支持变量: {actual_value}, {threshold}, {entity_name}, {hours}" />
        </Form.Item>
        <Form.Item name="suggestion" label="处理建议">
          <Input placeholder="如 建议检查工艺参数和来料质量" />
        </Form.Item>
        <Form.Item name="enabled" label="启用状态" valuePropName="checked">
          <Switch checkedChildren="开" unCheckedChildren="关" />
        </Form.Item>
      </Form>
    </Drawer>
  );
}

// ═══════════════════════════════════════════════════════════════════
// Resource Thresholds Tab (inline form, no drawer needed)
// ═══════════════════════════════════════════════════════════════════

function ResourceThresholdsTab() {
  const [values, setValues] = useState(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await request.get('/admin/resources');
      setValues(data);
      form.setFieldsValue(data);
    } catch { message.error('加载失败'); }
    finally { setLoading(false); }
  }, [form]);

  useEffect(() => { load(); }, [load]);

  const handleSave = async () => {
    try {
      const vals = await form.validateFields(); setSaving(true);
      await request.put('/admin/resources', vals);
      message.success('已保存，即时生效');
      load();
    } catch (err) {
      if (err?.errorFields) return;
      message.error('保存失败');
    } finally { setSaving(false); }
  };

  if (!values) return <Spin style={{ display: 'block', margin: '60px auto' }} />;

  return (
    <div style={{ maxWidth: 600 }}>
      <div style={{ marginBottom: 16, display: 'flex', alignItems: 'center', gap: 16 }}>
        <Tag color={values.current_tier === 'critical' ? 'red' : values.current_tier === 'constrained' ? 'orange' : 'green'}>
          当前层级: {values.current_tier}
        </Tag>
        <span style={{ color: '#999', fontSize: 13 }}>当前并发: {values.concurrent_requests}</span>
      </div>
      <Form form={form} layout="vertical">
        <Form.Item name="resource_aware_enabled" label="资源感知优化" valuePropName="checked"
          help="关闭后不限制并发和 API 调用频率">
          <Switch checkedChildren="开" unCheckedChildren="关" />
        </Form.Item>
        <Form.Item name="max_concurrent_requests" label="最大并发请求数"
          help="全局最大并发数，达到后会排队等待">
          <Input type="number" />
        </Form.Item>
        <Form.Item name="constrained_at" label="紧张阈值"
          help="并发数达到此值进入 CONSTRAINED 状态，切换预算模型">
          <Input type="number" />
        </Form.Item>
        <Form.Item name="critical_at" label="严重阈值"
          help="并发数达到此值进入 CRITICAL 状态，严格限流">
          <Input type="number" />
        </Form.Item>
        <Form.Item name="max_api_calls_per_minute" label="API 调用频率上限（次/分钟）">
          <Input type="number" />
        </Form.Item>
        <Form.Item name="token_budget_per_hour" label="Token 预算（Token/小时）">
          <Input type="number" />
        </Form.Item>
        <Button type="primary" loading={saving} onClick={handleSave}>保存设置</Button>
      </Form>
    </div>
  );
}

