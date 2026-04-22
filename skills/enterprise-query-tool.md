# Enterprise Query Tool Skill

## 描述
为企业Agent添加企业信息查询能力,支持三级查询策略:企业API → 网页搜索 → LLM知识。

## 触发条件
用户需要查询企业工商信息、股东信息、经营状况等时。

## 实现步骤

### 1. 创建企业工具
```python
# backend/app/tools/enterprise_tool.py
class EnterpriseTool:
    async def query(self, company_name: str, query_type: str = "basic") -> Dict:
        # 1. 尝试企业API (企查查/天眼查)
        # 2. 网页搜索工具
        # 3. 返回LLM提示
```

### 2. 三级查询策略

**第一级: 企业API**
- 配置企查查/天眼查API Key
- 调用真实API获取结构化数据
- 返回: 注册资本、法人、成立日期、经营范围等

**第二级: 网页搜索**
- 多策略搜索: "企业名 企查查"、"企业名 天眼查"、"企业名 工商信息"
- 结果去重
- 相关性过滤: 必须包含企业核心名称
- 返回搜索摘要

**第三级: LLM知识**
- 搜索无相关结果时返回提示
- 让LLM用自身知识回答

### 3. 集成到Agent工具链
```python
@tool
async def query_enterprise(company_name: str) -> str:
    """企业信息查询工具"""
    from app.tools.enterprise_tool import enterprise_tool
    result = await enterprise_tool.query(company_name)
    return enterprise_tool.format_result(result)

self.agent = create_react_agent(self.llm, [think, search_web, query_enterprise])
```

### 4. 相关性过滤
```python
# 提取企业核心名称
company_keywords = clean_name
for suffix in ['有限公司', '股份有限公司', '有限责任公司', '集团']:
    company_keywords = company_keywords.replace(suffix, '')

# 检查搜索结果是否包含企业名
if company_keywords in title or company_keywords in snippet:
    relevant_results.append(r)
```

## 返回格式
```json
{
  "success": true/false,
  "source": "api/web_search/llm",
  "info": { ... },
  "message": "提示信息"
}
```

## 注意事项
- 企业API需要付费,未配置时跳过
- Bing搜索对中文企业名效果不佳,建议配置Google或企业API
- 相关性过滤避免返回无关搜索结果
