"""探索与发现 API"""
from fastapi import APIRouter

from app.services.explorer_service import analyze_production_data, format_explorer_report

router = APIRouter(prefix="/explorer", tags=["探索发现"])


@router.get("/analyze", summary="生产数据探索分析")
async def production_analysis(hours: int = 24):
    """
    分析最近 N 小时的生产数据，主动发现异常

    返回异常列表和探索报告
    """
    data = await analyze_production_data(hours)
    return {
        "success": True,
        "analysis": data,
        "report": format_explorer_report(data),
    }
