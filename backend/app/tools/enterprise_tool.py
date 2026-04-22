"""企业信息查询工具 - 查询企业工商信息"""
from app.core.logger import log
from app.tools.search_tool import search_tool
from typing import Dict, Any, Optional, List
import asyncio
import re


class EnterpriseTool:
    """企业信息查询工具类 - 查询企业工商信息
    
    查询优先级:
    1. 企业API (暂未配置,跳过)
    2. 网页搜索工具
    3. 返回提示让LLM处理
    """
    
    def __init__(self):
        pass
    
    async def query(
        self, 
        company_name: str,
        query_type: str = "basic"
    ) -> Dict[str, Any]:
        """
        查询企业信息
        
        Args:
            company_name: 企业名称或统一社会信用代码
            query_type: 查询类型 (basic-基本信息, detail-详细信息, shareholder-股东信息)
            
        Returns:
            企业信息字典
        """
        try:
            log.info(f"查询企业信息: {company_name}, 类型: {query_type}")
            
            # 清理企业名称
            clean_name = self._clean_company_name(company_name)
            
            # 1. 尝试企业API (暂未配置,跳过)
            # TODO: 配置企查查/天眼查API后启用
            
            # 2. 使用网页搜索工具获取企业信息
            # 多种搜索策略
            search_queries = [
                f'{clean_name} 企查查',
                f'{clean_name} 天眼查',
                f'{clean_name} 工商信息',
            ]
            
            all_results = []
            for query in search_queries:
                results = await search_tool.search(query, max_results=3)
                all_results.extend(results)
                if len(all_results) >= 5:
                    break
            
            # 去重
            seen_links = set()
            unique_results = []
            for r in all_results:
                link = r.get("link", "")
                if link and link not in seen_links:
                    seen_links.add(link)
                    unique_results.append(r)
            
            search_results = unique_results[:5]
            
            # 检查搜索结果是否相关
            # 使用完整企业名(去掉后缀)作为关键词
            company_keywords = clean_name
            for suffix in ['有限公司', '股份有限公司', '有限责任公司', '集团']:
                company_keywords = company_keywords.replace(suffix, '')
            company_keywords = company_keywords.strip()
            
            relevant_results = []
            for r in search_results:
                title = r.get("title", "")
                snippet = r.get("snippet", "")
                # 必须包含完整企业核心名称(如"常州康岩信息技术")
                if company_keywords in title or company_keywords in snippet:
                    relevant_results.append(r)
            
            if relevant_results:
                info = self._parse_search_results(clean_name, relevant_results)
                return {
                    "success": True,
                    "data_type": "search",
                    "source": "web_search",
                    "info": info,
                    "raw_results": relevant_results
                }
            
            # 3. 搜索无相关结果,返回提示让LLM处理
            return {
                "success": False,
                "data_type": "llm",
                "source": "llm",
                "message": f"未找到 '{clean_name}' 的企业信息,请使用大模型知识回答",
                "company_name": clean_name,
                "query_type": query_type
            }
            
        except Exception as e:
            log.error(f"企业信息查询失败: {str(e)}")
            return {
                "success": False,
                "error": str(e),
                "company_name": company_name,
                "source": "error"
            }
    
    def _clean_company_name(self, name: str) -> str:
        """清理企业名称"""
        name = re.sub(r'\s+', '', name)
        prefixes = ['查询', '查找', '搜索', '了解', '分析', '帮我查', '查一下']
        for prefix in prefixes:
            if name.startswith(prefix):
                name = name[len(prefix):]
                break
        return name.strip()
    
    def _parse_search_results(self, company_name: str, results: List[Dict]) -> Dict[str, Any]:
        """解析搜索结果提取企业信息"""
        info = {
            "company_name": company_name,
            "search_snippets": []
        }
        
        for r in results:
            snippet = r.get("snippet", "")
            title = r.get("title", "")
            link = r.get("link", "")
            if snippet:
                info["search_snippets"].append({
                    "title": title,
                    "snippet": snippet,
                    "link": link
                })
        
        return info
    
    def format_result(self, result: Dict[str, Any]) -> str:
        """格式化企业信息为文本"""
        if result.get("source") == "error":
            return f"查询失败: {result.get('error', '未知错误')}"
        
        if result.get("source") == "llm":
            return result.get("message", "请使用大模型知识回答")
        
        if result.get("source") == "web_search":
            info = result.get("info", {})
            company_name = info.get("company_name", "未知企业")
            snippets = info.get("search_snippets", [])
            
            if not snippets:
                return f"未找到 '{company_name}' 的相关信息。"
            
            text = f"## {company_name} 企业信息\n\n"
            text += "以下是从网络搜索获取的相关信息:\n\n"
            
            for i, s in enumerate(snippets, 1):
                text += f"**{i}. {s.get('title', '')}**\n"
                text += f"{s.get('snippet', '')}\n\n"
            
            return text
        
        return "未知结果格式"
    
    async def search_similar(
        self, 
        keyword: str,
        max_results: int = 5
    ) -> List[Dict[str, Any]]:
        """
        模糊搜索企业
        
        Args:
            keyword: 搜索关键词
            max_results: 最大结果数
            
        Returns:
            企业列表
        """
        try:
            log.info(f"模糊搜索企业: {keyword}")
            
            # 使用网页搜索
            search_query = f"{keyword} 公司 企业"
            results = await search_tool.search(search_query, max_results=max_results)
            
            return results
            
        except Exception as e:
            log.error(f"模糊搜索企业失败: {str(e)}")
            return []


# 单例实例
enterprise_tool = EnterpriseTool()
