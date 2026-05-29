"""Ontology management API — status, reload, source configuration."""

from fastapi import APIRouter
from pydantic import BaseModel

from app.services.ontology_service import ontology_service

router = APIRouter(prefix="/ontology", tags=["本体管理"])


class OntologySourceConfig(BaseModel):
    localPath: str = ""
    remoteUrl: str = ""


@router.get("/status")
async def get_status():
    """Return current ontology loading status and metadata."""
    return ontology_service.status()


@router.post("/reload")
async def reload():
    """Reload ontology from the previously configured source."""
    ok = await ontology_service.reload()
    return {"success": ok, "status": ontology_service.status()}


@router.put("/source")
async def configure_source(config: OntologySourceConfig):
    """Configure and reload ontology source."""
    ok = await ontology_service.load(
        local_path=config.localPath,
        remote_url=config.remoteUrl,
    )
    return {"success": ok, "status": ontology_service.status()}
