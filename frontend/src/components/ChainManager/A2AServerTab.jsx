import React, { useState, useEffect, useCallback } from 'react';
import {
  Button, Table, Card, Tag, Space, Switch, Popconfirm, message, Empty, Modal, Drawer, Typography, Input, Alert, Checkbox, Form,
} from 'antd';
import {
  PlusOutlined, ReloadOutlined, DeleteOutlined, CopyOutlined, KeyOutlined, SettingOutlined,
} from '@ant-design/icons';
import request from '../../services/request';

const { Text, Paragraph } = Typography;

/**
 * A2A 服务端「能力开放」面板
 * FC 对外 = 一个 agent 引擎（factory-copilot），业务域（domain）由本体图谱推导。
 * 先配置 API Key，再点每行的「配置」按钮给该 Key 单独设置开放能力（开放哪些业务域 = scopes）。
 */
export default function A2AServerTab() {
  const [keys, setKeys] = useState([]);
  const [domains, setDomains] = useState([]);
  const [namespace, setNamespace] = useState('');
  const [loading, setLoading] = useState(false);

  // 配置能力弹窗（configKey = 正在配置的 Key 名）
  const [configKey, setConfigKey] = useState(null);
  const [configScopes, setConfigScopes] = useState([]);
  const [savingScopes, setSavingScopes] = useState(false);

  // 创建 Key 弹窗
  const [createOpen, setCreateOpen] = useState(false);
  const [createName, setCreateName] = useState('');
  const [creating, setCreating] = useState(false);
  // 完整 Key 展示弹窗（仅创建成功后显示一次）
  const [fullKey, setFullKey] = useState(null);

  const loadAll = useCallback(async () => {
    setLoading(true);
    try {
      const [k, d] = await Promise.all([
        request.get('/a2a/keys'),
        request.get('/a2a/domains'),
      ]);
      setKeys(Array.isArray(k) ? k : []);
      setNamespace(d?.namespace || '');
      setDomains(Array.isArray(d?.domains) ? d.domains : []);
    } catch { message.error('加载失败'); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { loadAll(); }, [loadAll]);

  // ── API Key 操作 ──
  const handleCreateKey = async () => {
    if (!createName.trim()) { message.warning('请输入 Key 备注名'); return; }
    setCreating(true);
    try {
      const r = await request.post('/a2a/keys', { name: createName.trim(), scopes: [] });
      setCreateOpen(false);
      setCreateName('');
      setFullKey({ name: r.name, key: r.key });
      await loadAll();
    } catch (e) { message.error(e?.response?.data?.detail || '创建失败'); }
    finally { setCreating(false); }
  };

  const handleToggleKey = async (name, enabled) => {
    try {
      await request.put(`/a2a/keys/${encodeURIComponent(name)}`, { enabled });
      message.success(enabled ? '已启用' : '已停用');
      loadAll();
    } catch { message.error('操作失败'); }
  };

  const handleDeleteKey = async (name) => {
    try {
      await request.delete(`/a2a/keys/${encodeURIComponent(name)}`);
      message.success('已吊销');
      if (configKey === name) setConfigKey(null);
      loadAll();
    } catch { message.error('吊销失败'); }
  };

  // ── 开放能力（scopes）操作 ──
  const openConfig = (key) => {
    setConfigKey(key.name);
    setConfigScopes([...(key.scopes || [])]);
  };

  const handleSaveScopes = async () => {
    if (!configKey) return;
    setSavingScopes(true);
    try {
      await request.put(`/a2a/keys/${encodeURIComponent(configKey)}`, { scopes: configScopes });
      message.success('已保存开放能力');
      setConfigKey(null);
      loadAll();
    } catch { message.error('保存失败'); }
    finally { setSavingScopes(false); }
  };

  // 完整 Key 弹窗关闭后，自动衔接「配置能力」
  const closeFullKey = () => {
    const name = fullKey?.name;
    setFullKey(null);
    if (name) {
      const k = keys.find(x => x.name === name);
      if (k) openConfig(k);
    }
  };

  const copyFullKey = () => {
    if (!fullKey?.key) return;
    navigator.clipboard?.writeText(fullKey.key).then(
      () => message.success('已复制到剪贴板'),
      () => message.warning('复制失败，请手动选择复制'),
    );
  };

  const keyColumns = [
    { title: '备注名', dataIndex: 'name', width: 140 },
    { title: 'Key', dataIndex: 'key', width: 440,
      render: (v) => v ? (
        <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          <code style={{ fontSize: 12, color: '#6c5ce7', whiteSpace: 'nowrap' }}>{v}</code>
          <Button size="small" type="text" icon={<CopyOutlined />} style={{ flexShrink: 0 }}
            onClick={() => navigator.clipboard?.writeText(v).then(
              () => message.success('已复制'),
              () => message.warning('复制失败'),
            )} />
        </div>
      ) : <span style={{ color: '#bbb' }}>—</span> },
    { title: '开放能力', dataIndex: 'scopes', render: (scopes) => {
      if (!scopes || scopes.length === 0) return <Tag color="red">无权限</Tag>;
      const labels = domains.filter(d => scopes.includes(d.domain_key)).map(d => d.display_name || d.domain_key);
      return <Space size={[2, 2]} wrap>{(labels.length ? labels : scopes).map(s => <Tag key={s} style={{ margin: 0 }}>{s}</Tag>)}</Space>;
    } },
    { title: '最近使用', dataIndex: 'last_used_at', width: 170,
      render: v => v ? <span style={{ fontSize: 12, color: '#888' }}>{v.replace('T', ' ').slice(0, 19)}</span> : <span style={{ color: '#bbb' }}>—</span> },
    { title: '启用', dataIndex: 'enabled', width: 70, align: 'center',
      render: (v, r) => <Switch size="small" checked={v} onChange={checked => handleToggleKey(r.name, checked)} /> },
    { title: '操作', key: 'actions', width: 130, render: (_, r) => (
      <Space size={0}>
        <Button size="small" type="link" icon={<SettingOutlined />} onClick={() => openConfig(r)}>配置</Button>
        <Popconfirm title={`吊销 Key「${r.name}」？`} description="吊销后外部系统将无法再调用" onConfirm={() => handleDeleteKey(r.name)}>
          <Button size="small" danger type="text" icon={<DeleteOutlined />} />
        </Popconfirm>
      </Space>
    ) },
  ];

  return (
    <div>
      <Alert type="info" showIcon style={{ marginBottom: 16 }}
        message="FC 对外是一个 agent 引擎（factory-copilot），业务域由本体图谱推导"
        description={(
          <span>外部系统通过 <code>/.well-known/agent-card.json</code> 发现能力，携带 API Key 调用 <code>/tasks/sendSubscribe</code>。
          先创建 API Key，再点每行的「配置」按钮设置该 Key 开放的业务域（空 = 无权限）。</span>
        )} />

      <Card size="small" title={<span><KeyOutlined style={{ color: '#6c5ce7', marginRight: 6 }} />API Key 管理</span>}
        extra={<Space>
          <span style={{ fontSize: 12, color: '#888' }}>namespace: {namespace || '—'}</span>
          <Button icon={<ReloadOutlined />} onClick={loadAll}>刷新</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>创建 API Key</Button>
        </Space>}>
        <Table columns={keyColumns} dataSource={keys} rowKey="name" loading={loading}
          size="small" pagination={false} scroll={{ x: 1200 }}
          locale={{ emptyText: <Empty description="暂无 API Key，点击右上角「创建 API Key」" /> }} />
      </Card>

      {/* 创建 API Key 弹窗 */}
      <Modal
        title="创建 API Key"
        open={createOpen}
        onOk={handleCreateKey}
        confirmLoading={creating}
        onCancel={() => { setCreateOpen(false); setCreateName(''); }}
        okText="创建"
      >
        <div style={{ marginBottom: 8 }}>
          <Text>备注名</Text>
          <Input style={{ marginTop: 6 }} placeholder="如 MES 调用、WMS 调用"
            value={createName} onChange={e => setCreateName(e.target.value)} />
        </div>
        <div style={{ fontSize: 12, color: '#888' }}>
          创建后 Key 默认为「无权限」，需点「配置」按钮设置其开放的业务域。
        </div>
      </Modal>

      {/* 完整 Key 展示（仅一次） */}
      <Modal
        title="API Key 已创建"
        open={!!fullKey}
        onOk={closeFullKey}
        onCancel={closeFullKey}
        footer={[
          <Button key="copy" icon={<CopyOutlined />} onClick={copyFullKey}>复制</Button>,
          <Button key="ok" type="primary" onClick={closeFullKey}>我已保存，去配置能力</Button>,
        ]}
      >
        <Alert type="info" showIcon style={{ marginBottom: 12 }}
          message="完整 Key 已生成，可在列表中随时查看与复制" />
        <Paragraph style={{ marginBottom: 4 }}>备注名：<Text strong>{fullKey?.name}</Text></Paragraph>
        <Paragraph copyable={{ text: fullKey?.key }}>
          <code style={{ fontSize: 12, wordBreak: 'break-all' }}>{fullKey?.key}</code>
        </Paragraph>
        <div style={{ fontSize: 12, color: '#888' }}>
          调用时携带请求头：<code>Authorization: Bearer &lt;key&gt;</code>
        </div>
      </Modal>

      {/* 配置开放能力抽屉 */}
      <Drawer
        title={<>配置开放能力 — <Text strong>{configKey}</Text></>}
        open={!!configKey}
        onClose={() => setConfigKey(null)}
        width={520}
        extra={<Button type="primary" loading={savingScopes} onClick={handleSaveScopes}>保存</Button>}
      >
        <Form layout="vertical">
          <Form.Item
            label="开放业务域"
            extra="勾选该 Key 开放的业务域（空 = 无权限，不可调用任何业务域）"
          >
            {domains.length === 0 ? (
              <Empty description="当前 namespace 无业务域" />
            ) : (
              <Checkbox.Group value={configScopes} onChange={setConfigScopes} style={{ width: '100%', display: 'block' }}>
                {domains.map(d => (
                  <div key={d.domain_key} style={{ padding: '8px 4px', borderBottom: '1px solid #f5f5f5' }}>
                    <Checkbox value={d.domain_key}>
                      <span style={{ fontWeight: 600 }}>{d.display_name || d.domain_key}</span>
                      <code style={{ fontSize: 12, color: '#999', marginLeft: 8 }}>{d.domain_key}</code>
                    </Checkbox>
                    {d.description && <div style={{ fontSize: 12, color: '#888', marginTop: 2, paddingLeft: 24 }}>{d.description}</div>}
                    {d.concepts?.length > 0 && (
                      <Space size={[2, 2]} wrap style={{ marginTop: 4, paddingLeft: 24 }}>
                        {d.concepts.slice(0, 6).map(c => <Tag key={c} style={{ fontSize: 11, margin: 0 }}>{c}</Tag>)}
                        {d.concepts.length > 6 && <Text type="secondary" style={{ fontSize: 11 }}>+{d.concepts.length - 6}</Text>}
                      </Space>
                    )}
                  </div>
                ))}
              </Checkbox.Group>
            )}
          </Form.Item>
        </Form>
      </Drawer>
    </div>
  );
}
