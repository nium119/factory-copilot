# Streaming Output Optimization Skill

## 描述
优化LLM流式输出体验,解决图表渲染、内容抖动、重复渲染等问题。

## 触发条件
用户反馈流式输出时图表不显示、页面抖动、内容闪烁等问题时。

## 常见问题与解决方案

### 问题1: 图表不显示
**原因**: 流式输出中JSON/DSL代码不完整,渲染失败后标记为"已渲染"

**解决**: 
```javascript
// 不在流式输出中渲染图表
function MarkdownRenderer({ content, streaming = false }) {
  useEffect(() => {
    if (streaming) {
      chartsRenderedRef.current = false;
      return; // 跳过渲染
    }
    // streaming=false时才渲染图表
    renderCharts();
  }, [content, streaming]);
}
```

### 问题2: 页面抖动
**原因**: 每次content变化都销毁+重建图表实例

**解决**:
```javascript
// 只在组件卸载时销毁
useEffect(() => {
  // 渲染图表
  return () => {
    // 只在卸载时销毁
    chartInstances.forEach(chart => chart.dispose());
  };
}, []); // 空依赖数组
```

### 问题3: Mermaid语法错误
**原因**: mermaid.render()失败时会在DOM中插入错误元素

**解决**:
```javascript
// 先验证语法
const isValid = mermaid.parse(code);
if (!isValid) {
  // 显示原始代码
  return `<pre>${code}</pre>`;
}

// 清理错误元素
document.querySelectorAll('[id^="dmermaid-svg-"]').forEach(el => el.remove());
```

### 问题4: HTML特殊字符转义
**原因**: 图表代码直接放入innerHTML,特殊字符被解析

**解决**:
```javascript
// Base64编码
const encoded = btoa(unescape(encodeURIComponent(code)));
// 存入data属性
return `<div data-chart-source="${encoded}"></div>`;

// 解码
const code = decodeURIComponent(escape(atob(encoded)));
```

## 最佳实践

1. **延迟渲染**: 流式输出完成后再渲染图表
2. **懒加载**: ECharts/Mermaid按需加载,不影响首屏
3. **错误处理**: 渲染失败时显示原始代码
4. **实例管理**: 避免频繁创建/销毁图表实例
5. **编码处理**: Base64编码避免HTML转义问题

## ChatInterface集成
```javascript
// 传递streaming状态
<MarkdownRenderer 
  content={item.content} 
  streaming={item.thinking}  // thinking=true表示正在输出
/>
```
