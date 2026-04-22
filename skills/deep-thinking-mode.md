# Deep Thinking Mode Skill

## 描述
为Agent添加深度思考能力,支持复杂问题的分步推理和思考过程展示。

## 触发条件
用户需要处理复杂问题、多步骤推理、需要展示思考过程时。

## 实现步骤

### 1. 后端思考工具
```python
@tool
def think(query: str) -> str:
    """用于深度思考和分析问题"""
    return query

self.agent = create_react_agent(self.llm, [think, search_web, query_enterprise])
```

### 2. 系统Prompt配置
```python
DEFAULT_SYSTEM_PROMPT = """
你是一个专业的AI助手。回答问题时请遵循以下规则:

1. 对于复杂问题,先使用think工具进行深度思考
2. 思考过程要分步骤、有逻辑
3. 思考完成后再给出最终答案

思考模式示例:
- 用户: "分析A公司和B公司的优劣势"
- Agent: 
  1. 调用think工具思考分析框架
  2. 调用query_enterprise查询A公司信息
  3. 调用query_enterprise查询B公司信息
  4. 对比分析并给出结论
"""
```

### 3. 前端思考过程展示
```javascript
// 消息结构
{
  role: 'assistant',
  content: '最终答案',
  thinking: '思考过程...',  // 思考内容
  thinkingExpanded: false   // 折叠状态
}

// 思考过程折叠显示
<Collapse ghost>
  <Collapse.Panel 
    header="💭 思考过程" 
    key="thinking"
  >
    <MarkdownRenderer content={item.thinking} />
  </Collapse.Panel>
</Collapse>

// 最终答案
<MarkdownRenderer content={item.content} />
```

### 4. 流式输出处理
```python
async def chat_stream(self, message: str):
    async for chunk in self.agent.astream(...):
        # 区分思考过程和最终答案
        if chunk.get('type') == 'thinking':
            yield f"data: {json.dumps({'thinking': chunk['content']})}\n\n"
        else:
            yield f"data: {json.dumps({'content': chunk['content']})}\n\n"
```

## 思考模式触发条件

自动触发:
- 问题包含"分析"、"比较"、"评估"等关键词
- 需要多步骤推理
- 需要调用多个工具

手动触发:
- 用户明确要求"详细思考"
- 用户要求"展示推理过程"

## UI展示

```
┌─────────────────────────────────┐
│ 💭 思考过程 ▼                   │
├─────────────────────────────────┤
│ 1. 首先查询A公司信息...         │
│ 2. 然后查询B公司信息...         │
│ 3. 对比分析发现...              │
└─────────────────────────────────┘

最终答案内容...
```

## 注意事项
- 思考过程默认折叠,避免干扰阅读
- 思考过程不计入最终答案长度
- 支持Markdown格式
- 流式输出时实时更新思考过程
