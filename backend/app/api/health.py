"""Health check API — app status, Neo4j, DataBackend health."""

from fastapi import APIRouter

from app.core.config import settings
from app.models.schemas import HealthCheckResponse

router = APIRouter(tags=["健康检查"])


@router.get("/health", response_model=HealthCheckResponse, summary="健康检查")
async def health_check():
    """检查服务运行状态，返回版本 + Neo4j + DataBackend 健康信息。"""
    neo4j_ok = False
    data_backend_status = {}
    try:
        from app.services.neo4j_service import neo4j_service
        neo4j_ok = neo4j_service.connected
    except Exception:
        pass

    try:
        from app.services.data_backend import data_backend
        data_backend_status = await data_backend.health()
    except Exception:
        pass

    return HealthCheckResponse(
        status="healthy",
        version=settings.APP_VERSION,
        neo4j="connected" if neo4j_ok else "disconnected",
        data_backend=data_backend_status,
    )


@router.get("/", summary="应用信息")
async def root():
    """返回应用基本信息：名称、版本、运行状态。"""
    return {
        "name": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "status": "running",
    }
