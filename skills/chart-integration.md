# Chart Integration Skill

## 描述
为React应用集成ECharts和Mermaid图表渲染能力,支持LLM输出图表代码块自动渲染。

## 触发条件
用户需要在聊天/文档应用中支持图表展示时。

## 实现步骤

### 1. 安装依赖
```bash
npm install echarts mermaid --save
```

### 2. MarkdownRenderer集成
修改`MarkdownRenderer.jsx`,添加图表代码块检测和渲染:

```javascript
// ECharts/Mermaid懒加载
async function loadECharts() {
  const mod = await import('echarts');
  return mod.default || mod;
}

async function loadMermaid() {
  const mod = await import('mermaid');
  const mermaid = mod.default;
  mermaid.initialize({ startOnLoad: false, theme: 'default', securityLevel: 'loose' });
  return mermaid;
}

// 自定义marked renderer
renderer.code = function(code, language) {
  if (language === 'echarts') {
    const encoded = btoa(unescape(encodeURIComponent(code)));
    return `<div class="echarts-chart" data-chart-source="${encoded}" style="width:100%;height:400px;"></div>`;
  }
  if (language === 'mermaid') {
    const encoded = btoa(unescape(encodeURIComponent(code)));
    return `<div class="mermaid-chart" data-mermaid-source="${encoded}"></div>`;
  }
  return originalCodeRenderer(code, language);
};
```

### 3. 流式输出优化
- 添加`streaming`属性,流式输出中不渲染图表
- 输出完成后(`streaming=false`)才触发图表渲染
- 避免JSON不完整导致的渲染错误

### 4. 后端Prompt配置
```python
DEFAULT_SYSTEM_PROMPT = """
1. 数据图表(柱状图/折线图/饼图等)使用```echarts代码块,内容为echarts option JSON
2. 关系图(流程图/时序图等)使用```mermaid代码块
"""
```

## 支持的图表类型

**ECharts**: 柱状图、折线图、饼图、散点图、雷达图、地图、热力图、K线图、仪表盘、桑基图等

**Mermaid**: 流程图、时序图、类图、状态图、ER图、甘特图、思维导图、架构图等

## 注意事项
- 两个库都是懒加载,不影响首屏性能
- 流式输出时需等待JSON完整后再渲染
- Mermaid渲染失败时显示原始代码而非错误信息
