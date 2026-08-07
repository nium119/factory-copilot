import React, { useState, useEffect, useRef } from 'react';
import { Tabs, Card, Button, Switch, Select, Input, Drawer, Form, Tag, Space, Popconfirm, Tooltip, message } from 'antd';
import { PlusOutlined, DeleteOutlined, EditOutlined, BellOutlined, SettingOutlined, SendOutlined, CheckOutlined, ReloadOutlined } from '@ant-design/icons';
import { ProTable } from '@ant-design/pro-components';
import { authFetch } from '../../utils/authFetch';

const { TextArea } = Input;

function store(key, val) {
  if (val !== undefined) {
    localStorage.setItem(key, typeof val === 'string' ? val : JSON.stringify(val));
    return val;
  }
  try { const raw = localStorage.getItem(key); return raw ? JSON.parse(raw) : null; }
  catch { return localStorage.getItem(key); }
}

// 变量名 → 中文说明
const VAR_DESC = {
  plan_label: '方案标题', missing_actions_count: '缺失操作数量', missing_actions_list: '缺失操作列表',
  conversation_owner: '提交人工号', chain_name: '执行链名称', status: '执行结果(ok/failed/partial)',
  steps_completed: '已完成步数', total_steps: '总步数', error_summary: '错误摘要',
  user_id: '提交人工号', message: '原始消息', candidates: '候选操作列表',
  namespace: '命名空间', actions_added_count: '新增操作数量', concepts_updated: '更新的概念列表',
  tier: '资源等级', reason: '告警原因', concurrent_requests: '当前并发数', api_calls_per_minute: 'API调用/分',
};
// 事件类型 → 可用变量
const EVENT_VARS = {
  'plan.generated': ['plan_label', 'missing_actions_count', 'missing_actions_list', 'conversation_owner'],
  'plan.executed': ['chain_name', 'status', 'steps_completed', 'total_steps', 'error_summary'],
  'approval.required': ['user_id', 'message', 'candidates'],
  'schema.pushed': ['namespace', 'actions_added_count', 'concepts_updated'],
  'system.alert': ['tier', 'reason', 'concurrent_requests', 'api_calls_per_minute'],
};
const PREVIEW_VALUES = {
  plan_label: 'BOM差异分析', missing_actions_count: '2',
  missing_actions_list: 'WorkOrderBOM_copyBom, WorkOrderBOM_adjustQty', conversation_owner: 'ZHANGSAN',
  chain_name: 'BOM同步', status: '失败', steps_completed: '2', total_steps: '3',
  error_summary: '数据查询超时', user_id: 'ZHANGSAN', message: '创建新的BOM配置',
  candidates: 'WorkOrderBOM_addBomMaterial, WorkOrderBOM_query', namespace: 'mes',
  actions_added_count: '3', concepts_updated: 'WorkOrderBOM, WorkOrder',
  tier: '严重', reason: 'Token额度超限', concurrent_requests: '10', api_calls_per_minute: '35',
};
const CHANNELS = [
  { key: 'inapp', label: '🔔 应用内', desc: '站内通知中心' },
  { key: 'wecom', label: '💬 企业微信', desc: '群机器人 Webhook' },
  { key: 'dingtalk', label: '📌 钉钉', desc: '群机器人 Webhook' },
  { key: 'email', label: '📧 邮件', desc: 'SMTP 发送' },
  { key: 'sms', label: '📱 短信', desc: 'SMS 网关' },
  { key: 'webhook', label: '🔗 Webhook', desc: '自定义 HTTP 推送' },
];

// ── 规则表列定义 ──
const RULE_COLUMNS = (actionRef, onEdit, onDelete, onToggle, eventTypes, targets, conditions) => [
  { title: '优先级', dataIndex: 'priority', width: 80, align: 'center', search: false },
  {
    title: '触发规则', dataIndex: 'event_type', width: 260, ellipsis: true,
    render: (_, r) => {
      const et = (eventTypes || []).find(e => e.key === r.event_type);
      const list = conditions[r.event_type] || [];
      const c = list.find(x => x.key === r.condition);
      const eventLabel = et?.label || r.event_type;
      const condLabel = c ? c.label : (r.condition || '总是通知');
      return <span style={{ fontSize: 13 }}>{eventLabel} · <span style={{ color: condLabel.includes('仅') ? '#fa8c16' : '#888' }}>{condLabel}</span></span>;
    },
  },
  {
    title: '通知目标', dataIndex: 'target', width: 130,
    render: (_, r) => {
      const t = (targets || []).find(tg => tg.key === r.target);
      return t ? t.label : r.target;
    },
  },
  {
    title: '渠道', dataIndex: 'channels', width: 120, search: false,
    render: (_, r) => {
      try {
        const chs = typeof r.channels === 'string' ? JSON.parse(r.channels) : (r.channels || []);
        return chs.map(c => {
          const ch = CHANNELS.find(x => x.key === c);
          return <Tag key={c} style={{ fontSize: 10, margin: '0 2px' }}>{ch?.label || c}</Tag>;
        });
      } catch { return <span>{r.channels}</span>; }
    },
  },
  {
    title: '标题模板', dataIndex: 'title_template', ellipsis: true, search: false,
    render: (_, r) => <span style={{ fontSize: 12 }}>{r.title_template}</span>,
  },
  {
    title: '启用', dataIndex: 'enabled', width: 70, align: 'center', search: false,
    render: (_, r) => <Switch size="small" checked={r.enabled} onChange={() => onToggle(r)} />,
  },
  {
    title: '操作', width: 80, align: 'center', search: false,
    render: (_, r) => (
      <Space>
        <Button size="small" icon={<EditOutlined />} onClick={() => onEdit(r)} />
        <Popconfirm title="删除此规则？" onConfirm={() => onDelete(r.id)}>
          <Button size="small" danger icon={<DeleteOutlined />} />
        </Popconfirm>
      </Space>
    ),
  },
];

export default function NotificationPrefs() {
  const [activeTab, setActiveTab] = useState('rules');
  const [eventTypes, setEventTypes] = useState([]);
  const [targets, setTargets] = useState([]);
  const [conditions, setConditions] = useState({});
  const [formEventType, setFormEventType] = useState('');
  const [formTarget, setFormTarget] = useState('');
  const [formUserId, setFormUserId] = useState('');
  const [employeeOptions, setEmployeeOptions] = useState([]);
  const [channelCfgs, setChannelCfgs] = useState({});

  const fetchEmployees = async (q = '') => {
    try {
      const resp = await authFetch(`${window.__API_BASE__}/notifications/employees/search?q=${encodeURIComponent(q || '')}`);
      const data = await resp.json();
      setEmployeeOptions((data.items || []).map(e => ({ value: e.code, label: e.label })));
    } catch { /* ignore */ }
  };

  useEffect(() => {
    authFetch(window.__API_BASE__ + '/notifications/channel-configs').then(r => r.json()).then(d => {
      const m = {};
      (d.items || []).forEach(c => { m[c.key] = c.value; });
      setChannelCfgs(m);
      // 同步到本地状态
      setWecomUrl(m.wecom_webhook || '');
      setDingtalkUrl(m.dingtalk_webhook || '');
      setWebhookUrl(m.webhook_url || '');
    }).catch(() => {});
  }, []);

  useEffect(() => {
    authFetch(window.__API_BASE__ + '/notifications/event-types').then(r => r.json()).then(d => {
      setEventTypes(d.items || []);
      setTargets(d.targets || []);
      setConditions(d.conditions || {});
    }).catch(() => {});
  }, []);

  const fetchNotifs = async () => {
    setNotifLoading(true);
    try {
      const resp = await authFetch(window.__API_BASE__ + '/notifications?status=all&limit=50');
      const data = await resp.json();
      setNotifs(data.items || []);
    } catch { /* ignore */ }
    setNotifLoading(false);
  };
  const [ruleModal, setRuleModal] = useState(null);
  const [wecomUrl, setWecomUrl] = useState('');
  const [wecomTestResult, setWecomTestResult] = useState(null);
  const [wecomTesting, setWecomTesting] = useState(false);
  const [emailTo, setEmailTo] = useState('');
  const [emailTestResult, setEmailTestResult] = useState(null);
  const [emailTesting, setEmailTesting] = useState(false);
  const [dingtalkUrl, setDingtalkUrl] = useState('');
  const [dingtalkTestResult, setDingtalkTestResult] = useState(null);
  const [dingtalkTesting, setDingtalkTesting] = useState(false);
  const [webhookUrl, setWebhookUrl] = useState('');
  const [webhookTestResult, setWebhookTestResult] = useState(null);
  const [webhookTesting, setWebhookTesting] = useState(false);
  const actionRef = useRef();
  useEffect(() => { setWecomUrl(store('__wecom_webhook_url') || ''); }, []);

  // ── 规则 CRUD ──
  const handleSaveRule = async (values) => {
    const target = formTarget === 'user:' && formUserId ? `user:${formUserId}` : values.target;
    const payload = {
      ...values,
      target,
      channels: Array.isArray(values.channels) ? JSON.stringify(values.channels) : (values.channels || '["inapp"]'),
    };
    try {
      if (ruleModal?.rule?.id) {
        await authFetch(`${window.__API_BASE__}/notifications/rules/${ruleModal.rule.id}`, {
          method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
        });
      } else {
        await authFetch(window.__API_BASE__ + '/notifications/rules', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
        });
      }
      message.success(ruleModal?.rule?.id ? '规则已更新' : '规则已创建');
      setRuleModal(null);
      actionRef.current?.reload();
    } catch { message.error('操作失败'); }
  };

  const handleDeleteRule = async (id) => {
    await authFetch(`${window.__API_BASE__}/notifications/rules/${id}`, { method: 'DELETE' });
    message.success('规则已删除');
    actionRef.current?.reload();
  };

  const handleToggleRule = async (rule) => {
    await authFetch(`${window.__API_BASE__}/notifications/rules/${rule.id}`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: !rule.enabled }),
    });
    actionRef.current?.reload();
  };

  const openRuleModal = (rule) => {
    setFormEventType(rule?.event_type || 'plan.generated');
    const t = rule?.target || 'owner';
    setFormTarget(t);
    setFormUserId(t.startsWith('user:') ? t.slice(5) : '');
    setRuleModal({
      rule: rule || null,
      form: {
        event_type: rule?.event_type || 'plan.generated',
        condition: rule?.condition || '',
        target: rule?.target || 'owner',
        channels: (() => { try { return JSON.parse(rule?.channels || '["inapp"]'); } catch { return ['inapp']; } })(),
        title_template: rule?.title_template || '',
        body_template: rule?.body_template || '',
        priority: rule?.priority || 0,
        enabled: rule?.enabled !== false,
      },
    });
  };

  // ── 企微测试 ──
  const saveChannelConfig = async (key, value, desc) => {
    try {
      await authFetch(window.__API_BASE__ + '/notifications/channel-configs', {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ items: [{ key, value, description: desc || '' }] }),
      });
    } catch { /* ignore */ }
  };

  const handleWecomTest = async () => {
    setWecomTesting(true);
    setWecomTestResult(null);
    try {
      const resp = await authFetch(window.__API_BASE__ + '/notifications/wecom-test', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ webhook_url: wecomUrl }),
      });
      const data = await resp.json();
      setWecomTestResult(data.ok ? 'success' : 'fail');
      if (data.ok) saveChannelConfig('wecom_webhook', wecomUrl, '企业微信机器人Webhook');
      message[data.ok ? 'success' : 'error'](data.ok ? '企微测试发送成功' : `发送失败: ${data.error || '未知错误'}`);
    } catch { message.error('请求失败'); }
    setWecomTesting(false);
  };

  const handleEmailTest = async () => {
    setEmailTesting(true);
    setEmailTestResult(null);
    try {
      const resp = await authFetch(window.__API_BASE__ + '/notifications/email-test', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: emailTo }),
      });
      const data = await resp.json();
      setEmailTestResult(data.ok ? 'success' : 'fail');
      message[data.ok ? 'success' : 'error'](data.ok ? '邮件发送成功' : `发送失败: ${data.error || ''}`);
    } catch { message.error('请求失败'); }
    setEmailTesting(false);
  };

  const handleDingtalkTest = async () => {
    setDingtalkTesting(true);
    setDingtalkTestResult(null);
    try {
      const resp = await authFetch(window.__API_BASE__ + '/notifications/dingtalk-test', { method: 'POST' });
      const data = await resp.json();
      setDingtalkTestResult(data.ok ? 'success' : 'fail');
      if (data.ok) saveChannelConfig('dingtalk_webhook', dingtalkUrl, '钉钉机器人Webhook');
      message[data.ok ? 'success' : 'error'](data.ok ? '钉钉发送成功' : `发送失败: ${data.error || ''}`);
    } catch { message.error('请求失败'); }
    setDingtalkTesting(false);
  };

  const handleWebhookTest = async () => {
    setWebhookTesting(true);
    setWebhookTestResult(null);
    try {
      const resp = await authFetch(window.__API_BASE__ + '/notifications/webhook-test', { method: 'POST' });
      const data = await resp.json();
      setWebhookTestResult(data.ok ? 'success' : 'fail');
      if (data.ok) saveChannelConfig('webhook_url', webhookUrl, '通用Webhook');
      message[data.ok ? 'success' : 'error'](data.ok ? 'Webhook 发送成功' : `发送失败: ${data.error || ''}`);
    } catch { message.error('请求失败'); }
    setWebhookTesting(false);
  };

  return (
    <div style={{ height: '100%', overflow: 'auto', background: '#fff' }}>
      <div style={{ padding: '16px 24px', borderBottom: '1px solid #f0f0f0', display: 'flex', alignItems: 'center', gap: 12 }}>
        <BellOutlined style={{ fontSize: 18, color: '#6c5ce7' }} />
        <span style={{ fontSize: 16, fontWeight: 600 }}>通知中心</span>
      </div>
      <div style={{ padding: 24 }}>

        <Tabs activeKey={activeTab} onChange={setActiveTab} items={[
          // ═══ Tab 1: 通知规则 (ProTable) ═══
          {
            key: 'rules',
            label: <span><SettingOutlined /> 通知规则</span>,
            children: (
              <ProTable
                actionRef={actionRef}
                columns={RULE_COLUMNS(actionRef, openRuleModal, handleDeleteRule, handleToggleRule, eventTypes, targets, conditions)}
                rowKey="id"
                search={{ labelWidth: 'auto' }}
                options={{ reload: true, density: true }}
                pagination={false}
                headerTitle="通知规则配置"
                toolBarRender={() => [
                  <Button key="add" type="primary" icon={<PlusOutlined />} onClick={() => openRuleModal(null)}>新增规则</Button>,
                  <Button key="reload" icon={<ReloadOutlined />} onClick={() => actionRef.current?.reload()}>刷新</Button>,
                ]}
                request={async () => {
                  const resp = await authFetch(window.__API_BASE__ + '/notifications/rules');
                  const data = await resp.json();
                  return { data: data.items || [], total: data.items?.length || 0, success: true };
                }}
                locale={{ emptyText: '暂无通知规则' }}
              />
            ),
          },

          // ═══ Tab 2: 渠道设置 ═══
          {
            key: 'channels',
            label: <span><SendOutlined /> 渠道设置</span>,
            children: (
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
                <Card title="🔔 应用内通知" size="small" style={{ marginBottom: 16 }}>
                  <div style={{ fontSize: 13, color: '#666', lineHeight: 1.8 }}>
                    应用内通知已内置启用，无需额外配置。<br />
                    用户登录后，顶部 Bell 图标自动显示未读通知。<br />
                    通知通过 SSE 实时推送，页面无需刷新。
                  </div>
                </Card>

                <Card title="📧 邮件通知" size="small" style={{ marginBottom: 16 }}
                  extra={channelCfgs.smtp_host ? <Tag color="green">已配置</Tag> : <Tag>未配置</Tag>}>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                    {[
                      {l:'SMTP服务器',k:'smtp_host',ph:'smtp.company.com'},
                      {l:'端口',k:'smtp_port',ph:'587',w:160},
                      {l:'用户名',k:'smtp_user',ph:'user@company.com'},
                      {l:'密码',k:'smtp_password',ph:'SMTP密码',pwd:true},
                      {l:'发件人',k:'smtp_from',ph:'noreply@company.com'},
                    ].map(f => (
                      <div key={f.k} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <span style={{ width: 70, flexShrink: 0, fontSize: 12, color: '#666', textAlign: 'right' }}>{f.l}</span>
                        {f.pwd
                          ? <Input.Password value={channelCfgs[f.k] || ''} onChange={e => setChannelCfgs(p => ({...p, [f.k]: e.target.value}))} placeholder={f.ph} style={{ flex: 1 }} />
                          : <Input value={channelCfgs[f.k] || ''} onChange={e => setChannelCfgs(p => ({...p, [f.k]: e.target.value}))} placeholder={f.ph} style={{ flex: 1, ...(f.w ? {maxWidth: f.w} : {}) }} />
                        }
                      </div>
                    ))}
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <span style={{ width: 70, flexShrink: 0, fontSize: 12, color: '#666', textAlign: 'right' }}>测试邮箱</span>
                      <Input value={emailTo} onChange={e => setEmailTo(e.target.value)} placeholder="zhangsan@company.com" style={{ flex: 1 }} />
                    </div>
                  </div>
                  <div style={{ marginTop: 12, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <Space>
                      <Button icon={<SendOutlined />} loading={emailTesting} onClick={handleEmailTest}>测试</Button>
                      {emailTestResult === 'success' && <Tag color="green">成功</Tag>}
                      {emailTestResult === 'fail' && <Tag color="red">失败</Tag>}
                    </Space>
                    <Button type="primary" size="small" onClick={() => {
                      const items = [
                        {key:'smtp_host',value:channelCfgs.smtp_host||'',description:'SMTP服务器'},
                        {key:'smtp_port',value:channelCfgs.smtp_port||'',description:'SMTP端口'},
                        {key:'smtp_user',value:channelCfgs.smtp_user||'',description:'SMTP用户名'},
                        {key:'smtp_password',value:channelCfgs.smtp_password||'',description:'SMTP密码'},
                        {key:'smtp_from',value:channelCfgs.smtp_from||'',description:'发件人'},
                      ].filter(i => i.value);
                      authFetch(window.__API_BASE__ + '/notifications/channel-configs', {method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({items})}).then(() => message.success('已保存'));
                    }}>保存</Button>
                  </div>
                </Card>

                <Card title="💬 企业微信机器人" size="small" style={{ marginBottom: 16 }}
                  extra={wecomUrl ? <Tag color="green">已配置</Tag> : <Tag>未配置</Tag>}>
                  <Input value={wecomUrl} onChange={e => setWecomUrl(e.target.value)}
                    placeholder="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=..."
                    style={{ fontFamily: 'monospace', fontSize: 12, marginBottom: 12 }} />
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <Space>
                      <Button icon={<SendOutlined />} loading={wecomTesting} onClick={handleWecomTest}>测试</Button>
                      {wecomTestResult === 'success' && <Tag color="green">成功</Tag>}
                      {wecomTestResult === 'fail' && <Tag color="red">失败</Tag>}
                    </Space>
                    <Button type="primary" size="small" onClick={() => { if(wecomUrl) saveChannelConfig('wecom_webhook', wecomUrl, '企业微信'); message.success('已保存'); }}>保存</Button>
                  </div>
                </Card>

                <Card title="📌 钉钉机器人" size="small" style={{ marginBottom: 16 }}
                  extra={dingtalkUrl ? <Tag color="green">已配置</Tag> : <Tag>未配置</Tag>}>
                  <Input value={dingtalkUrl} onChange={e => setDingtalkUrl(e.target.value)}
                    placeholder="https://oapi.dingtalk.com/robot/send?access_token=..."
                    style={{ fontFamily: 'monospace', fontSize: 12, marginBottom: 12 }} />
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <Space>
                      <Button icon={<SendOutlined />} loading={dingtalkTesting} onClick={handleDingtalkTest}>测试</Button>
                    {dingtalkTestResult === 'success' && <Tag color="green">成功</Tag>}
                    {dingtalkTestResult === 'fail' && <Tag color="red">失败</Tag>}
                  </Space>
                  <Button type="primary" size="small" onClick={() => { if(dingtalkUrl) saveChannelConfig('dingtalk_webhook', dingtalkUrl, '钉钉'); message.success('已保存'); }}>保存</Button>
                </div>
                </Card>

                <Card title="📱 短信通知" size="small" style={{ marginBottom: 16 }}
                  extra={channelCfgs.sms_api_url ? <Tag color="green">已配置</Tag> : <Tag>未配置</Tag>}>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                    {[
                      {l:'网关URL',k:'sms_api_url',ph:'https://sms-api.com/send'},
                      {l:'API Key',k:'sms_api_key',ph:'密钥'},
                    ].map(f => (
                      <div key={f.k} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <span style={{ width: 70, flexShrink: 0, fontSize: 12, color: '#666', textAlign: 'right' }}>{f.l}</span>
                        <Input value={channelCfgs[f.k] || ''} onChange={e => setChannelCfgs(p => ({...p, [f.k]: e.target.value}))} placeholder={f.ph} style={{ flex: 1 }} />
                      </div>
                    ))}
                  </div>
                  <div style={{ marginTop: 12, fontSize: 12, color: '#888' }}>
                    员工手机号从本体图谱员工数据读取。
                  </div>
                  <div style={{ marginTop: 8, textAlign: 'right' }}>
                    <Button type="primary" size="small" onClick={() => {
                      const items = [
                        {key:'sms_api_url',value:channelCfgs.sms_api_url||'',description:'短信网关'},
                        {key:'sms_api_key',value:channelCfgs.sms_api_key||'',description:'短信密钥'},
                      ].filter(i => i.value);
                      authFetch(window.__API_BASE__ + '/notifications/channel-configs', {method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({items})}).then(() => message.success('已保存'));
                    }}>保存</Button>
                  </div>
                </Card>

                <Card title="🔗 通用 Webhook" size="small" style={{ marginBottom: 16 }}
                  extra={webhookUrl ? <Tag color="green">已配置</Tag> : <Tag>未配置</Tag>}>
                  <Input value={webhookUrl} onChange={e => setWebhookUrl(e.target.value)}
                    placeholder="https://your-system.com/api/notify"
                    style={{ fontFamily: 'monospace', fontSize: 12, marginBottom: 12 }} />
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <Space>
                      <Button icon={<SendOutlined />} loading={webhookTesting} onClick={handleWebhookTest}>测试</Button>
                      {webhookTestResult === 'success' && <Tag color="green">成功</Tag>}
                      {webhookTestResult === 'fail' && <Tag color="red">失败</Tag>}
                    </Space>
                    <Button type="primary" size="small" onClick={() => { if(webhookUrl) saveChannelConfig('webhook_url', webhookUrl, '通用Webhook'); message.success('已保存'); }}>保存</Button>
                  </div>
                </Card>
              </div>
            ),
          },
        ]} />
      </div>

      {/* ── 规则编辑弹窗 ── */}
      <Drawer
        title={ruleModal?.rule?.id ? '编辑通知规则' : '新增通知规则'}
        open={!!ruleModal}
        onClose={() => setRuleModal(null)}
        width={480}
        destroyOnClose
      >
        {ruleModal && (
          <Form layout="vertical" initialValues={ruleModal.form} onFinish={handleSaveRule}>
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginBottom: 16 }}>
              <Button onClick={() => setRuleModal(null)}>取消</Button>
              <Button type="primary" htmlType="submit">保存</Button>
            </div>

            <Form.Item label="事件类型" name="event_type" rules={[{ required: true }]} style={{ marginBottom: 12 }}
              help="方案分析完成、执行链完成、需要审批、Schema更新、系统告警">
              <Select
                showSearch
                placeholder="选择事件类型"
                options={eventTypes.map(e => ({ value: e.key, label: `${e.label} — ${e.desc}` }))}
                onChange={(val) => setFormEventType(val)}
              />
            </Form.Item>

            <Form.Item label="触发时机" name="condition" style={{ marginBottom: 12 }}
              help="不选即该事件发生时总是通知，选了则仅在满足条件时通知">
              <Select
                allowClear
                placeholder="总是通知"
                options={(conditions[formEventType] || conditions[ruleModal?.form?.event_type] || []).map(c => ({
                  value: c.key, label: c.label,
                }))}
              />
            </Form.Item>

            <Form.Item label="通知目标" name="target" rules={[{ required: true }]} style={{ marginBottom: 12 }}
              help="对话发起人 / 角色下所有员工 / 指定工号">
              <Select
                showSearch
                placeholder="选择通知目标"
                options={targets.map(t => ({ value: t.key, label: t.key === 'user:' ? `${t.label} — 输入工号` : `${t.label} — ${t.desc}` }))}
                onChange={(val) => setFormTarget(val)}
              />
            </Form.Item>
            {formTarget === 'user:' && (
              <Form.Item label="指定用户" style={{ marginBottom: 12 }} help="搜索员工工号或姓名">
                <Select
                  showSearch
                  placeholder="输入工号或姓名搜索"
                  value={formUserId || undefined}
                  defaultActiveFirstOption={false}
                  onFocus={() => { if (employeeOptions.length === 0) fetchEmployees(); }}
                  onSearch={(val) => fetchEmployees(val)}
                  onChange={(val) => setFormUserId(val)}
                  options={employeeOptions}
                  filterOption={false}
                />
              </Form.Item>
            )}

            <Form.Item label="通知渠道" name="channels" style={{ marginBottom: 12 }}
              help="可多选">
              <Select mode="multiple" placeholder="选择通知渠道" options={CHANNELS.map(c => ({ value: c.key, label: c.label }))} />
            </Form.Item>

            <Form.Item label="标题模板" name="title_template" rules={[{ required: true }]} style={{ marginBottom: 4 }}>
              <Input placeholder="方案缺少 {missing_actions_count} 个操作" />
            </Form.Item>
            <Form.Item label="正文模板" name="body_template" rules={[{ required: true }]} style={{ marginBottom: 8 }}>
              <TextArea rows={3} placeholder="方案「{plan_label}」需要创建操作：{missing_actions_list}" />
            </Form.Item>
            <Form.Item label="可用变量" style={{ marginBottom: 8 }}>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                {(EVENT_VARS[formEventType] || EVENT_VARS['plan.generated']).map(v => (
                  <Tag key={v} color="blue" style={{ cursor: 'pointer', margin: 0, fontSize: 12, lineHeight: '22px' }}
                    onClick={() => {
                      const ta = document.querySelector('.ant-drawer textarea');
                      if (ta) { const s = ta.selectionStart; const val = ta.value; ta.value = val.slice(0, s) + '{' + v + '}' + val.slice(s); ta.focus(); ta.setSelectionRange(s + v.length + 2, s + v.length + 2); ta.dispatchEvent(new Event('input', { bubbles: true })); }
                    }}
                  >{'{'}{v}{'}'} {VAR_DESC[v] || ''}</Tag>
                ))}
              </div>
            </Form.Item>
            <Form.Item noStyle shouldUpdate>
              {({ getFieldValue }) => {
                const t = (getFieldValue('title_template') || '通知标题').replace(/\{(\w+)\}/g, (_, k) => PREVIEW_VALUES[k] || '{' + k + '}');
                const b = (getFieldValue('body_template') || '通知正文').replace(/\{(\w+)\}/g, (_, k) => PREVIEW_VALUES[k] || '{' + k + '}');
                return (
                  <Form.Item label="预览效果" style={{ marginBottom: 12 }}>
                    <div style={{ padding: '10px 14px', background: '#f6ffed', borderRadius: 6, border: '1px solid #b7eb8f' }}>
                      <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 4 }}>{t}</div>
                      <div style={{ fontSize: 13, color: '#555' }}>{b}</div>
                    </div>
                  </Form.Item>
                );
              }}
            </Form.Item>

            <Form.Item label="优先级" name="priority" style={{ marginBottom: 16 }}
              help="数字越大越优先">
              <Input type="number" style={{ width: 80 }} min={0} max={100} />
            </Form.Item>

          </Form>
        )}
      </Drawer>
    </div>
  );
}
