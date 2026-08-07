import React, { useState, useEffect } from 'react';
import {
  Button, Form, Input, Select, Switch, Space, Tag, message, TreeSelect, Radio,
} from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import request from '../../services/request';

// ── 触发词预设 ──
export const TRIGGER_EXAMPLES = {
  fault_diagnosis: ['故障.*诊断', '设备.*故障', '设备.*坏', '设备.*异常', '停机.*原因', '诊断.*故障'],
  quality_analysis: ['质量.*分析', '缺陷.*分析', '不良.*分析', '质检.*分析', '质量.*改善', '质量.*改进'],
  work_order_readiness: ['生产准备', '投产准备', '齐套检查', '开工检查', '准备检查', '工单.*准备'],
  production_report: ['生产.*报告', '综合.*报告', '生产.*总结', '车间.*报告', '产线.*报告', '综合分析.*生产'],
};

export const TRIGGER_PRESET_NAMES = {
  fault_diagnosis: '设备故障诊断',
  quality_analysis: '质量分析',
  work_order_readiness: '工单准备检查',
  production_report: '生产综合报告',
};

export const TEMPLATE_PRESETS = {
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

/**
 * ChainForm — 链条编辑表单（共用组件，不含 Drawer）
 *
 * 参考 MES 项目模式：表单只负责内容，Drawer 由父组件管理。
 *
 * @param {object|null} record - 编辑中的链条（null = 新建）
 * @param {Array} agents - 可选 Agent 列表
 * @param {Function} onCancel - 取消回调
 * @param {Function} onSuccess - 保存成功回调
 */

/** 执行链步骤字段 — 独立组件以支持 Form.useWatch */
function PipelineStepFields({ name, rest, actionList, conceptLabelMap }) {
  const form = Form.useFormInstance();
  const stepConcepts = Form.useWatch(['steps', name, 'focus_concepts'], form) || '';
  const concepts = stepConcepts.split(',').filter(Boolean);
  const filtered = concepts.length > 0
    ? actionList.filter(a => concepts.includes(a.conceptName))
    : actionList;
  const displayList = filtered.length > 0 ? filtered : actionList;  // 过滤无结果时回退全部

  return (
    <>
      <Form.Item {...rest} name={[name, 'action_name']} label="执行Action" style={{ marginBottom: 8 }}
        help={concepts.length > 0
          ? (filtered.length > 0 ? `已按概念 [${concepts.join(', ')}] 过滤，${filtered.length} 个 Action` : `概念 [${concepts.join(', ')}] 无 Action，显示全部`)
          : '选「数据范围」概念可过滤，也可直接搜索'}>
        <Select placeholder="选择 Action..."
          showSearch
          filterOption={(input, option) => (option?.label ?? '').toLowerCase().includes(input.toLowerCase())}
          options={displayList.map(a => ({
            value: a.name,
            label: `${conceptLabelMap[a.conceptName] || a.conceptName || 'MCP'}.${a.label || a.name}`,
          }))}
        />
      </Form.Item>
      <Form.Item noStyle shouldUpdate={(prev, cur) => {
        const a = prev.steps?.[name]?.action_name; const b = cur.steps?.[name]?.action_name;
        return a !== b;
      }}>
        {({ getFieldValue }) => {
          const an = getFieldValue(['steps', name, 'action_name']);
          const actionParams = (actionList.find(a => a.name === an) || {}).params || [];
          const prevParams = getFieldValue(['steps', name, 'action_params']);
          let currentParams = {};
          try { currentParams = typeof prevParams === 'string' ? JSON.parse(prevParams || '{}') : (prevParams || {}); } catch {}
          return (
            <>
              {actionParams.length > 0 && (
                <div style={{ marginBottom: 8 }}>
                  <div style={{ fontSize: 12, color: '#999', marginBottom: 4 }}>参数（逐个填写，留空自动映射）</div>
                  {actionParams.map(p => (
                    <Form.Item key={p.name} style={{ marginBottom: 4 }} label={
                      <span style={{ fontSize: 12 }}>{p.label || p.name}{p.required ? ' *' : ''}</span>
                    } labelCol={{ style: { paddingRight: 4 } }}>
                      <Input size="small" placeholder={p.required ? '必填' : '可选'}
                        value={currentParams[p.name] || ''}
                        onChange={e => {
                          const newParams = { ...currentParams, [p.name]: e.target.value };
                          form.setFieldValue(['steps', name, 'action_params'], JSON.stringify(newParams));
                        }}
                      />
                    </Form.Item>
                  ))}
                </div>
              )}
              <Form.Item {...rest} name={[name, 'action_params']} label="参数模板" style={{ marginBottom: 8 }}
                help={<span>留空 <code>{'{}'}</code> 自动映射。跨步骤引用 <code>{'{{步骤ID.字段}}'}</code></span>}>
                <Input.TextArea rows={2} placeholder='{"materialCode": "{{plan.物料编码}}"}' style={{ fontFamily: 'monospace', fontSize: 11 }} />
              </Form.Item>
            </>
          );
        }}
      </Form.Item>
      <Form.Item {...rest} name={[name, 'precondition']} label="前置条件" style={{ marginBottom: 8 }}
        help="表达式，不满足则中止。如 {{prev.in_progress}} == 0。留空跳过">
        <Input placeholder='{{check_dispatch.in_progress}} == 0' style={{ fontFamily: 'monospace' }} />
      </Form.Item>
      <Form.Item {...rest} name={[name, 'on_failure']} label="失败处理" style={{ marginBottom: 0 }} initialValue="abort">
        <Select size="small" options={[
          { value: 'abort', label: '中止' },
          { value: 'skip', label: '跳过' },
          { value: 'retry', label: '重试' },
        ]} />
      </Form.Item>
    </>
  );
}


export default function ChainForm({ record, agents = [], onCancel, onSuccess }) {
  const [form] = Form.useForm();
  const [saving, setSaving] = useState(false);
  const watchMode = Form.useWatch('mode', form);
  const [conceptList, setConceptList] = useState([]);
  const [conceptLabelMap, setConceptLabelMap] = useState({});  // conceptName → conceptLabel
  const [actionList, setActionList] = useState([]);

  // 加载 Action 列表（供执行链选择）
  useEffect(() => {
    request.get('/chains/actions').then(data => {
      setActionList(data || []);
    }).catch(() => {});
  }, []);

  // 加载概念树 + 构建 name→label 映射
  useEffect(() => {
    request.get('/chains/concepts').then(data => {
      const list = data || [];
      const labelMap = {};
      const map = {};
      for (const c of list) {
        labelMap[c.name] = c.label || c.name;
        map[c.name] = { value: c.name, title: `${c.label || c.name} (${c.name})`, children: [] };
      }
      setConceptLabelMap(labelMap);
      const roots = [];
      for (const c of list) {
        const node = map[c.name];
        if (c.parents && c.parents.length > 0) {
          let parent = map[c.parents[0]];
          if (!parent) {
            parent = { value: c.parents[0], title: c.parents[0], children: [] };
            map[c.parents[0]] = parent;
            roots.push(parent);
          }
          parent.children.push(node);
        } else {
          roots.push(node);
        }
      }
      setConceptList(roots);
    }).catch(() => {});
  }, []);

  // 初始化表单：record 为 null 时是新建
  useEffect(() => {
    if (record) {
      const hasSteps = (record.steps || []).length > 0;
      form.setFieldsValue({
        chain_id: record.chain_id, name: record.name, description: record.description,
        triggers: (record.triggers || []).join('\n'),
        final_prompt_template: record.final_prompt_template || '',
        focus_concepts: record.focus_concepts || '',
        verify_target: typeof record.verify_target === 'string' ? record.verify_target : (record.verify_target ? JSON.stringify(record.verify_target, null, 2) : ''),
        enabled: record.enabled !== false,
        mode: record.mode || (hasSteps ? 'chained' : 'merged'),
        steps: record.steps || [],
      });
    } else {
      form.resetFields();
      form.setFieldsValue({ enabled: true, mode: 'merged', steps: [], focus_concepts: '' });
    }
  }, [record, form]);

  const handleSave = async () => {
    try {
      const values = await form.validateFields(); setSaving(true);
      const payload = {
        chain_id: values.chain_id, name: values.name || '', description: values.description || '',
        triggers: (values.triggers || '').split('\n').map(s => s.trim()).filter(Boolean),
        final_prompt_template: values.final_prompt_template || '',
        focus_concepts: values.focus_concepts || '',
        verify_target: values.verify_target || '',
        enabled: values.enabled,
        steps: (values.steps || []).map((s, i) => ({ agent_name: 'analysis_monitor', ...s, step_order: i })),
      };
      if (record && record.chain_id) {
        await request.put(`/chains/${encodeURIComponent(record.chain_id)}`, payload);
        message.success('已更新');
      } else {
        await request.post('/chains', payload);
        message.success('已创建');
      }
      onSuccess?.();
    } catch (err) {
      if (err?.errorFields) return;
      message.error(err?.response?.data?.detail || '保存失败');
    } finally { setSaving(false); }
  };

  const handleTriggerPreset = (k) => {
    if (TRIGGER_EXAMPLES[k]) form.setFieldsValue({ triggers: TRIGGER_EXAMPLES[k].join('\n') });
  };

  return (
    <div>
      <div style={{ marginBottom: 16, textAlign: 'right' }}>
        <Space>
          <Button onClick={onCancel}>取消</Button>
          <Button type="primary" loading={saving} onClick={handleSave}>保存</Button>
        </Space>
      </div>
      <Form form={form} layout="vertical">
        <Space direction="vertical" style={{ width: '100%' }} size="small">
          <Space.Compact block>
            <Form.Item name="chain_id" label="链条标识" rules={[{ required: true }]} style={{ flex: 1 }}>
              <Input placeholder="英文标识，如 fault_diagnosis" disabled={!!(record && record.chain_id)} />
            </Form.Item>
            <Form.Item name="name" label="显示名称" rules={[{ required: true }]} style={{ flex: 2 }}>
              <Input placeholder="中文名称，如 设备故障诊断" />
            </Form.Item>
          </Space.Compact>
          <Form.Item name="description" label="功能描述">
            <Input.TextArea rows={2} placeholder="简要说明这条链条的用途和触发场景" />
          </Form.Item>
          <Form.Item name="mode" label="推理模式" initialValue="merged"
            help="合并：一次 LLM 输出完整报告。链式：逐步推理。Pipeline：确定性分步执行Action，不依赖LLM。">
            <Radio.Group>
              <Radio.Button value="merged">合并</Radio.Button>
              <Radio.Button value="chained">链式</Radio.Button>
              <Radio.Button value="pipeline">执行链</Radio.Button>
            </Radio.Group>
          </Form.Item>
          {watchMode === 'merged' && (
            <Form.Item name="focus_concepts" label="数据范围" help="选择要查询哪些概念的数据。留空则自动从用户消息中提取。"
              getValueFromEvent={(v) => Array.isArray(v) ? v.join(',') : v}
              getValueProps={(v) => ({ value: v ? v.split(',').filter(Boolean) : [] })}>
              <TreeSelect treeData={conceptList} size="small" placeholder="选择概念..."
                treeCheckable showSearch treeNodeFilterProp="title"
                style={{ minWidth: 200 }} maxTagCount={3}
              />
            </Form.Item>
          )}
          {/* 链式 / Pipeline 模式：步骤编辑 */}
          {(watchMode === 'chained' || watchMode === 'pipeline') && (
          <Form.List name="steps">
            {(fields, { add, remove, move }) => (
              <>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
                  <strong>{watchMode === 'pipeline' ? '⚡ 执行步骤' : '🧠 推理步骤'}</strong>
                  <span style={{ fontSize: 11, color: '#999' }}>
                    {watchMode === 'pipeline' ? '每步直接调用 Action，不依赖 LLM' : '每步由 LLM 推理，输出作为下一步输入'}
                  </span>
                  <Button type="dashed" size="small" icon={<PlusOutlined />} onClick={() => add({ step_id: '', description: '', prompt_template: '', output_key: '', action_name: '', action_params: '{}', precondition: '', on_failure: 'abort' })}>添加步骤</Button>
                </div>
                {fields.length === 0 && <div style={{ color: '#999', fontSize: 13, marginBottom: 12 }}>暂未添加{watchMode === 'pipeline' ? '执行' : '推理'}步骤</div>}
                {fields.map(({ key, name, ...rest }) => (
                  <div key={key} style={{ border: `1px solid ${watchMode === 'pipeline' ? '#1677ff30' : '#e8e8ec'}`, borderRadius: 8, padding: 16, marginBottom: 12, background: watchMode === 'pipeline' ? '#f0f5ff' : '#fafafa', position: 'relative' }}>
                    <div style={{ position: 'absolute', top: 8, right: 8, zIndex: 1 }}>
                      <Space size={4}>
                        {name > 0 && <Button size="small" onClick={() => move(name, name - 1)}>↑ 上移</Button>}
                        {name < fields.length - 1 && <Button size="small" onClick={() => move(name, name + 1)}>↓ 下移</Button>}
                        <Button size="small" danger onClick={() => remove(name)}>删除</Button>
                      </Space>
                    </div>
                    <div style={{ fontSize: 12, color: watchMode === 'pipeline' ? '#1677ff' : '#999', marginBottom: 8, fontWeight: 500 }}>
                      {watchMode === 'pipeline' ? '⚡' : '🧠'} 步骤 {name + 1}
                    </div>
                    <Space.Compact block style={{ marginBottom: 8 }}>
                      <Form.Item {...rest} name={[name, 'step_id']} label="步骤标识" style={{ flex: 1, marginBottom: 0 }}>
                        <Input placeholder="英文标识，如 fault_check" />
                      </Form.Item>
                      <Form.Item {...rest} name={[name, 'description']} label="步骤说明" style={{ flex: 2, marginBottom: 0 }}>
                        <Input placeholder="中文说明，如 故障情况检查" />
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
                    {watchMode === 'pipeline' ? (
                      <PipelineStepFields name={name} rest={rest} actionList={actionList} conceptLabelMap={conceptLabelMap} />
                    ) : (
                      <Form.Item {...rest} name={[name, 'prompt_template']} label="推理提示词" style={{ marginBottom: 0 }}
                        help="固定变量: {message} 用户消息、{data_context} 数据查询结果。之前步骤的 output_key 也可作为变量">
                        <Input.TextArea rows={4} placeholder="根据以下数据检查设备故障情况:\n\n数据: {data_context}\n用户问题: {message}\n\n请给出诊断结论。" style={{ fontFamily: 'monospace' }} />
                      </Form.Item>
                    )}
                  </div>
                ))}
              </>
            )}
          </Form.List>
          )}
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
                <div style={{ marginBottom: 16 }}>
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
                ? "步骤1: 设备诊断报告...\n\n用户消息: {message}\n\n数据: {data_context}\n\n请给出诊断结论。"
                : "点击上方「日报模板」快捷填入，或自定义格式。\n可用变量：{message} = 用户问题，{data_context} = 查询到的数据"}
              style={{ fontFamily: 'monospace' }} />
          </Form.Item>
          <Form.Item name="verify_target"
            label={<span>回滚后验证目标 <Tag color="orange" style={{ marginLeft: 4, fontSize: 11 }}>回滚链专用</Tag></span>}
            help={"回滚链（xxx_rollback）声明回滚后的期望状态，回滚执行后硬取实际值对比验证。格式 JSON：{\"concept\":\"概念名\",\"property\":\"属性名\",\"expected\":\"期望值\",\"label\":\"中文说明\"}。留空则回滚不验证。"}>
            <Input.TextArea rows={3}
              placeholder={'{"concept": "WorkOrderBOMItem", "property": "status", "expected": "已回滚", "label": "BOM状态已恢复"}'}
              style={{ fontFamily: 'monospace', fontSize: 11 }} />
          </Form.Item>
        </Space>
      </Form>
    </div>
  );
}
