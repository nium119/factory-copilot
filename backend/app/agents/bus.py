"""Agent 间消息总线 — A2A 轻量调度"""
from typing import Any, Optional

from app.agents.settings import COLLAB_DOMAIN_QUERIES
from app.core.logger import log


class AgentBus:
    """Agent 间通信总线，避免跨 Agent 硬编码 import"""

    _instance: Optional["AgentBus"] = None

    @classmethod
    def get_instance(cls) -> "AgentBus":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def dispatch(
        self,
        agent_name: str,
        action: str,
        params: Optional[dict] = None,
    ) -> Any:
        """向指定 Agent 发送请求"""
        try:
            from app.agents import get_agent
            agent = get_agent(agent_name)
            query = self._build_query(action, params)
            result = await agent.call_tools(query)
            log.debug(f"AgentBus.dispatch({agent_name}, {action}) -> {'有数据' if result else '无数据'}")
            return result
        except Exception as e:
            log.warning(f"AgentBus.dispatch({agent_name}, {action}) 失败: {e}")
            return None

    def _build_query(self, action: str, params: Optional[dict] = None) -> str:
        """将动作和参数转换为自然语言查询"""
        query_map = {
            "query_inventory": "查询物料库存和齐套情况",
            "query_schedule": lambda: f"查询{params.get('line')}排产计划和产能情况" if params and params.get("line") else "查询当前排产计划和产能情况",
            "query_equipment": lambda: f"查询{params.get('line')}设备状态" if params and params.get("line") else "查询设备运行状态和故障信息",
            "query_quality": "质量概况和合格率",
            "query_process": "查询工艺路线和参数",
            "query_production_prep": "查询生产准备检查情况",
            "query_andon": "查询安灯异常和升级情况",
            "query_workstation": "查询工位终端报工情况",
        }

        handler = query_map.get(action)
        if callable(handler):
            return handler()
        if handler:
            return handler
        # 使用领域查询模板
        return COLLAB_DOMAIN_QUERIES.get(action, action)
