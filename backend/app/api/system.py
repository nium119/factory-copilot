from fastapi import APIRouter

from app.core.resource_monitor import resource_monitor

router = APIRouter(tags=["系统状态"])


@router.get("/system/resources", summary="获取系统资源状态")
async def get_resource_status():
    """返回当前系统资源使用状况（并发/API频率/token预算/模型层级）"""
    return resource_monitor.snapshot()
