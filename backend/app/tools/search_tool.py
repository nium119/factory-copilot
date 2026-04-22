"""搜索工具 - 使用Bing网页搜索"""
import requests
from bs4 import BeautifulSoup
from app.core.logger import log
from typing import List, Dict, Any, Optional
import asyncio
from urllib.parse import quote_plus


class SearchTool:
    """搜索工具类 - 使用Bing网页搜索"""
    
    def __init__(self):
        self.max_results = 5
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        }
    
    async def search(
        self, 
        query: str, 
        max_results: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        执行搜索
        
        Args:
            query: 搜索查询
            max_results: 最大结果数
            
        Returns:
            搜索结果列表
        """
        try:
            if max_results:
                self.max_results = max_results
            
            log.info(f"执行搜索: {query}")
            
            loop = asyncio.get_event_loop()
            results = await loop.run_in_executor(
                None, 
                self._sync_search, 
                query
            )
            
            log.info(f"搜索完成,找到 {len(results)} 个结果")
            return results
            
        except Exception as e:
            log.error(f"搜索失败: {str(e)}")
            return []
    
    def _sync_search(self, query: str) -> List[Dict[str, Any]]:
        """同步搜索方法 - 使用Bing网页搜索"""
        try:
            url = f"https://www.bing.com/search?q={quote_plus(query)}"
            response = requests.get(url, headers=self.headers, timeout=10)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            results = []
            
            # Bing搜索结果在ol#b_results下的li.b_algo
            for item in soup.select('li.b_algo'):
                if len(results) >= self.max_results:
                    break
                
                title_elem = item.select_one('h2 a')
                snippet_elem = item.select_one('p') or item.select_one('.b_caption p')
                
                if title_elem:
                    title = title_elem.get_text(strip=True)
                    link = title_elem.get('href', '')
                    snippet = snippet_elem.get_text(strip=True) if snippet_elem else ''
                    
                    if title:
                        results.append({
                            "title": title,
                            "link": link,
                            "snippet": snippet
                        })
            
            return results
            
        except Exception as e:
            log.error(f"同步搜索失败: {str(e)}")
            return []
    
    def format_results(self, results: List[Dict[str, Any]]) -> str:
        """
        格式化搜索结果为文本
        """
        if not results:
            return "未找到相关结果。"
        
        formatted_text = "搜索结果:\n\n"
        for i, result in enumerate(results, 1):
            formatted_text += f"{i}. {result['title']}\n"
            formatted_text += f"   链接: {result['link']}\n"
            formatted_text += f"   摘要: {result['snippet']}\n\n"
        
        return formatted_text

# 单例实例
search_tool = SearchTool()
