# Web Search Tool Skill

## 描述
为Agent添加联网搜索能力,支持实时获取网络信息。

## 触发条件
用户需要查询实时信息、新闻、最新数据等LLM知识库中没有的内容时。

## 实现步骤

### 1. 创建搜索工具
```python
# backend/app/tools/search_tool.py
class SearchTool:
    async def search(self, query: str, max_results: int = 5) -> List[Dict]:
        # 使用Bing/Google搜索
        # 返回: [{title, link, snippet}, ...]
```

### 2. 搜索实现
```python
import requests
from bs4 import BeautifulSoup

async def search(self, query: str, max_results: int = 5):
    # Bing搜索
    url = f"https://www.bing.com/search?q={query}"
    headers = {"User-Agent": "Mozilla/5.0..."}
    
    response = requests.get(url, headers=headers)
    soup = BeautifulSoup(response.text, 'html.parser')
    
    # 解析搜索结果
    results = []
    for item in soup.select('.b_algo')[:max_results]:
        results.append({
            "title": item.select_one('h2').text,
            "link": item.select_one('a')['href'],
            "snippet": item.select_one('.b_caption p').text
        })
    
    return results
```

### 3. 集成到Agent
```python
@tool
async def search_web(query: str) -> str:
    """联网搜索工具"""
    from app.tools.search_tool import search_tool
    results = await search_tool.search(query)
    return search_tool.format_results(results)

self.agent = create_react_agent(self.llm, [think, search_web])
```

### 4. 前端集成
```javascript
// 搜索按钮
<Button 
  icon={<SearchOutlined />} 
  onClick={handleWebSearch}
  loading={searching}
>
  联网搜索
</Button>

// 搜索结果展示
const handleWebSearch = async () => {
  const results = await api.webSearch(inputValue);
  // 将结果注入到消息中
};
```

## 搜索引擎选择

**Bing**: 
- 优点: 无需API Key,直接爬取
- 缺点: 中文企业名效果不佳,可能被反爬

**Google**:
- 优点: 搜索质量高
- 缺点: 需要API Key,国内访问受限

**SerpAPI**:
- 优点: 统一接口,支持多搜索引擎
- 缺点: 付费服务

## 返回格式
```json
[
  {
    "title": "搜索结果标题",
    "link": "https://example.com",
    "snippet": "内容摘要..."
  }
]
```

## 注意事项
- 添加User-Agent避免被反爬
- 设置超时时间
- 结果缓存避免重复请求
- 敏感词过滤
