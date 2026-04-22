"""Prompt配置文件 - 集中管理所有系统提示词"""


# ============================================
# 系统提示词
# ============================================

# 默认系统提示词 - 支持图表渲染
DEFAULT_SYSTEM_PROMPT = """你是一个专业的AI助手。回答问题时请遵循以下规则:
1. 当需要展示数据图表(柱状图、折线图、饼图、散点图等)时,使用echarts代码块(```echarts),内容为合法的echarts option JSON
2. 当需要展示流程、架构、时序等关系图时,使用mermaid代码块(```mermaid)
3. echarts代码块示例:
```echarts
{
  "xAxis": {"type": "category", "data": ["A","B","C"]},
  "yAxis": {"type": "value"},
  "series": [{"type": "bar", "data": [10,20,30]}]
}
```
4. 回复使用中文,内容清晰有条理"""

# 简单系统提示词 - 基础对话
SIMPLE_SYSTEM_PROMPT = "你是一个有帮助的AI助手,请用中文回答问题。"


# ============================================
# 工具增强提示词模板
# ============================================

# 联网搜索增强提示词模板
# 使用变量: {search_context}, {message}
WEB_SEARCH_ENHANCED_PROMPT = """基于以下搜索结果回答用户问题。如果搜索结果不足以回答问题，请说明并给出你自己的分析。

搜索结果:
{search_context}

用户问题: {message}"""

# 企业信息查询增强提示词模板
# 使用变量: {enterprise_context}, {message}
ENTERPRISE_QUERY_ENHANCED_PROMPT = """基于以下企业信息回答用户问题。如果信息不足以回答问题，请说明。

企业信息:
{enterprise_context}

用户问题: {message}"""


# ============================================
# 思考过程提示词
# ============================================

# 深度思考工具描述
THINK_TOOL_DESCRIPTION = "用于深度思考和分析问题"


# ============================================
# 工具调用状态提示
# ============================================

# 搜索开始提示
# 使用变量: {query}
SEARCH_START_PROMPT = "正在搜索: {query}\n\n"

# 搜索结果提示
# 使用变量: {count}
SEARCH_RESULT_COUNT_PROMPT = "搜索到 {count} 条结果:\n\n"

# 搜索无结果提示
SEARCH_NO_RESULT_PROMPT = "未找到相关搜索结果\n\n"

# 企业查询开始提示
# 使用变量: {company_name}
ENTERPRISE_QUERY_START_PROMPT = "正在查询企业信息: {company_name}\n\n"

# 企业查询成功提示
ENTERPRISE_QUERY_SUCCESS_PROMPT = "查询成功，获取到企业信息:\n\n"

# 企业查询失败提示
# 使用变量: {error}
ENTERPRISE_QUERY_ERROR_PROMPT = "查询失败: {error}\n\n"


# ============================================
# Prompt 辅助函数
# ============================================

def format_web_search_prompt(search_context: str, message: str) -> str:
    """
    格式化联网搜索增强提示词
    
    Args:
        search_context: 搜索结果上下文
        message: 用户消息
        
    Returns:
        格式化后的提示词
    """
    return WEB_SEARCH_ENHANCED_PROMPT.format(
        search_context=search_context,
        message=message
    )


def format_enterprise_query_prompt(enterprise_context: str, message: str) -> str:
    """
    格式化企业信息查询增强提示词
    
    Args:
        enterprise_context: 企业信息上下文
        message: 用户消息
        
    Returns:
        格式化后的提示词
    """
    return ENTERPRISE_QUERY_ENHANCED_PROMPT.format(
        enterprise_context=enterprise_context,
        message=message
    )


def get_system_prompt(use_enhanced: bool = True) -> str:
    """
    获取系统提示词

    Args:
        use_enhanced: 是否使用增强版(支持图表渲染)

    Returns:
        系统提示词
    """
    if use_enhanced:
        return DEFAULT_SYSTEM_PROMPT
    return SIMPLE_SYSTEM_PROMPT


# ============================================
# 摘要压缩提示词
# ============================================

SUMMARY_PROMPT = """你是一个信息摘要专家。请对以下对话历史进行简洁摘要，保留对后续对话有用的关键信息。

{existing_summary_header}
{existing_summary}

{old_messages_header}
{old_messages}

请生成一份摘要，要求：
1. 用中文
2. 控制在{max_tokens}字以内
3. 保留用户需求、重要决策、技术细节等关键信息
4. 如果是更新已有摘要，请整合新信息与旧摘要
5. 去掉寒暄、重复内容等无关信息

摘要："""


def format_summary_prompt(
    old_messages: str,
    existing_summary: str = "",
    max_tokens: int = 500
) -> str:
    """
    格式化摘要压缩提示词

    Args:
        old_messages: 旧对话消息
        existing_summary: 已有摘要（首次压缩时为空）
        max_tokens: 最大字数

    Returns:
        格式化后的提示词
    """
    existing_header = "## 已有摘要\n\n" if existing_summary else ""
    old_messages_header = "## 新对话内容\n\n" if existing_summary else "## 对话内容\n\n"

    return SUMMARY_PROMPT.format(
        existing_summary_header=existing_header,
        existing_summary=existing_summary,
        old_messages_header=old_messages_header,
        old_messages=old_messages,
        max_tokens=max_tokens
    )
