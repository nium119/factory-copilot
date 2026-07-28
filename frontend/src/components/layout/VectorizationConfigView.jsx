import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Switch, Button, Tag, Drawer, Checkbox, Select, message, Space, Popconfirm, Typography, Row, Col, Card, Divider, InputNumber, Form, Progress, Modal } from 'antd';
import { ProTable } from '@ant-design/pro-components';
import { ReloadOutlined, EditOutlined, DeleteOutlined, PlusOutlined, SettingOutlined } from '@ant-design/icons';

const { Text, Title } = Typography;
import request from '../../services/request';

export default function VectorizationConfigView() {
  const [editModal, setEditModal] = useState(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [rebuild, setRebuild] = useState(null); // { conceptName, done, total }
  const [namespace, setNamespace] = useState('');
  const [nsLabels, setNsLabels] = useState({});
  const actionRef = useRef();

  useEffect(() => {
    request.get('/api/chains/compile/namespaces').then(d => {
      if (d.ok) {
        setNsLabels(d.labels || {});
        setNamespace(d.active || (d.namespaces?.[0]) || '');
      }
    }).catch(() => {});
  }, []);

  const handleToggle = useCallback(async (conceptName, enabled, fingerprint) => {
    try {
      await request.put(`/admin/vectorization/concepts/${conceptName}`, {
        enabled,
        fingerprint: fingerprint || { properties: [], relationProperties: [] },
      });
      message.success(`${conceptName} ${enabled ? '已启用' : '已禁用'}`);
      actionRef.current?.reload();
    } catch (e) {
      message.error('操作失败: ' + (e.message || ''));
    }
  }, []);

  const handleReindex = useCallback(async (conceptName, conceptLabel, totalCount) => {
    const cleanup = () => setTimeout(() => setRebuild(null), 2000);
    setRebuild({ conceptName, label: conceptLabel, done: 0, total: totalCount });  // 立即弹窗
    try {
      const apiBase = import.meta.env.VITE_API_BASE_URL || '';
      const resp = await fetch(`${apiBase}/api/admin/vectorization/concepts/${conceptName}/reindex/stream`, { method: 'POST' });
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        for (const line of buf.split('\n')) {
          if (line.startsWith('data: ')) {
            const d = JSON.parse(line.slice(6));
            if (d.phase === 'progress') {
              setRebuild({ conceptName, label: conceptLabel, done: d.done ?? 0, total: d.total ?? 0 });
            } else if (d.phase === 'done') {
              setRebuild(prev => prev ? { ...prev, done: d.done ?? 0 } : null);
              cleanup();
            }
          }
        }
        buf = '';
      }
      actionRef.current?.reload();
    } catch (e) {
      setRebuild(null);
      message.error('重建失败: ' + (e.message || ''));
    }
  }, []);

  const columns = [
    {
      title: '概念', dataIndex: 'conceptLabel', width: 140,
      render: (_, r) => <span>{r.conceptLabel} <Tag>{r.conceptName}</Tag></span>,
    },
    {
      title: '启用', dataIndex: 'enabled', width: 70,
      filters: [{ text: '已启用', value: 1 }, { text: '未启用', value: 0 }],
      filterMultiple: false,
      render: (v, r) => (
        <Popconfirm
          title={v === 1 ? '禁用后 findSimilar Action 将不可用' : '启用后自动注册 findSimilar Action'}
          onConfirm={() => handleToggle(r.conceptName, v !== 1, r.fingerprint)}
          okText="确认" cancelText="取消"
        >
          <Switch size="small" checked={v === 1} />
        </Popconfirm>
      ),
    },
    {
      title: '指纹属性', width: 280, search: false,
      render: (_, r) => {
        const fp = r.fingerprint || {};
        const props = fp.properties || [];
        const rels = fp.relationProperties || [];
        if (r.enabled !== 1 || props.length === 0) return <span style={{ color: '#bbb' }}>未配置</span>;
        return (
          <span style={{ fontSize: 12 }}>
            {props.map(p => <Tag key={p.name} color="blue">{p.label || p.name}</Tag>)}
            {rels.map(rp => (
              <Tag key={rp.relation} color="green">{rp.relationLabel || rp.relation}: [{rp.properties?.map(p => p.label || p.name).join(',')}]</Tag>
            ))}
          </span>
        );
      },
    },
    {
      title: '向量化进度', width: 140, search: false,
      render: (_, r) => {
        if (r.enabled !== 1) return <span style={{ color: '#bbb' }}>—</span>;
        const pct = r.totalCount > 0 ? Math.round((r.indexedCount / r.totalCount) * 100) : 0;
        const color = pct === 100 ? 'green' : pct > 0 ? 'orange' : 'red';
        return <Tag color={color}>已向量化 {r.indexedCount}/{r.totalCount}</Tag>;
      },
    },
    {
      title: '操作', width: 180, search: false,
      render: (_, r) =>
        r.enabled === 1 ? (
          <Space>
            <Button size="small" icon={<EditOutlined />} onClick={() => setEditModal(r)}>编辑</Button>
            <Button size="small" icon={<ReloadOutlined />} onClick={() => handleReindex(r.conceptName, r.conceptLabel, r.totalCount)}>重建向量</Button>
          </Space>
        ) : null,
    },
  ];

  return (
    <div style={{ padding: 24 }}>
      <ProTable
        actionRef={actionRef}
        columns={columns}
        rowKey="conceptName"
        search={{ labelWidth: 'auto', defaultCollapsed: false }}
        options={{ reload: true, density: true }}
        toolbar={{
          title: '概念向量化配置',
          actions: [
            <Button key="reload" icon={<ReloadOutlined />} onClick={() => actionRef.current?.reload()}>刷新</Button>,
            <Button key="settings" icon={<SettingOutlined />} onClick={() => setSettingsOpen(true)}>设置</Button>,
          ],
        }}
        tableRender={(_, dom) => dom}
        pagination={false}
        request={async (params) => {
          try {
            const resp = await request.get(`/admin/vectorization/concepts?namespace=${namespace}`);
            let data = (resp.concepts || []).map(c => ({
              ...c,
              enabled: c.enabled ? 1 : 0,
            }));
            if (params.conceptName) {
              data = data.filter(d =>
                d.conceptName?.toLowerCase().includes(params.conceptName.toLowerCase()) ||
                d.conceptLabel?.includes(params.conceptName)
              );
            }
            if (params.enabled && params.enabled.length > 0) {
              const want = params.enabled[0];
              data = data.filter(d => d.enabled === want);
            }
            return { data, total: data.length, success: true };
          } catch (e) {
            return { data: [], total: 0, success: false };
          }
        }}
        locale={{ emptyText: '暂无概念数据，请先在 OntoStudio 中推送本体到 Neo4j' }}
      />

      {editModal && (
        <EditFingerprintModal
          concept={editModal}
          namespace={namespace}
          onClose={() => setEditModal(null)}
          onSaved={() => { setEditModal(null); actionRef.current?.reload(); }}
        />
      )}
      <SettingsDrawer open={settingsOpen} onClose={() => setSettingsOpen(false)} />
      <Modal
        title={rebuild ? `正在重建 ${rebuild.label || rebuild.conceptName}` : ''}
        open={!!rebuild}
        footer={null}
        closable={false}
        maskClosable={false}
        width={400}
      >
        <Progress
          percent={rebuild && rebuild.total > 0 ? Math.round((rebuild.done / rebuild.total) * 100) : 0}
          format={() => rebuild ? `${rebuild.done}/${rebuild.total}` : ''}
        />
      </Modal>
    </div>
  );
}

// ── 编辑指纹属性弹窗 ──────────────────────────────────────

function EditFingerprintModal({ concept, namespace, onClose, onSaved }) {
  const fp = concept.fingerprint || {};
  const [selectedProps, setSelectedProps] = useState(fp.properties || []);
  const [relationProps, setRelationProps] = useState(
    (fp.relationProperties || []).map(rp => ({
      relation: rp.relation || '',
      properties: rp.properties || [],
      separator: rp.separator || '×',
    }))
  );
  const [saving, setSaving] = useState(false);
  const [loadedConcepts, setLoadedConcepts] = useState([]);

  useEffect(() => {
    request.get(`/admin/vectorization/concepts?namespace=${namespace}`).then(resp => {
      setLoadedConcepts(resp.concepts || []);
    }).catch(() => {});
  }, []);

  const propOptions = (concept.properties || []).map(p => ({
    label: `${p.label || p.name} (${p.name})`, value: p.name,
  }));

  const relOptions = (concept.relations || []).map(r => ({
    label: `${r.label || r.target} → ${r.target}`, value: r.target || r.label,
  }));
  const getChildProps = (relationName) => {
    const target = loadedConcepts.find(c => c.conceptName === relationName);
    return (target?.properties || []).map(p => ({
      label: `${p.label || p.name} (${p.name})`, value: p.name,
    }));
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await request.put(`/admin/vectorization/concepts/${concept.conceptName}`, {
        enabled: concept.enabled,
        fingerprint: {
          properties: selectedProps,
          relationProperties: relationProps.filter(rp => rp.relation && rp.properties.length > 0),
        },
      });
      message.success('配置已保存');
      onSaved();
    } catch (e) {
      message.error('保存失败: ' + (e.message || ''));
    } finally {
      setSaving(false);
    }
  };

  const propLabels = {};
  (concept.properties || []).forEach(p => { propLabels[p.name] = p.label || p.name; });
  const getConceptLabels = (conceptName) => {
    const c = loadedConcepts.find(x => x.conceptName === conceptName);
    const m = {};
    (c?.properties || []).forEach(p => { m[p.name] = p.label || p.name; });
    return { label: c?.conceptLabel || conceptName, propMap: m };
  };
  const fingerprintPreview = [
    ...selectedProps.map(p => propLabels[p] || p),
    ...relationProps.filter(rp => rp.relation && rp.properties.length > 0)
      .map(rp => {
        const { label, propMap } = getConceptLabels(rp.relation);
        return `${label}.[${rp.properties.map(p => propMap[p] || p).join(', ')}]`;
      }),
  ].join(' | ') || '暂未选择属性';

  return (
    <Drawer
      title={<span>指纹属性 — {concept.conceptLabel || concept.conceptName}</span>}
      open onClose={onClose} width={900}
      extra={
        <Space>
          <Button onClick={onClose}>取消</Button>
          <Button type="primary" loading={saving} onClick={handleSave}>保存</Button>
        </Space>
      }
    >
      <Row gutter={[16, 16]}>
        {/* 节点属性 */}
        <Col span={24}>
          <Card size="small" title="节点属性" styles={{ body: { padding: '12px 16px' } }}>
            <Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 12 }}>
              选择用于描述实例"相似度"的特征属性，选中后拼接为向量化指纹文本
            </Text>
            <Checkbox.Group value={selectedProps} onChange={vals => setSelectedProps(vals)}>
              <Row gutter={[4, 8]}>
                {propOptions.map(opt => (
                  <Col span={12} key={opt.value}>
                    <Checkbox value={opt.value}>
                      <Text style={{ fontSize: 13 }}>{opt.label}</Text>
                    </Checkbox>
                  </Col>
                ))}
              </Row>
            </Checkbox.Group>
          </Card>
        </Col>

        {/* 关系属性 */}
        <Col span={24}>
          <Card
            size="small"
            title="关系属性"
            extra={
              <Button size="small" type="link" icon={<PlusOutlined />}
                onClick={() => setRelationProps([...relationProps, { relation: '', properties: [] }])}>
                添加
              </Button>
            }
            styles={{ body: { padding: '12px 16px' } }}
          >
            <Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 12 }}>
              子概念属性参与指纹计算，如 BOM 的物料名称和用量
            </Text>
            {relationProps.length === 0 && (
              <Text type="secondary" style={{ fontSize: 13 }}>暂不关联子概念属性</Text>
            )}
            {relationProps.map((rp, idx) => (
              <div key={idx} style={{
                marginBottom: 12, padding: 12,
                background: '#fafbfc', borderRadius: 6,
                border: '1px solid #f0f0f0',
              }}>
                <Row gutter={8} align="middle" style={{ marginBottom: 8 }}>
                  <Col flex="auto">
                    <Select
                      size="small" style={{ width: '100%' }}
                      placeholder="选择关系目标" value={rp.relation || undefined}
                      options={relOptions}
                      onChange={val => {
                        const next = [...relationProps];
                        next[idx] = { ...next[idx], relation: val, properties: [] };
                        setRelationProps(next);
                      }}
                    />
                  </Col>
                  <Col>
                    <Button size="small" type="text" danger icon={<DeleteOutlined />}
                      onClick={() => setRelationProps(relationProps.filter((_, i) => i !== idx))} />
                  </Col>
                </Row>
                {rp.relation && getChildProps(rp.relation).length > 0 && (
                  <Checkbox.Group
                    value={rp.properties}
                    onChange={vals => {
                      const next = [...relationProps];
                      next[idx] = { ...next[idx], properties: vals };
                      setRelationProps(next);
                    }}
                  >
                    <Row gutter={[8, 4]}>
                      {getChildProps(rp.relation).map(cp => (
                        <Col key={cp.value} span={12}><Checkbox value={cp.value}>{cp.label}</Checkbox></Col>
                      ))}
                    </Row>
                  </Checkbox.Group>
                )}
              </div>
            ))}
          </Card>
        </Col>

        {/* 预览 */}
        <Col span={24}>
          <Divider style={{ margin: '0 0 8px' }} />
          <Text type="secondary" style={{ fontSize: 12 }}>指纹片段预览</Text>
          <div style={{
            marginTop: 4, padding: '8px 12px',
            background: '#f6f8fa', borderRadius: 4,
            fontFamily: 'monospace', fontSize: 12, color: '#555',
            wordBreak: 'break-all',
          }}>
            {fingerprintPreview}
          </div>
        </Col>
      </Row>
    </Drawer>
  );
}

// ── 全局设置抽屉 ─────────────────────────────────────────

function SettingsDrawer({ open, onClose }) {
  const defaults = { maintenanceInterval: 60, defaultTopK: 5, graphWeight: 0.7, maxCandidates: 1000 };
  const [settings, setSettings] = useState(defaults);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (open) {
      request.get('/admin/vectorization/settings').then(d => setSettings({ ...defaults, ...d.settings })).catch(() => {});
    }
  }, [open]);

  const handleSave = async () => {
    setSaving(true);
    try {
      await request.put('/admin/vectorization/settings', settings);
      message.success('设置已保存');
      onClose();
    } catch (e) {
      message.error('保存失败');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Drawer title="向量化设置" open={open} onClose={onClose} width={400}
      extra={<Button type="primary" loading={saving} onClick={handleSave}>保存</Button>}
    >
      <Form layout="vertical" size="small">
        <Form.Item label="维护间隔（秒）" help="后台扫描补全未向量化节点的周期">
          <InputNumber style={{ width: '100%' }} min={10} max={3600}
            value={settings.maintenanceInterval}
            onChange={v => setSettings(s => ({ ...s, maintenanceInterval: v }))} />
        </Form.Item>
        <Form.Item label="默认返回数" help="findSimilar 默认返回 Top-K 数量">
          <InputNumber style={{ width: '100%' }} min={1} max={20}
            value={settings.defaultTopK}
            onChange={v => setSettings(s => ({ ...s, defaultTopK: v }))} />
        </Form.Item>
        <Form.Item label="图匹配权重" help="图结构相似 vs 语义向量相似的比例（0~1）">
          <InputNumber style={{ width: '100%' }} min={0} max={1} step={0.1}
            value={settings.graphWeight}
            onChange={v => setSettings(s => ({ ...s, graphWeight: v }))} />
        </Form.Item>
        <Form.Item label="最大候选数" help="单次检索最多遍历的节点数">
          <InputNumber style={{ width: '100%' }} min={100} max={10000} step={100}
            value={settings.maxCandidates}
            onChange={v => setSettings(s => ({ ...s, maxCandidates: v }))} />
        </Form.Item>
      </Form>
    </Drawer>
  );
}
