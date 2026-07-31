import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import {
  Button, Table, Drawer, Form, Input, Select, Switch, Space, Tag, Popconfirm, Popover,
  message, Empty, Tabs, ColorPicker, Spin, Typography, Card,
} from 'antd';
import { ProTable } from '@ant-design/pro-components';
import ApiTab from './ApiTab';
import StatsTab from './StatsTab';
import ModelConfigTab from './ModelConfigTab';
import ConnectionConfigTab from './ConnectionConfigTab';
import ChainForm from '../ChainEditor';
import VectorizationConfigView from '../layout/VectorizationConfigView';

const { Text } = Typography;
import {
  PlusOutlined, EditOutlined, DeleteOutlined, ReloadOutlined, SaveOutlined,
  ArrowLeftOutlined, LinkOutlined, RobotOutlined, ApiOutlined, BookOutlined,
  DashboardOutlined, ControlOutlined, CloudServerOutlined,
  ClockCircleOutlined, BarChartOutlined,
} from '@ant-design/icons';
import request from '../../services/request';

const AGENT_COLORS = {
  analysis_monitor: '#6c5ce7',
  quality_equipment: '#00b894',
  production_management: '#fdcb6e',
  production_execution: '#0984e3',
  warehouse_logistics: '#e17055',
};

export default function ChainManager({ onBack, onNamespaceChange, onRefresh, initialTab, tabFilter }) {
  const [activeTab, setActiveTab] = useState(initialTab || 'agents');
  const [chainDrawerOpen, setChainDrawerOpen] = useState(false);
  const [editingChain, setEditingChain] = useState(null);
  const [chainDrawerKey, setChainDrawerKey] = useState(0);
  const [chainsRefreshKey, setChainsRefreshKey] = useState(0);

  const [namespaces, setNamespaces] = useState([]);
  const [nsLabels, setNsLabels] = useState({});
  const [activeNs, setActiveNs] = useState('');
  const [switchingNs, setSwitchingNs] = useState(false);

  useEffect(() => {
    request.get('/chains/compile/namespaces').then(d => {
      if (d.ok) { setNamespaces(d.namespaces || []); setNsLabels(d.labels || {}); setActiveNs(d.active); }
    }).catch(() => {});
  }, []);

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
          {onBack && (
          <Button type="text" icon={<ArrowLeftOutlined />} onClick={onBack} style={{ fontSize: 16 }}>
            返回对话
          </Button>
          )}
          <span style={{ fontSize: 16, fontWeight: 600, color: '#1a1a2e' }}>系统配置</span>
          {initialTab === 'agents' && (<><span style={{ marginLeft: 12, fontSize: 13, color: '#888' }}>本体图谱：</span>
          <Select size="small" style={{ width: 140 }} value={activeNs}
            loading={switchingNs}
            onChange={async (val) => {
              setSwitchingNs(true);
              try {
                const r = await request.post(`/chains/compile/namespace/${encodeURIComponent(val)}`);
                if (r.ok) {
                  setActiveNs(val);
                  message.success(r.message || '切换完成', 1.5);
                  onNamespaceChange?.();
                } else {
                  message.error(r.message || '切换失败');
                }
              } catch { message.error('切换失败'); }
              finally { setSwitchingNs(false); }
            }}
            options={namespaces.map(n => ({ value: n, label: nsLabels[n] || n }))}
          /></>)}
        </Space>
        <Button type="link" icon={<BookOutlined />} onClick={() => window.open('/manual.html', '_blank')} style={{ fontSize: 13 }}>
          操作手册
        </Button>
      </div>

      <Tabs
        activeKey={activeTab}
        onChange={setActiveTab}
        destroyInactiveTabPane={false}
        style={{ flex: 1, display: 'flex', flexDirection: 'column' }}
        tabBarStyle={{ paddingLeft: 16, marginBottom: 0 }}
        items={[
          { key: 'agents', label: <span><ControlOutlined />业务域配置</span>,
            children: <div style={{ height: 'calc(100vh - 190px)', overflow: 'auto', padding: 20 }}><AgentConfigTab onSwitchTab={setActiveTab} onEditChain={handleEditChain} onRefresh={onRefresh} /></div> },
          { key: 'chains', label: <span><LinkOutlined />链条配置</span>,
            children: <div style={{ height: 'calc(100vh - 190px)', overflow: 'auto', padding: 20 }}><ChainsTab key={chainsRefreshKey} onEditChain={handleEditChain} drawerOpen={chainDrawerOpen} editingChain={editingChain} formKey={chainDrawerKey} onDrawerClose={handleChainsSaved} onDrawerSaved={handleChainsSaved} agents={agentsForDrawer} /></div> },
          { key: 'skills', label: <span><ApiOutlined />操作目录</span>,
            children: <div style={{ height: 'calc(100vh - 190px)', overflow: 'auto', padding: 20 }}><SkillsTab /></div> },
          { key: 'systems', label: <span><CloudServerOutlined />API 接口</span>,
            children: <div style={{ height: 'calc(100vh - 190px)', overflow: 'auto', padding: 20 }}><ApiTab /></div> },
          { key: 'vectorization', label: <span><ControlOutlined />向量化</span>,
            children: <div style={{ height: 'calc(100vh - 190px)', overflow: 'auto' }}><VectorizationConfigView /></div> },
          { key: 'api-logs', label: <span><ApiOutlined />API 日志</span>,
            children: <div style={{ height: 'calc(100vh - 230px)', overflow: 'auto', padding: 10 }}><ApiLogsTab /></div> },
          { key: 'models', label: <span><RobotOutlined />模型配置</span>,
            children: <div style={{ height: 'calc(100vh - 190px)', overflow: 'auto', padding: 20 }}><ModelConfigTab /></div> },
          { key: 'stats', label: <span><BarChartOutlined />行为数据</span>,
            children: <div style={{ height: 'calc(100vh - 190px)', overflow: 'auto', padding: 20 }}><StatsTab /></div> },
          { key: 'resources', label: <span><ControlOutlined />资源阈值</span>,
            children: <div style={{ height: 'calc(100vh - 190px)', overflow: 'auto', padding: 20 }}><ResourceThresholdsTab /></div> },
          { key: 'connections', label: <span><LinkOutlined />连接配置</span>,
            children: <div style={{ height: 'calc(100vh - 190px)', overflow: 'auto', padding: 20 }}><ConnectionConfigTab /></div> },
          { key: 'health', label: <span><DashboardOutlined />系统健康</span>,
            children: <div style={{ height: 'calc(100vh - 190px)', overflow: 'auto', padding: 20 }}><SystemMonitorTab /></div> },
          { key: 'mcp', label: <span><ApiOutlined />MCP 服务器</span>,
            children: <div style={{ height: 'calc(100vh - 190px)', overflow: 'auto', padding: 20 }}><MCPServersTab /></div> },
          { key: 'a2a', label: <span><RobotOutlined />外部 Agent</span>,
            children: <div style={{ height: 'calc(100vh - 190px)', overflow: 'auto', padding: 20 }}><A2AAgentsTab /></div> },
        ].filter(item => !tabFilter || tabFilter.includes(item.key))}
      />
      <Drawer
        title={editingChain && editingChain.chain_id ? `编辑: ${editingChain.display_name || editingChain.name || editingChain.chain_id}` : (editingChain ? `保存为链: ${editingChain.display_name || editingChain.name || '动态规划链'}` : '新建链条')}
        open={chainDrawerOpen}
        onClose={() => { setChainDrawerOpen(false); setChainsRefreshKey(k => k + 1); setEditingChain(null); }}
        width={720}
      >
        {chainDrawerOpen && (
          <ChainForm
            key={chainDrawerKey}
            record={editingChain}
            agents={agentsForDrawer}
            onCancel={() => { setChainDrawerOpen(false); setChainsRefreshKey(k => k + 1); setEditingChain(null); }}
            onSuccess={() => { setChainDrawerOpen(false); setChainsRefreshKey(k => k + 1); setEditingChain(null); }}
          />
        )}
      </Drawer>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════
// Chains Tab
// ═══════════════════════════════════════════════════════════════════

function ChainsTab({ onEditChain, drawerOpen: extDrawerOpen, editingChain: extEditingChain, formKey: extFormKey, onDrawerClose, onDrawerSaved, agents: externalAgents }) {
  const [localAgents, setLocalAgents] = useState([]);
  const [localDrawerOpen, setLocalDrawerOpen] = useState(false);
  const [localEditingChain, setLocalEditingChain] = useState(null);
  const [localFormKey, setLocalFormKey] = useState(0);

  const useExternal = !!onEditChain;
  const drawerOpen = useExternal ? extDrawerOpen : localDrawerOpen;
  const editingChain = useExternal ? extEditingChain : localEditingChain;
  const formKey = useExternal ? extFormKey : localFormKey;
  const agents = externalAgents?.length ? externalAgents : localAgents;

  const actionRef = useRef();

  const loadAgents = useCallback(async () => {
    try {
      const data = await request.get('/chains/agents/list').catch(() => []);
      setLocalAgents(Array.isArray(data) ? data : []);
    } catch { /* silent */ }
  }, []);

  useEffect(() => { loadAgents(); }, [loadAgents]);

  const handleCreate = () => {
    if (useExternal) { onEditChain(null); return; }
    setLocalEditingChain(null); setLocalFormKey(k => k + 1); setLocalDrawerOpen(true);
  };
  const handleEdit = (chain) => {
    if (useExternal) { onEditChain(chain); return; }
    setLocalEditingChain(chain); setLocalFormKey(k => k + 1); setLocalDrawerOpen(true);
  };

  const handleDelete = async (chainId) => {
    try { await request.delete(`/chains/${encodeURIComponent(chainId)}`); message.success('已删除'); actionRef.current?.reload(); }
    catch { message.error('删除失败'); }
  };

  const handleReload = async () => {
    try { await request.post('/chains/reload'); message.success('缓存已刷新'); }
    catch { message.error('刷新失败'); }
  };

  const columns = [
    { title: '链条ID', dataIndex: 'chain_id', width: 170, search: false, render: (_, r) => <code style={{ fontSize: 12, color: '#6c5ce7' }}>{r.chain_id}</code> },
    { title: '名称', dataIndex: 'name', width: 140, render: (_, r) => r.display_name || r.name },
    { title: '描述', dataIndex: 'description', ellipsis: true, search: false },
    { title: '模式', key: 'mode', width: 80, search: false, render: (_, r) => (
      r.mode === 'pipeline' ? <Tag color="blue">执行链</Tag> :
      (r.reasoning_steps || r.steps || []).length > 0 ? <Tag color="purple">链式</Tag> : <Tag color="default">合并</Tag>
    )},
    { title: '来源', dataIndex: 'source', width: 80, valueType: 'select', valueEnum: { manual: '手动', compiler: '编译器' },
      render: (_, r) => r.source === 'compiler' ? <Tag color="orange">编译器</Tag> : <Tag color="default">手动</Tag> },
    { title: '启用', dataIndex: 'enabled', width: 60, valueType: 'select', valueEnum: { 1: '是', 0: '否' },
      render: (_, r) => <Tag color={r.enabled ? 'green' : 'default'}>{r.enabled ? '是' : '否'}</Tag> },
    { title: '操作', key: 'actions', width: 100, search: false, render: (_, r) => (
      <Space>
        <Button size="small" icon={<EditOutlined />} onClick={() => handleEdit(r)} />
        <Popconfirm title="确定删除？" onConfirm={() => handleDelete(r.chain_id)}>
          <Button size="small" danger icon={<DeleteOutlined />} />
        </Popconfirm>
      </Space>
    )},
  ];

  return (
    <ProTable
      actionRef={actionRef}
      columns={columns}
      rowKey="chain_id"
      search={{ labelWidth: 'auto', defaultCollapsed: false }}
      options={{ reload: true, density: true }}
      toolbar={{
        actions: [
          <Button key="reload" icon={<ReloadOutlined />} onClick={handleReload}>刷新缓存</Button>,
          <Button key="create" type="primary" icon={<PlusOutlined />} onClick={handleCreate}>新建链条</Button>,
        ],
      }}
      pagination={false}
      request={async (params) => {
        const data = await request.get('/chains').catch(() => []);
        const filtered = params.name
          ? data.filter(d => (d.name || '').toLowerCase().includes(params.name.toLowerCase()))
          : data;
        return { data: filtered, total: filtered.length, success: true };
      }}
      locale={{ emptyText: <Empty description="暂无链条配置" /> }}
    />
  );
}

// ═══════════════════════════════════════════════════════════════════
// API 接口 Tab — 外部 HTTP API 配置（MCP/CLI 由各自 tab 管理）
// ═══════════════════════════════════════════════════════════════════


// ═══════════════════════════════════════════════════════════════════
// Skills Tab
// ═══════════════════════════════════════════════════════════════════

function SkillsTab() {
  const actionRef = useRef();
  const [overrides, setOverrides] = useState({});
  const [cachedStatus, setCachedStatus] = useState(null);

  const loadOverrides = useCallback(async () => {
    try {
      const ov = await request.get('/chains/compile/skill-overrides').catch(() => ({ ok: false }));
      setOverrides(ov.overrides || {});
    } catch { /* silent */ }
  }, []);

  useEffect(() => { loadOverrides(); }, [loadOverrides]);
  useEffect(() => { actionRef.current?.reload(); }, [overrides]);

  const saveOverrides = async (newOv) => {
    setOverrides(newOv);
    try { await request.put('/chains/compile/skill-overrides', { overrides: newOv }); } catch { message.error('保存失败'); }
    actionRef.current?.reload();
  };

  const addTrigger = (name, trigger) => {
    if (!trigger || !trigger.trim()) return;
    const t = trigger.trim();
    const newOv = { ...overrides };
    const cur = newOv[name] || {};
    const triggers = [...(cur.triggers || [])];
    if (!triggers.includes(t)) { triggers.push(t); }
    newOv[name] = { ...cur, triggers };
    saveOverrides(newOv);
    message.success(`已添加: ${t}`);
  };

  const removeTrigger = (name, trigger) => {
    const newOv = { ...overrides };
    const cur = newOv[name] || {};
    newOv[name] = { ...cur, triggers: (cur.triggers || []).filter(t => t !== trigger) };
    saveOverrides(newOv);
    message.success(`已移除: ${trigger}`);
  };

  const dsColors = { neo4j: 'blue', api: 'green', db: 'orange' };

  const columns = [
    { title: '序号', width: 50, search: false, render: (_t, _r, idx) => idx + 1 },
    { title: '父级', width: 100, search: false, render: (_, r) => {
      const cm1 = cachedStatus?.concept_map || {};
      const ci = cm1[r.concept] || {};
      const parents = (ci.parents || []).map(p => cm1[p]?.label || p);
      return parents.length > 0
        ? <Space wrap size={[2, 2]}>{parents.map(p => <Tag key={p} color="default" style={{ fontSize: 11 }}>{p}</Tag>)}</Space>
        : <span style={{ color: '#ccc' }}>-</span>;
    }},
    { title: '概念', dataIndex: 'concept_label', width: 90, render: (_, r) => r.concept_label || <code style={{ fontSize: 12 }}>{r.concept}</code> },
    { title: '数据源', dataIndex: 'data_source_type', width: 70, valueType: 'select', valueEnum: { neo4j: 'Neo4j', api: 'API', db: 'DB' },
      render: (_, r) => <Tag color={dsColors[r.data_source_type] || 'default'}>{r.data_source_type}</Tag> },
    { title: '业务域', dataIndex: 'agent', width: 120, render: (_, r) => r.agent ? <Tag color="blue">{r.agent}</Tag> : <Tag color="default">-</Tag> },
    { title: '操作', width: 160, search: false, render: (_, r) => {
      const cm3 = cachedStatus?.concept_map || {};
      const ci = cm3[r.concept] || {};
      const actions = ci.actions || [];
      return actions.length > 0
        ? <Space wrap size={[2, 2]}>{actions.map(a => <Tag key={a.name} color="green" style={{ fontSize: 11, margin: 0 }}>{a.label || a.name}</Tag>)}</Space>
        : <span style={{ color: '#ccc' }}>-</span>;
    }},
    { title: '查询触发词', width: 160, search: false, render: (_, r) => {
      const triggers = [...new Set([...((overrides[r.name] || {}).triggers || []), ...(r.triggers || [])])];
      return (
      <Space wrap size={[2, 2]}>
        {triggers.map(t => (
          <Tag key={t} closable onClose={() => removeTrigger(r.name, t)} style={{ fontSize: 11, margin: 0 }}>{t}</Tag>
        ))}
        <Input placeholder="+ 触发词" style={{ width: 100, fontSize: 11 }}
          onKeyDown={e => { if (e.key === 'Enter') { addTrigger(r.name, e.target.value); e.target.value = ''; } }}
          onBlur={e => { if (e.target.value.trim()) { addTrigger(r.name, e.target.value.trim()); e.target.value = ''; } }}
        />
      </Space>
      );
    }},
  ];

  return (
    <ProTable
      actionRef={actionRef}
      columns={columns}
      rowKey="name"
      search={{ labelWidth: 'auto', defaultCollapsed: false }}
      options={{ reload: true, density: true }}
      headerTitle={
        cachedStatus?.ok && (
          <Tag color="green">
            应用时间: {cachedStatus.compiled_at?.slice(0, 19) || '-'} | {Object.keys(cachedStatus.concept_map || {}).length}概念 → {cachedStatus.skill_count}操作 → {cachedStatus.agent_count}业务域
          </Tag>
        )
      }
      pagination={{ defaultPageSize: 50 }}
      request={async (params) => {
        const [data] = await Promise.all([
          request.get('/chains/compile/status').catch(() => ({ ok: false })),
        ]);
        setCachedStatus(data);
        const cm = data?.concept_map || {};
        const getSeq = (c) => (cm[c] || {}).seq ?? 999;
        let list = (data.skills || []).map(s => ({
          ...s,
          effectiveTriggers: [...new Set([
            ...((overrides[s.name] || {}).triggers || []),
            ...(s.triggers || []),
          ])],
        }));
        list.sort((a, b) => getSeq(a.concept) - getSeq(b.concept));
        if (params.concept_label) {
          const kw = params.concept_label.toLowerCase();
          list = list.filter(s => (s.concept_label || s.concept).toLowerCase().includes(kw));
        }
        if (params.data_source_type) {
          list = list.filter(s => s.data_source_type === params.data_source_type);
        }
        if (params.agent) {
          list = list.filter(s => (s.agent || '').includes(params.agent));
        }
        return { data: list, total: list.length, success: true };
      }}
      locale={{ emptyText: <Empty description="暂无数据 (编译器是否已运行?)" /> }}
    />
  );
}

// ═══════════════════════════════════════════════════════════════════
// 业务域配置 Tab — 概念→域分组 + 编译状态
// ═══════════════════════════════════════════════════════════════════

function AgentConfigTab({ onSwitchTab, onEditChain, onRefresh }) {
  const [domainConfig, setDomainConfig] = useState(null);
  const [compileStatus, setCompileStatus] = useState(null);
  const [dirty, setDirty] = useState(false);
  const [loading, setLoading] = useState(false);
  const [allChains, setAllChains] = useState([]);  // DB 链条列表，按概念关联域
  const [compiling, setCompiling] = useState(false);
  const [deriveMode, setDeriveMode] = useState('');
  const [deriveThinking, setDeriveThinking] = useState('');
  const [deriveContent, setDeriveContent] = useState('');
  const thinkingRef = useRef(null);
  const contentRef = useRef(null);
  const abortRef = useRef(null);

  useEffect(() => {
    if (thinkingRef.current) thinkingRef.current.scrollTop = thinkingRef.current.scrollHeight;
  }, [deriveThinking]);
  useEffect(() => {
    if (contentRef.current) contentRef.current.scrollTop = contentRef.current.scrollHeight;
  }, [deriveContent]);
  const [historyVersions, setHistoryVersions] = useState([]);
  const [historyModalOpen, setHistoryModalOpen] = useState(false);
  const [currentVersion, setCurrentVersion] = useState('');

  const loadHistory = useCallback(async () => {
    try {
      const r = await request.get('/chains/compile/config/history');
      if (r.ok) {
        setHistoryVersions(r.versions || []);
        const active = (r.versions || []).find(v => v.is_active);
        if (active) setCurrentVersion(active.version_no || '');
      }
    } catch {}
  }, []);

  useEffect(() => { loadHistory(); }, [loadHistory, compileStatus]);

  const loadAll = useCallback(async () => {
    setLoading(true);
    try {
      const [cfg, status, chainsData] = await Promise.all([
        request.get('/chains/compile/config').catch(() => ({ ok: false })),
        request.get('/chains/compile/status').catch(() => ({ ok: false })),
        request.get('/chains').catch(() => []),
      ]);
      setAllChains(Array.isArray(chainsData) ? chainsData : []);
      if (cfg.ok) {
        setDomainConfig(cfg.config);
        setDirty(cfg.dirty || false);
      } else if (status.ok && status.agents) {
        // 无 YAML 时从编译状态自动推导，并持久化保存
        const derived = {};
        status.agents.forEach(a => {
          const skills = (status.skills || []).filter(s => s.agent === a.name || s.agent === a.display_name);
          derived[a.name] = {
            display_name: a.display_name,
            icon: a.icon || '🤖',
            color: a.color || '#6c5ce7',
            description: a.description || '',
            concepts: skills.map(s => s.concept),
          };
        });
        setDomainConfig(derived);
        // 自动保存, 下次直接读 YAML
        request.put('/chains/compile/config', { config: derived }).catch(() => {});
      }
      setCompileStatus(status);  // 始终设置，避免 ok:false 时守卫跳过渲染推导按钮
    } catch { message.error('加载失败'); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { loadAll(); }, [loadAll]);

  const updateLocal = (newConfig) => { setDomainConfig({ ...newConfig }); };

  const handleSaveConfig = async () => {
    try { await request.put('/chains/compile/config', { config: domainConfig }); setDirty(true); message.success('已保存'); }
    catch { message.error('保存失败'); }
  };

  const handleCompile = async () => {
    try { setCompiling(true); const d = await request.post('/chains/compile/reload'); setDirty(false); message.success(d.message); await loadAll(); onRefresh?.(); }
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
  };

  if (loading) return <Spin style={{ display: 'block', margin: '60px auto' }} />;
  if (!domainConfig || !compileStatus) return <Empty description="暂无业务域配置，请点击「规则推导」或「AI推导」生成" />;

  const cm = compileStatus.concept_map || {};
  // 域配置只显示当前 namespace 的概念
  const allConcepts = compileStatus.active_concepts || Object.keys(cm).filter(Boolean);
  const assigned = new Set();
  Object.values(domainConfig).forEach(cfg => (cfg.concepts || []).forEach(c => assigned.add(c)));
  const unassigned = allConcepts.filter(c => !assigned.has(c));
  const agents = compileStatus.agents || [];

  // 按概念重叠匹配链条到域：链的第一个概念在域的 concepts 中即归属
  const getChainsForDomain = (domainConcepts) => {
    const cs = new Set(domainConcepts || []);
    return allChains.filter(ch => {
      const concepts = (ch.focus_concepts || '').split(',').map(s => s.trim()).filter(Boolean);
      return concepts.length > 0 && cs.has(concepts[0]);
    });
  };

  return (
    <div style={{ height: 'calc(100vh - 200px)', overflow: 'auto' }}>
      {/* 状态栏 */}
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
        <Space wrap>
          <Button icon={<ReloadOutlined />} onClick={loadAll}>刷新</Button>
          <Button icon={<PlusOutlined />} onClick={() => {
            const name = `domain_${Date.now()}`;
            const displayName = `新业务域 ${Object.keys(domainConfig || {}).length + 1}`;
            updateLocal({ ...domainConfig, [name]: { display_name: displayName, icon: '📦', color: '#6c5ce7', description: '', concepts: [] } });
          }}>添加业务域</Button>
          <Button type="primary" icon={<ApiOutlined />} loading={compiling && !deriveMode} onClick={handleCompile}>全部应用</Button>
          {dirty
            ? <Tag color="orange">● 未应用</Tag>
            : <Space size={4}><Tag color="green">✓ 已应用</Tag>
              <Button size="small" type="link" style={{ padding: 0 }} onClick={async () => {
                const r = await request.post('/chains/compile/config/undo');
                if (r.ok) { setDirty(true); message.success(r.message); loadAll(); onRefresh?.(); }
              }}>撤销</Button></Space>
          }
          <Button icon={<DeleteOutlined />} onClick={async () => {
            try {
              await request.put('/chains/compile/config', { config: {} });
              const r = await request.post('/chains/compile/reload');
              if (r.ok) { message.success('业务域已清除'); onRefresh?.(); await loadAll(); }
              else { message.error(r.message || '清除失败'); }
            } catch(e) { message.error('清除失败: ' + (e.message || e)); console.error(e); }
          }}>清除业务域</Button>
          <Button icon={<ClockCircleOutlined />} onClick={() => { loadHistory(); setHistoryModalOpen(true); }}>
            版本 ({historyVersions.length})
          </Button>
          <Drawer title="配置版本历史" open={historyModalOpen} onClose={() => setHistoryModalOpen(false)} width={800}
            extra={
              <Popconfirm title="确定删除选中版本?" onConfirm={async () => {
                const selected = historyVersions.filter(v => v._selected);
                for (const v of selected) {
                  await request.delete(`/chains/compile/config/history/${encodeURIComponent(v.version)}`);
                }
                message.success(`已删除 ${selected.length} 个版本`);
                loadHistory();
              }}>
                <Button size="small" danger disabled={!historyVersions.some(v => v._selected)}>批量删除</Button>
              </Popconfirm>
            }>
            {historyVersions.length === 0 ? <Empty description="暂无历史版本" /> : (
              <Table size="small" pagination={{ pageSize: 15, size: 'small' }} dataSource={historyVersions} rowKey="version"
                rowSelection={{
                  type: 'checkbox',
                  onChange: (_, rows) => {
                    setHistoryVersions(prev => prev.map(v => ({ ...v, _selected: rows.some(r => r.version === v.version) })));
                  },
                }}
                expandable={{
                  expandedRowRender: (r) => (
                    <Table size="small" pagination={false} dataSource={r.domains || []} rowKey="name"
                      columns={[
                        { title: '图标', dataIndex: 'icon', width: 50, align: 'center' },
                        { title: '域名', dataIndex: 'display_name', width: 100 },
                        { title: '标识', dataIndex: 'name', width: 140, render: t => <code style={{ fontSize: 11 }}>{t}</code> },
                        { title: '概念数', dataIndex: 'concept_count', width: 60, align: 'center' },
                        { title: '概念', dataIndex: 'concepts', render: concepts => <Space wrap size={[2,2]}>{(concepts || []).map(c => <Tag key={c} style={{ fontSize: 10 }}>{c}</Tag>)}</Space> },
                      ]}
                    />
                  ),
                }}
                columns={[
                  { title: '版本', dataIndex: 'version_no', width: 60, render: (t, r) => <Space>{t}{r.is_active && <Tag color="green" style={{ fontSize: 10 }}>当前</Tag>}</Space> },
                  { title: '时间', dataIndex: 'updated_at', width: 150, render: t => t?.slice(0,19) || '-' },
                  { title: '域数', dataIndex: 'domain_count', width: 50, align: 'center' },
                  { title: '概念数', dataIndex: 'concept_count', width: 60, align: 'center' },
                  { title: '概览', dataIndex: 'domains', render: domains => <Space wrap size={[2,2]}>{(domains || []).slice(0,6).map(d => <Tag key={d.name} color="blue" style={{ fontSize: 10 }}>{d.display_name}</Tag>)}</Space> },
                  { title: '', width: 120, render: (_, r) => (
                    <Space size={4}>
                      {!r.is_active && <Popconfirm title="确定回滚到此版本？当前未保存的修改将丢失" onConfirm={async () => {
                        await request.post(`/chains/compile/config/restore/${encodeURIComponent(r.version)}`);
                        const rr = await request.post('/chains/compile/reload');
                        if (rr.ok) { message.success('已回滚'); setHistoryModalOpen(false); loadAll(); loadHistory(); onRefresh?.(); }
                      }}>
                        <Button size="small" type="primary" ghost>恢复</Button>
                      </Popconfirm>}
                      {!r.is_active && <Popconfirm title="确定删除?" onConfirm={async () => {
                        await request.delete(`/chains/compile/config/history/${encodeURIComponent(r.version)}`);
                        message.success('已删除');
                        loadHistory();
                      }}>
                        <Button size="small" danger icon={<DeleteOutlined />} />
                      </Popconfirm>}
                    </Space>
                  )},
                ]}
              />
            )}
          </Drawer>
          <Space.Compact>
            <Button icon={<ControlOutlined />} loading={compiling && deriveMode === 'rule'}
              onClick={async () => {
                setDeriveMode('rule'); setCompiling(true);
                try {
                  const r = await request.post('/chains/compile/derive?mode=rule');
                  if (r.ok) { message.success(r.message); setDirty(true); await loadAll(); onRefresh?.(); }
                  else { message.warning(r.message || '推导失败'); }
                } catch { message.error('推导失败'); }
                finally { setCompiling(false); setDeriveMode(''); }
              }}>规则推导</Button>
            <Button icon={<RobotOutlined />} loading={compiling && deriveMode === 'llm'}
              onClick={async () => {
                setDeriveMode('llm'); setCompiling(true); setDeriveThinking(''); setDeriveContent('');
                const controller = new AbortController();
                abortRef.current = controller;
                try {
                  const resp = await fetch(window.__API_BASE__ + '/chains/compile/derive/stream?mode=llm', { method: 'POST', signal: controller.signal });
                  const reader = resp.body.getReader();
                  const decoder = new TextDecoder();
                  let buffer = '';
                  while (true) {
                    const { done, value } = await reader.read();
                    if (done) break;
                    buffer += decoder.decode(value, { stream: true });
                    const lines = buffer.split('\n');
                    buffer = lines.pop() || '';
                    for (const line of lines) {
                      if (line.startsWith('data: ')) {
                        try {
                          const data = JSON.parse(line.slice(6));
                          if (data.type === 'thinking') {
                            setDeriveThinking(prev => prev + data.text);
                          } else if (data.type === 'content') {
                            setDeriveContent(prev => prev + data.text);
                          } else if (data.type === 'done') {
                            message.success(data.message);
                            setDirty(true); await loadAll(); onRefresh?.();
                          } else if (data.type === 'error') {
                            message.warning(data.message);
                          }
                        } catch {}
                      }
                    }
                  }
                } catch (e) { if (e.name !== 'AbortError') message.error('AI推导失败'); }
                finally { setCompiling(false); setDeriveMode(''); abortRef.current = null; }
              }}>⭐ AI推导</Button>
          </Space.Compact>
          {compileStatus.ok ? (
            <Tag color="green">{Object.keys(compileStatus.concept_map || {}).length} 概念 → {compileStatus.skill_count} 操作 → {agents.length} 业务域</Tag>
          ) : (
            <Tag color="blue">{Object.keys(domainConfig || {}).length} 业务域（未应用）</Tag>
          )}
          {compileStatus.compiled_at && <span style={{ fontSize: 11, color: '#999' }}>应用时间: {compileStatus.compiled_at.slice(0, 19)} {currentVersion && <Tag color="blue" style={{ fontSize: 10 }}>版本{currentVersion}</Tag>}</span>}
        </Space>
      </div>

      <Drawer title="🤖 AI 推导详情" placement="right" width={500}
        open={deriveMode === 'llm'}
        onClose={() => { if (!compiling) { setDeriveMode(''); setDeriveThinking(''); setDeriveContent(''); } }}
        extra={compiling && <Button size="small" danger onClick={() => abortRef.current?.abort()}>取消生成</Button>}
      >
        {!deriveThinking && !deriveContent && compiling && (
          <div style={{ textAlign: 'center', padding: 40, color: '#999' }}>正在等待 LLM 响应...</div>
        )}
        {deriveThinking && (
          <div style={{ marginBottom: 16 }}>
            <div style={{ fontSize: 14, fontWeight: 500, color: '#722ed1', marginBottom: 8 }}>🧠 思考过程</div>
            <pre ref={thinkingRef} style={{ fontSize: 13, whiteSpace: 'pre-wrap', maxHeight: '40vh', overflow: 'auto', margin: 0, background: '#f9f0ff', padding: 12, borderRadius: 6, lineHeight: 1.8 }}>{deriveThinking}</pre>
          </div>
        )}
        {deriveContent && (
          <div>
            <div style={{ fontSize: 14, fontWeight: 500, color: '#389e0d', marginBottom: 8 }}>📝 输出</div>
            <pre ref={contentRef} style={{ fontSize: 13, whiteSpace: 'pre-wrap', maxHeight: '40vh', overflow: 'auto', margin: 0, background: '#f6ffed', padding: 12, borderRadius: 6, lineHeight: 1.8 }}>{deriveContent}</pre>
          </div>
        )}
      </Drawer>

      {/* 编译器未运行提示 */}
      {!compileStatus.ok && !Object.keys(domainConfig || {}).length && (
        <Card size="small" style={{ marginBottom: 16, background: '#f0f5ff', border: '1px solid #adc6ff' }}>
          <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
            <span style={{ fontSize: 24 }}>📋</span>
            <div style={{ flex: 1, lineHeight: 1.8 }}>
              <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 4 }}>编译器尚未运行</div>
              <div style={{ color: '#555', fontSize: 13 }}>
                当前业务域尚未生成。请选择推导方式：
              </div>
              <div style={{ marginTop: 8, display: 'flex', gap: 12 }}>
                <div style={{ padding: '6px 12px', background: '#fff', borderRadius: 6, border: '1px solid #e8e8e8', flex: 1 }}>
                  <div style={{ fontWeight: 500, fontSize: 13 }}>🔄 规则推导</div>
                  <div style={{ color: '#888', fontSize: 12 }}>基于概念父子层级自动分组，速度快，结果确定</div>
                </div>
                <div style={{ padding: '6px 12px', background: '#fff', borderRadius: 6, border: '1px solid #e8e8e8', flex: 1 }}>
                  <div style={{ fontWeight: 500, fontSize: 13 }}>🤖 AI推导</div>
                  <div style={{ color: '#888', fontSize: 12 }}>由大模型根据语义关系智能分组，结果更贴合业务</div>
                </div>
              </div>
            </div>
          </div>
        </Card>
      )}

      {/* 未分配概念警告 */}
      {unassigned.length > 0 && (
        <div style={{ border: '1px dashed #faad14', borderRadius: 8, padding: 12, marginBottom: 16, background: '#fffbe6' }}>
          <div style={{ fontSize: 13, fontWeight: 500, color: '#d48806', marginBottom: 8 }}>
            ⚠️ {unassigned.length} 个概念未分配 — 不属于任何业务域
          </div>
          <Space wrap size={[4, 4]}>
            {unassigned.map(c => {
              const s = (compileStatus.skills || []).find(x => x.concept === c);
              return <Tag key={c} color="orange" closable onClose={() => handleMoveConcept(c, null, Object.keys(domainConfig)[0])}>{(s?.concept_label || (compileStatus.concept_map || {})[c]?.label || c)}</Tag>;
            })}
          </Space>
        </div>
      )}

      {/* 业务域卡片 */}
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
                <Input size="small" style={{ width: 40, textAlign: 'center', fontSize: 18 }} value={cfg.icon || '📦'}
                  onChange={e => { const nc = { ...domainConfig }; nc[name] = { ...cfg, icon: e.target.value }; updateLocal(nc); }} />
                <div style={{ lineHeight: 1.3 }}>
                  <Input size="small" style={{ fontWeight: 600, width: 140 }} value={cfg.display_name || name}
                    onChange={e => { const nc = { ...domainConfig }; nc[name] = { ...cfg, display_name: e.target.value }; updateLocal(nc); }} />
                  <br />
                  <Text type="secondary" style={{ fontSize: 11 }}><code>{name}</code></Text>
                </div>
                {isDefault && <Tag color="purple">默认</Tag>}
              </Space>
            } extra={
              <Space size={4} onClick={e => e.stopPropagation()}>
                <Button size="small" onClick={async () => {
                  await request.put('/chains/compile/config', { config: domainConfig });
                  setDirty(true); message.success('已保存');
                }}>保存</Button>
                <Tag color="blue">{agentInfo.skill_count || concepts.length} 操作</Tag>
                <Tag color="purple">{getChainsForDomain(concepts).length} 链</Tag>
                {<Popconfirm title="删除此业务域?" onConfirm={async () => {
                  const nc = { ...domainConfig }; delete nc[name];
                  await request.put('/chains/compile/config', { config: nc });
                  setDomainConfig(nc); message.success('已删除，点应用生效');
                }}><Button size="small" type="text" danger icon={<DeleteOutlined />} /></Popconfirm>}
              </Space>
            }>
              <Input.TextArea size="small" rows={2} style={{ marginBottom: 10 }} placeholder="域描述" value={cfg.description || ''}
                onChange={e => { const nc = { ...domainConfig }; nc[name] = { ...cfg, description: e.target.value }; updateLocal(nc); }} />
              <div style={{ fontSize: 11, color: '#999', marginBottom: 4 }}>负责概念 ({concepts.length}):</div>
              <div style={{ maxHeight: 160, overflow: 'auto', marginBottom: 8 }}>
                <Space wrap size={[4, 4]} style={{ minHeight: 24 }}>
                  {concepts.map(c => {
                    const label = (compileStatus.concept_map || {})[c]?.label || c;
                    return <Tag key={c} closable color="blue" onClose={() => handleMoveConcept(c, name, null)}>{label}</Tag>;
                  })}
                  {concepts.length === 0 && <span style={{ color: '#ccc', fontSize: 11 }}>无</span>}
                </Space>
              </div>
              <Select size="small" style={{ width: '100%' }} placeholder="+ 添加概念"
                showSearch value={undefined}
                filterOption={(input, option) => (option?.label || '').includes(input)}
                options={allConcepts.filter(c => !assigned.has(c) || concepts.includes(c)).map(c => ({
                  value: c,
                  label: `${(compileStatus.concept_map || {})[c]?.label || c}`,
                }))}
                onChange={(val) => handleMoveConcept(val, null, name)}
              />
              {/* 多跳分析链 — 从 DB 按概念匹配 */}
              {(() => {
                const matchedChains = getChainsForDomain(concepts);
                if (matchedChains.length === 0) return null;
                return (
                  <div style={{ marginTop: 12, borderTop: '1px solid #f0f0f0', paddingTop: 8 }}>
                    <div style={{ fontSize: 11, color: '#999', marginBottom: 4 }}>
                      分析链 ({matchedChains.length}):
                      <span style={{ fontSize: 10, color: '#bbb' }}> 点击编辑 → 链条配置页</span>
                    </div>
                    {matchedChains.map(ch => (
                      <div key={ch.chain_id} style={{ fontSize: 11, color: '#6c5ce7', marginBottom: 2, cursor: 'pointer' }}
                        onClick={() => onEditChain?.(ch)}>
                        <EditOutlined style={{ marginRight: 4, fontSize: 10 }} />
                        <strong>{ch.display_name || ch.name}</strong>
                        <span style={{ color: '#bbb', marginLeft: 4 }}>
                          {ch.focus_concepts?.split(',').map(c => {
                            const label = (compileStatus?.concept_map || {})[c.trim()]?.label || c.trim();
                            return label;
                          }).join(' → ')}
                        </span>
                        <Tag color={ch.source === 'compiler' ? 'orange' : 'default'} style={{ fontSize: 9, marginLeft: 4 }}>
                          {ch.source === 'compiler' ? '编译器' : '手动'}
                        </Tag>
                      </div>
                    ))}
                  </div>
                );
              })()}
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
  const [overrides, setOverrides] = useState({});

  const loadServers = useCallback(async () => {
    setLoading(true);
    try {
      const data = await request.get('/mcp/servers');
      setServers(Array.isArray(data) ? data : []);
      // 加载 MCP 专用覆盖配置（跨 namespace）
      const ov = await request.get('/mcp/servers/overrides').catch(() => ({}));
      setOverrides(ov.overrides || {});
    } catch { message.error('加载失败'); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { loadServers(); }, [loadServers]);

  const saveOverrides = async (newOv) => {
    setOverrides(newOv);
    try { await request.put('/mcp/servers/overrides', { overrides: newOv }); } catch { message.error('保存失败'); }
  };

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

  const handleApply = async () => {
    try { const r = await request.post('/mcp/servers/apply'); message.success(`已连接 ${r.connected} 台服务器`); loadServers(); }
    catch { message.error('应用失败'); }
  };
  const handleUndo = async () => {
    try { await request.post('/mcp/servers/undo'); message.success('已撤销'); loadServers(); }
    catch { message.error('撤销失败'); }
  };

  return (
    <>
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between' }}>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={loadServers}>刷新</Button>
          <Button type="primary" icon={<SaveOutlined />} onClick={handleApply}>全部应用</Button>
          <Button icon={<ClockCircleOutlined />} onClick={handleUndo}>撤销</Button>
        </Space>
        <Button type="primary" icon={<PlusOutlined />} onClick={handleCreate}>添加 MCP 服务器</Button>
      </div>
      <Table columns={columns} dataSource={servers} rowKey="name" loading={loading}
        size="small" pagination={false}
        expandable={{
          expandedRowRender: (r) => (
            <div style={{ padding: '8px 48px' }}>
              {(!r.tools || r.tools.length === 0) ? (
                <span style={{ color: '#999', fontSize: 12 }}>无工具（请先连接）</span>
              ) : (
                <Table size="small" dataSource={r.tools} rowKey="name" pagination={false}
                  columns={[
                    { title: '工具名', dataIndex: 'name', width: 180, render: v => <code>{v}</code> },
                    { title: '描述', dataIndex: 'description', ellipsis: true },
                    { title: '中文标签', dataIndex: 'name', width: 140,
                      render: (_, tool) => {
                        const fn = `mcp_${r.name}_${tool.name}`;
                        const ov = (overrides[fn] || {});
                        return <Input size="small" placeholder="输入中文名" style={{ fontSize: 12 }}
                          defaultValue={ov.label || ''}
                          onBlur={e => {
                            if (e.target.value.trim()) {
                              const newOv = { ...overrides, [fn]: { ...ov, label: e.target.value.trim() } };
                              saveOverrides(newOv);
                            }
                          }} />;
                      }},
                    { title: '触发词', dataIndex: 'name', width: 160,
                      render: (_, tool) => {
                        const fn = `mcp_${r.name}_${tool.name}`;
                        const ov = (overrides[fn] || {});
                        const triggers = ov.triggers || [];
                        return <Space wrap size={[2, 2]}>
                          {triggers.map(t => (
                            <Tag key={t} closable style={{ fontSize: 11, margin: 0 }}
                              onClose={() => {
                                const newOv = { ...overrides, [fn]: { ...ov, triggers: triggers.filter(x => x !== t) } };
                                saveOverrides(newOv);
                                message.success('已移除');
                              }}>{t}</Tag>
                          ))}
                          <Input size="small" placeholder="+触发词" style={{ width: 80, fontSize: 11 }}
                            onKeyDown={e => {
                              if (e.key === 'Enter' && e.target.value.trim()) {
                                const t = e.target.value.trim();
                                if (!triggers.includes(t)) {
                                  const newOv = { ...overrides, [fn]: { ...ov, triggers: [...triggers, t] } };
                                  saveOverrides(newOv);
                                  message.success(`已添加: ${t}`);
                                }
                                e.target.value = '';
                              }
                            }} />
                        </Space>;
                      }},
                  ]}
                />
              )}
            </div>
          ),
        }}
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
        size="small" pagination={false}
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

// ═══════════════════════════════════════════════════════════════════
// 系统监控 Tab — 健康状态 + 资源用量
// ═══════════════════════════════════════════════════════════════════

function SystemMonitorTab() {
  const [health, setHealth] = useState(null);
  const [loading, setLoading] = useState(false);

  const loadHealth = useCallback(async () => {
    setLoading(true);
    try {
      const r = await request.get('/system/health');
      setHealth(r);
    } catch { message.error('加载失败'); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { loadHealth(); }, [loadHealth]);

  if (loading) return <Spin style={{ display: 'block', margin: '60px auto' }} />;
  if (!health) return <Empty description="无法获取系统状态" />;

  const checks = health.checks || {};
  const statusIcon = (ok) => ok ? <Tag color="green">正常</Tag> : <Tag color="red">异常</Tag>;
  const neo4j = checks.neo4j || {};
  const ontology = checks.ontology || {};
  const backend = checks.data_backend || {};
  const db = checks.db || {};
  const resources = checks.resources || {};

  return (
    <div style={{ maxWidth: 900 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <h3 style={{ margin: 0 }}>系统健康总览</h3>
        <Button icon={<ReloadOutlined />} onClick={loadHealth}>刷新</Button>
      </div>

      {/* 健康卡片 */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 12, marginBottom: 24 }}>
        <Card size="small" title="Neo4j 图数据库" extra={statusIcon(neo4j.ok)}>
          <div style={{ fontSize: 12, color: '#666' }}>{neo4j.uri || '-'}</div>
        </Card>
        <Card size="small" title="本体缓存" extra={statusIcon(ontology.ok)}>
          <div style={{ fontSize: 12, color: '#666' }}>
            {ontology.concepts || 0} 概念 · {ontology.actions || 0} 动作
          </div>
        </Card>
        <Card size="small" title="数据后端" extra={statusIcon(backend.ok)}>
          <div style={{ fontSize: 12, color: '#666' }}>
            {backend.primary || '-'}
            {backend.backends && Object.entries(backend.backends).map(([k, v]) =>
              <Tag key={k} color={v.ok ? 'green' : 'red'} style={{ fontSize: 10, marginLeft: 4 }}>{k}</Tag>
            )}
          </div>
        </Card>
        <Card size="small" title="数据库" extra={statusIcon(db.ok)}>
          <div style={{ fontSize: 12, color: '#666' }}>SQLite</div>
        </Card>
      </div>

      {/* 资源用量 */}
      {Object.keys(resources).length > 0 && (
        <>
          <h4 style={{ marginBottom: 12 }}>资源用量</h4>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 12 }}>
            {resources.concurrent_requests !== undefined && (
              <Card size="small" title="当前并发">
                <div style={{ fontSize: 20, fontWeight: 600, color: resources.concurrent_requests > (resources.max_concurrent_requests || 10) * 0.8 ? '#faad14' : '#52c41a' }}>
                  {resources.concurrent_requests}
                </div>
              </Card>
            )}
            {resources.api_calls_this_minute !== undefined && (
              <Card size="small" title="API 调用/分">
                <div style={{ fontSize: 20, fontWeight: 600 }}>{resources.api_calls_this_minute}</div>
              </Card>
            )}
            {resources.token_usage_this_hour !== undefined && (
              <Card size="small" title="Token/小时">
                <div style={{ fontSize: 20, fontWeight: 600 }}>{resources.token_usage_this_hour.toLocaleString()}</div>
              </Card>
            )}
          </div>
        </>
      )}
    </div>
  );
}

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
        size="small" pagination={false}
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
        size="small" pagination={false}
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
          当前层级: {{ optimal: '充裕', normal: '正常', constrained: '紧张', critical: '严重' }[values.current_tier] || values.current_tier}
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
          help="并发数达到此值进入「紧张」状态，切换预算模型">
          <Input type="number" />
        </Form.Item>
        <Form.Item name="critical_at" label="严重阈值"
          help="并发数达到此值进入「严重」状态，严格限流">
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

// ── API 调用日志查看 ──

function ApiLogsTab() {
  const actionRef = useRef();
  const [form] = Form.useForm();
  const [keyword, setKeyword] = useState('');
  const [expandedKeys, setExpandedKeys] = useState([]);

  const columns = [
    { title: '序号', width: 50, search: false, render: (_t, _r, idx) => idx + 1 },
    { title: '时间', dataIndex: 'timestamp', width: 150, search: false, render: (_, r) => r.timestamp?.replace('T', ' ').slice(0, 19) },
    { title: '用户', dataIndex: 'user_id', width: 90 },
    { title: '会话', dataIndex: 'conversation_title', width: 160, search: false,
      render: (_, r) => r.conversation_title || (r.conversation_id ? <code style={{ fontSize: 11 }}>{r.conversation_id.slice(0, 8)}</code> : '-') },
    { title: '消息', dataIndex: 'message', width: 120, ellipsis: true, ellipsis: true, search: false },
    { title: '概念', dataIndex: 'concept', width: 120, search: false,
      render: (_, r) => r.concept ? <span>{r.concept_label && r.concept_label !== r.concept ? r.concept_label : <code style={{ fontSize: 11 }}>{r.concept}</code>}</span> : '-' },
    { title: '方法', dataIndex: 'method', width: 90, search: false,
      render: (_, r) => {
        const mm = { trigger: '触发词', rag_llm: 'RAG+LLM', llm: 'LLM分类', dynamic: '智能分析', manual: '手动指定' };
        return mm[r.method] || r.method || '-';
      }},
    { title: 'URL', dataIndex: 'url', width: 240, ellipsis: true, search: false },
    { title: '状态', dataIndex: 'status', width: 55, search: false,
      render: (_, r) => r.status > 0 ? <Tag color={r.status < 400 ? 'green' : 'red'}>{r.status}</Tag> : <Tag color="red">失败</Tag> },
    { title: '耗时', dataIndex: 'elapsed_ms', width: 65, search: false, render: (_, r) => r.elapsed_ms > 0 ? `${r.elapsed_ms}ms` : '-' },
  ];

  return (
    <ProTable
      actionRef={actionRef}
      form={form}
      columns={columns}
      rowKey="id"
      size="small"
      scroll={{ x: 'max-content', y: 600 }}
      search={false}
      options={{ reload: true, density: true }}
      expandable={{
        expandedRowRender: (r) => (
          <div style={{ padding: '12px 16px', background: '#fafafa', borderRadius: 4, fontSize: 13, lineHeight: 2 }}>
            {r.conversation_title && <div><strong>会话：</strong>{r.conversation_title}</div>}
            {r.conversation_id && !r.conversation_title && <div><strong>会话ID：</strong><code>{r.conversation_id}</code></div>}
            {r.message && <div><strong>消息：</strong>{r.message}</div>}
            {r.url && <div><strong>URL：</strong><code style={{ fontSize: 12, wordBreak: 'break-all' }}>{r.url}</code></div>}
            {r.context && <div style={{ marginTop: 8 }}><strong>详情：</strong><pre style={{ fontSize: 12, lineHeight: 1.6, whiteSpace: 'pre-wrap', wordBreak: 'break-all', margin: '4px 0 0', padding: '8px 12px', background: '#f0f0f0', borderRadius: 4, maxHeight: 300, overflow: 'auto' }}>{r.context}</pre></div>}
            {!r.message && !r.url && !r.context && <span style={{ color: '#999' }}>无详细信息</span>}
          </div>
        ),
        expandedRowKeys: expandedKeys,
        onExpand: (expanded, record) => {
          setExpandedKeys(expanded ? [record.id] : []);
        },
      }}
      onRow={(record) => ({
        onClick: () => {
          setExpandedKeys(expandedKeys.includes(record.id) ? [] : [record.id]);
        },
        style: { cursor: 'pointer' },
      })}
      pagination={{ defaultPageSize: 15, showSizeChanger: true, showTotal: t => `共 ${t} 条` }}
      toolbar={{
        actions: [
          <Input.Search key="kw" placeholder="搜索 URL/消息/错误" style={{ width: 220 }}
            value={keyword} onChange={e => setKeyword(e.target.value)}
            onSearch={() => actionRef.current?.reload()} />,
        ],
      }}
      request={async (params) => {
        const q = new URLSearchParams({ page: params.current || 1, page_size: params.pageSize || 15 });
        if (params.user_id) q.set('user_id', params.user_id);
        if (params.concept) q.set('concept', params.concept);
        if (keyword) q.set('keyword', keyword);
        const r = await request.get(`/chains/api-logs?${q}`).catch(() => ({ ok: false }));
        if (r.ok) return { data: r.logs || [], total: r.total || 0, success: true };
        return { data: [], total: 0, success: false };
      }}
      locale={{ emptyText: <Empty description="暂无 API 调用记录" /> }}
    />
  );
}

