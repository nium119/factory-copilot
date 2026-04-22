from app.core.logger import log
from app.core.exceptions import AgentException
from app.services.llm_service import llm_service
from app.tools.search_tool import search_tool
from app.tools.enterprise_tool import enterprise_tool
from typing import Optional, Dict, Any, List
import re

class AgentService:
    """Agent服务 - 集成LangChain和工具"""

    def __init__(self):
        self.conversations = {}  # 会话存储(用于兼容旧接口)
        self.tools = {
            "search": search_tool,
            "enterprise": enterprise_tool
        }

    async def process_message(
        self,
        content: str,
        session_id: str,
        context: Optional[Dict[str, Any]] = None,
        model_name: str = None,
        use_agent: bool = False
    ) -> str:
        """
        处理消息

        Args:
            content: 消息内容
            session_id: 会话ID
            context: 上下文信息
            model_name: 模型名称
            use_agent: 是否使用Agent模式

        Returns:
            Agent响应内容
        """
        try:
            log.info(f"处理消息 - 会话: {session_id}, 模型: {model_name}, Agent模式: {use_agent}, 内容: {content[:50]}...")

            # 检查是否需要调用工具
            tool_result = await self._check_and_call_tools(content)
            
            # 如果调用了工具,将结果添加到消息中
            enhanced_content = content
            if tool_result:
                enhanced_content = f"{content}\n\n工具调用结果:\n{tool_result}"

            # 获取系统提示词
            system_prompt = None
            if context and "system_prompt" in context:
                system_prompt = context["system_prompt"]

            # 调用LLM服务
            response = await llm_service.chat(
                message=enhanced_content,
                session_id=session_id,
                system_prompt=system_prompt,
                model_name=model_name
            )

            # 存储会话(用于兼容旧接口)
            if session_id not in self.conversations:
                self.conversations[session_id] = []
            self.conversations[session_id].append({
                "user": content,
                "agent": response
            })

            log.info(f"消息处理完成 - 会话: {session_id}")
            return response

        except Exception as e:
            log.error(f"Agent处理失败: {str(e)}")
            raise AgentException(f"Agent处理失败: {str(e)}")
    
    async def _check_and_call_tools(self, content: str) -> Optional[str]:
        """
        检查并调用工具
        
        Args:
            content: 用户消息内容
            
        Returns:
            工具调用结果,如果没有调用工具则返回None
        """
        try:
            # 检测企业信息查询意图
            enterprise_patterns = [
                r'查询企业(.+)',
                r'查找企业(.+)',
                r'搜索企业(.+)',
                r'企业查询(.+)',
                r'(.+)企业信息',
                r'(.+)工商信息',
                r'(.+)的工商信息',
                r'了解(.+)公司',
                r'(.+)公司信息',
                r'查一下(.+)公司',
            ]
            
            for pattern in enterprise_patterns:
                match = re.search(pattern, content)
                if match:
                    # 提取企业名称
                    try:
                        company_name = match.group(1).strip()
                    except IndexError:
                        company_name = match.group(0).strip()
                    
                    if not company_name:
                        company_name = content
                    
                    log.info(f"检测到企业信息查询意图: {company_name}")
                    
                    # 调用企业信息查询工具
                    result = await self.tools["enterprise"].query(company_name)
                    formatted_result = self.tools["enterprise"].format_result(result)
                    
                    return formatted_result
            
            # 检测搜索意图
            search_patterns = [
                r'搜索(.+)',
                r'查找(.+)',
                r'查询(.+)',
                r'帮我找(.+)',
                r'分析(.+)',
                r'我想了解(.+)',
                r'什么是(.+)',
                r'(.+)是什么',
                r'(.+)怎么样',
                r'(.+)天气',
                r'天气(.+)',
                r'(.+)的天气'
            ]
            
            for pattern in search_patterns:
                match = re.search(pattern, content)
                if match:
                    # 提取查询内容
                    try:
                        query = match.group(1).strip()
                    except IndexError:
                        # 没有捕获组,使用整个匹配内容
                        query = match.group(0).strip()
                    
                    # 如果查询为空,使用原始内容
                    if not query:
                        query = content
                    
                    log.info(f"检测到搜索意图: {query}")
                    
                    # 调用搜索工具
                    results = await self.tools["search"].search(query)
                    formatted_results = self.tools["search"].format_results(results)
                    
                    return formatted_results
            
            return None
            
        except Exception as e:
            log.error(f"工具调用失败: {str(e)}")
            return None

    async def get_session_history(self, session_id: str) -> List[Dict[str, Any]]:
        """
        获取会话历史

        Args:
            session_id: 会话ID

        Returns:
            会话历史记录
        """
        try:
            # 优先从LLM服务获取记忆
            memory_content = llm_service.get_memory_content(session_id)

            if memory_content:
                # 转换为兼容格式
                history = []
                for i in range(0, len(memory_content), 2):
                    if i + 1 < len(memory_content):
                        history.append({
                            "user": memory_content[i]["content"],
                            "agent": memory_content[i + 1]["content"]
                        })
                return history

            # 降级使用本地存储
            return self.conversations.get(session_id, [])

        except Exception as e:
            log.error(f"获取会话历史失败: {str(e)}")
            return self.conversations.get(session_id, [])

    async def clear_session(self, session_id: str) -> bool:
        """
        清除会话

        Args:
            session_id: 会话ID

        Returns:
            是否成功
        """
        try:
            # 清除LLM记忆
            llm_service.clear_memory(session_id)

            # 清除本地存储
            if session_id in self.conversations:
                del self.conversations[session_id]

            log.info(f"会话已清除: {session_id}")
            return True

        except Exception as e:
            log.error(f"清除会话失败: {str(e)}")
            return False

# 单例实例
agent_service = AgentService()
