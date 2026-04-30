
from fastapi import APIRouter

from app.core.config import settings
from app.models.schemas import HealthCheckResponse

router = APIRouter(tags=["健康检查"])

@router.get("/health", response_model=HealthCheckResponse, summary="健康检查")
async def health_check():
    """
    检查服务运行状态，返回服务版本和时间戳。
    """
    return HealthCheckResponse(
        status="healthy",
        version=settings.APP_VERSION
    )

@router.get("/", summary="应用信息")
async def root():
    """
    返回应用基本信息：名称、版本、运行状态。
    """
    return {
        "name": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "status": "running"
    }
