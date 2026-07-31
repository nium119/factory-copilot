import React, { useState, useEffect } from 'react';
import { Card, Form, Input, Switch, Button, message, Spin, Row, Col, Alert, Popconfirm, Tag } from 'antd';
import { SaveOutlined, ReloadOutlined, LinkOutlined, PoweroffOutlined, CheckCircleOutlined, CloseCircleOutlined } from '@ant-design/icons';
import request from '../../services/request';

const DB_TYPES = [
  { value: 'sqlite', label: 'SQLite' },
  { value: 'postgresql', label: 'PostgreSQL' },
  { value: 'mssql', label: 'SQL Server' },
];

// 本地默认值（API 无返回时兜底）
const DEFAULTS = {
  db_sqlite_enabled: 'true',
  db_sqlite_path: './data/agent.db',
  db_postgresql_enabled: 'false',
  db_postgresql_host: 'localhost',
  db_postgresql_port: '5432',
  db_postgresql_name: 'agent',
  db_postgresql_user: 'postgres',
  db_postgresql_password: '',
  db_mssql_enabled: 'false',
  db_mssql_host: 'localhost',
  db_mssql_port: '1433',
  db_mssql_name: 'agent',
  db_mssql_user: 'sa',
  db_mssql_password: '',
  neo4j_enabled: 'true',
  neo4j_uri: 'bolt://localhost:7687',
  neo4j_user: 'neo4j',
  neo4j_password: 'neo4j123',
  neo4j_database: 'neo4j',
};

export default function ConnectionConfigTab() {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [configs, setConfigs] = useState({});
  const [testNeo4j, setTestNeo4j] = useState(false);
  const [testDb, setTestDb] = useState(false);
  const [dbStatus, setDbStatus] = useState(null);        // null | true | false
  const [neo4jStatus, setNeo4jStatus] = useState(null);
  const [restarting, setRestarting] = useState(false);

  useEffect(() => { loadConfigs(); }, []);

  const loadConfigs = async () => {
    setLoading(true);
    try {
      const data = await request.get('/system/configs');
      if (data.ok) {
        const map = { ...DEFAULTS };
        (data.data || []).forEach(c => { map[c.key] = c.value || ''; });
        setConfigs(map);
      } else {
        setConfigs({ ...DEFAULTS });
      }
    } catch (e) {
      setConfigs({ ...DEFAULTS });
    }
    setLoading(false);
  };

  const setCfg = (key, value) => setConfigs(c => ({ ...c, [key]: value }));

  const handleSave = async () => {
    setSaving(true);
    try {
      const allKeys = [
        'db_sqlite_enabled', 'db_sqlite_path',
        'db_postgresql_enabled', 'db_postgresql_host', 'db_postgresql_port', 'db_postgresql_name', 'db_postgresql_user', 'db_postgresql_password',
        'db_mssql_enabled', 'db_mssql_host', 'db_mssql_port', 'db_mssql_name', 'db_mssql_user', 'db_mssql_password',
        'neo4j_enabled', 'neo4j_uri', 'neo4j_user', 'neo4j_password', 'neo4j_database',
      ];
      const items = allKeys
        .filter(k => configs[k] !== undefined && configs[k] !== null)
        .map(k => ({ key: k, value: configs[k], description: k }));
      const data = await request.put('/system/configs', { configs: items });
      if (data.ok) {
        message.success('已保存，重启后端后生效');
      } else {
        message.error(data.error || '保存失败');
      }
    } catch (e) {
      message.error('保存失败: ' + (e.message || e));
    }
    setSaving(false);
  };

  const handleTestNeo4j = async () => {
    setTestNeo4j(true);
    setNeo4jStatus(null);
    try {
      const r = await request.post('/system/configs/test-neo4j', configs);
      setNeo4jStatus(r.ok);
    } catch (e) {
      setNeo4jStatus(false);
    }
    setTestNeo4j(false);
  };

  const handleTestDb = async () => {
    setTestDb(true);
    setDbStatus(null);
    try {
      const payload = { ...configs };
      const enabledType = DB_TYPES.find(d => configs[`db_${d.value}_enabled`] === 'true');
      if (enabledType) {
        payload.db_type = enabledType.value;
        payload.db_path = configs[`db_${enabledType.value}_path`] || '';
        payload.db_host = configs[`db_${enabledType.value}_host`] || '';
        payload.db_port = configs[`db_${enabledType.value}_port`] || '';
        payload.db_name = configs[`db_${enabledType.value}_name`] || '';
        payload.db_user = configs[`db_${enabledType.value}_user`] || '';
        payload.db_password = configs[`db_${enabledType.value}_password`] || '';
      }
      const r = await request.post('/system/configs/test-db', payload);
      setDbStatus(r.ok);
    } catch (e) {
      setDbStatus(false);
    }
    setTestDb(false);
  };

  const handleRestart = async () => {
    setRestarting(true);
    try {
      await request.post('/system/restart');
    } catch (e) {
      // 重启后连接断开是正常的
    }
    // 轮询等待后端恢复
    message.loading('服务重启中，自动等待恢复...', 0);
    for (let i = 0; i < 60; i++) {
      await new Promise(r => setTimeout(r, 2000));
      try {
        const res = await fetch(window.__API_BASE__ + '/system/health');
        if (res.ok) {
          message.destroy();
          message.success('服务已恢复，即将刷新...', 1.5);
          setTimeout(() => window.location.reload(), 1500);
          return;
        }
      } catch (e) { /* 还没起来 */ }
    }
    message.destroy();
    message.warning('等待超时，请手动刷新页面');
    setRestarting(false);
  };

  if (loading) return <div style={{ textAlign: 'center', padding: 60 }}><Spin size="large" /></div>;

  return (
    <Card
      title={<span style={{ fontSize: 16, fontWeight: 600 }}>连接配置</span>}
      style={{ width: '100%', borderRadius: 12, border: 'none', boxShadow: '0 2px 12px rgba(0,0,0,0.06)' }}
      extra={
        <div style={{ display: 'flex', gap: 8 }}>
          <Button icon={<ReloadOutlined />} onClick={loadConfigs}>重置</Button>
          <Button type="primary" icon={<SaveOutlined />} loading={saving} onClick={handleSave}>保存配置</Button>
          <Popconfirm
            title="重启后端服务"
            description="确定要重启吗？重启期间服务不可用，约3-5秒后恢复。"
            onConfirm={handleRestart}
            okText="确认重启"
            cancelText="取消"
          >
            <Button danger icon={<PoweroffOutlined />} loading={restarting}>重启服务</Button>
          </Popconfirm>
        </div>
      }
    >
      <Alert
        message="连接配置修改后需重启后端服务才能生效。未填写的配置项将使用 .env 中的默认值。"
        type="info" showIcon style={{ marginBottom: 16, borderRadius: 8 }}
      />

      <Row gutter={16}>
        <Col span={12}>
        {/* ── 应用数据库 ── */}
        <Card
          title={<span style={{ fontSize: 15, fontWeight: 600 }}>💾 应用数据库</span>}
          extra={
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              {dbStatus === true && <Tag color="success" icon={<CheckCircleOutlined />}>已连通</Tag>}
              {dbStatus === false && <Tag color="error" icon={<CloseCircleOutlined />}>连接失败</Tag>}
              <Button icon={<LinkOutlined />} loading={testDb} onClick={handleTestDb}>
                测试连接
              </Button>
            </div>
          }
          style={{ marginBottom: 20, borderRadius: 12, border: 'none', boxShadow: '0 2px 12px rgba(0,0,0,0.06)' }}
        >
        <Form layout="vertical">
          {DB_TYPES.map(d => {
            const enabled = configs[`db_${d.value}_enabled`] === 'true';
            return (
              <Card
                key={d.value}
                size="small"
                title={
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <Switch
                      checked={enabled}
                      onChange={v => {
                        if (v) {
                          // 互斥：关掉其他
                          const next = { ...configs };
                          DB_TYPES.forEach(t => { next[`db_${t.value}_enabled`] = 'false'; });
                          next[`db_${d.value}_enabled`] = 'true';
                          setConfigs(next);
                        } else {
                          setCfg(`db_${d.value}_enabled`, 'false');
                        }
                      }}
                    />
                    <span>{d.label}</span>
                  </div>
                }
                style={{
                  marginBottom: 12, borderRadius: 10,
                  border: enabled ? '1px solid #1677ff' : '1px solid #f0f0f0',
                }}
              >
                {d.value === 'sqlite' ? (
                  <Form.Item label="文件路径" style={{ marginBottom: 0 }}>
                    <Input
                      value={configs[`db_${d.value}_path`] || ''}
                      onChange={e => setCfg(`db_${d.value}_path`, e.target.value)}
                      placeholder="./data/agent.db"
                      disabled={!enabled}
                    />
                  </Form.Item>
                ) : (
                  <Row gutter={12}>
                    <Col span={12}>
                      <Form.Item label="主机">
                        <Input value={configs[`db_${d.value}_host`] || ''} onChange={e => setCfg(`db_${d.value}_host`, e.target.value)} placeholder="localhost" disabled={!enabled} />
                      </Form.Item>
                    </Col>
                    <Col span={12}>
                      <Form.Item label="端口">
                        <Input value={configs[`db_${d.value}_port`] || ''} onChange={e => setCfg(`db_${d.value}_port`, e.target.value)} placeholder={d.value === 'mssql' ? '1433' : '5432'} disabled={!enabled} />
                      </Form.Item>
                    </Col>
                    <Col span={8}>
                      <Form.Item label="数据库名">
                        <Input value={configs[`db_${d.value}_name`] || ''} onChange={e => setCfg(`db_${d.value}_name`, e.target.value)} placeholder="agent" disabled={!enabled} />
                      </Form.Item>
                    </Col>
                    <Col span={8}>
                      <Form.Item label="用户名">
                        <Input value={configs[`db_${d.value}_user`] || ''} onChange={e => setCfg(`db_${d.value}_user`, e.target.value)} placeholder="postgres" disabled={!enabled} />
                      </Form.Item>
                    </Col>
                    <Col span={8}>
                      <Form.Item label="密码">
                        <Input.Password value={configs[`db_${d.value}_password`] || ''} onChange={e => setCfg(`db_${d.value}_password`, e.target.value)} placeholder="数据库密码" disabled={!enabled} />
                      </Form.Item>
                    </Col>
                  </Row>
                )}
              </Card>
            );
          })}
          <div style={{ fontSize: 12, color: '#999' }}>每个数据库独立配置，Switch 互斥——同时只能启用一个</div>
        </Form>
      </Card>
        </Col>

        <Col span={12}>
        {/* ── Neo4j 图数据库 ── */}
        <Card
        title={
          <span style={{ fontSize: 15, fontWeight: 600 }}>
            🗄️ Neo4j 图数据库
            <Switch
              size="small"
              checked={configs['neo4j_enabled'] !== 'false'}
              onChange={v => setCfg('neo4j_enabled', v ? 'true' : 'false')}
              style={{ marginLeft: 12 }}
            />
          </span>
        }
        extra={
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            {neo4jStatus === true && <Tag color="success" icon={<CheckCircleOutlined />}>已连通</Tag>}
            {neo4jStatus === false && <Tag color="error" icon={<CloseCircleOutlined />}>连接失败</Tag>}
            <Button icon={<LinkOutlined />} loading={testNeo4j} onClick={handleTestNeo4j}>
              测试连接
            </Button>
          </div>
        }
        style={{ marginBottom: 20, borderRadius: 12, border: 'none', boxShadow: '0 2px 12px rgba(0,0,0,0.06)' }}
      >
        <Form layout="vertical">
          <Row gutter={16}>
            {[
              { key: 'neo4j_uri', label: '连接地址', placeholder: 'bolt://localhost:7687', type: 'text' },
              { key: 'neo4j_user', label: '用户名', placeholder: 'neo4j', type: 'text' },
              { key: 'neo4j_password', label: '密码', placeholder: 'neo4j123', type: 'password' },
              { key: 'neo4j_database', label: '数据库名', placeholder: 'neo4j', type: 'text' },
            ].map(f => (
              <Col span={12} key={f.key}>
                <Form.Item label={f.label}>
                  {f.type === 'password' ? (
                    <Input.Password value={configs[f.key] || ''} onChange={e => setCfg(f.key, e.target.value)} placeholder={f.placeholder} />
                  ) : (
                    <Input value={configs[f.key] || ''} onChange={e => setCfg(f.key, e.target.value)} placeholder={f.placeholder} />
                  )}
                </Form.Item>
              </Col>
            ))}
          </Row>
        </Form>
      </Card>
        </Col>
      </Row>
    </Card>
  );
}
