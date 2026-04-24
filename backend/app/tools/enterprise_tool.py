"""企业信息查询工具 — 返回模拟数据，为后续接入真实 API 预留接口"""
from app.core.logger import log
from typing import Dict, Any, Optional


class EnterpriseTool:
    """企业信息查询工具类 — 当前返回模拟数据"""

    def __init__(self):
        pass

    async def query(
        self,
        company_name: str,
        query_type: str = "basic",
    ) -> Dict[str, Any]:
        """
        查询企业信息（模拟数据）

        Args:
            company_name: 企业名称
            query_type: 查询类型 (basic / detail / shareholder)

        Returns:
            企业信息字典
        """
        clean_name = self._clean_company_name(company_name)
        log.info(f"查询企业信息（模拟）: {clean_name}, 类型: {query_type}")

        return {
            "success": True,
            "data_type": "mock",
            "source": "mock",
            "info": {
                "company_name": clean_name,
                "status": "存续",
                "legal_person": "张某某",
                "registered_capital": "1000万人民币",
                "establishment_date": "2020-01-01",
                "business_scope": "软件开发、技术咨询",
                "address": "江苏省南京市某某区某某路1号",
            },
        }

    def _clean_company_name(self, name: str) -> str:
        """清理企业名称"""
        import re
        name = re.sub(r'\s+', '', name)
        prefixes = ['查询', '查找', '搜索', '了解', '分析', '帮我查', '查一下']
        for prefix in prefixes:
            if name.startswith(prefix):
                name = name[len(prefix):]
                break
        return name.strip()

    def format_result(self, result: Dict[str, Any]) -> str:
        """格式化企业信息为文本"""
        if not result.get("success"):
            return f"未找到企业信息"

        info = result.get("info", {})
        name = info.get("company_name", "未知")
        lines = [f"## {name} 企业信息\n"]
        for key, val in info.items():
            if key == "company_name":
                continue
            label_map = {
                "status": "经营状态", "legal_person": "法定代表人",
                "registered_capital": "注册资本", "establishment_date": "成立日期",
                "business_scope": "经营范围", "address": "注册地址",
            }
            label = label_map.get(key, key)
            lines.append(f"- **{label}**: {val}")
        return "\n".join(lines)

    async def search_similar(self, keyword: str, max_results: int = 5) -> list:
        """模糊搜索企业（模拟）"""
        log.info(f"模糊搜索企业（模拟）: {keyword}")
        return []


enterprise_tool = EnterpriseTool()
