"""动态 Skill 管理 API — 声明式工具，运行时配置，热更新"""
import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.repositories.skill_repo import SkillRepository
from app.services.skill_service import skill_service

router = APIRouter(prefix="/skills", tags=["动态Skill"])

ALLOWED_RISKS = ("READ", "WRITE_AUDIT")
ALLOWED_KINDS = ("cypher_template", "aggregate", "map_to_action")
ALLOWED_TYPES = ("concept_query", "aggregate", "transform")


class SkillIn(BaseModel):
    name: str
    display_name: str = ""
    description: str = ""
    type: str = "concept_query"
    concept: str = ""
    param_schema: list = Field(default_factory=list)
    implementation: dict = Field(default_factory=dict)
    risk: str = "READ"
    enabled: bool = True


class ExecIn(BaseModel):
    params: dict = Field(default_factory=dict)


def _validate(s: SkillIn):
    """创建/更新校验：写类必须映射 action（禁止裸写），只读模板必须只读"""
    if s.type not in ALLOWED_TYPES:
        raise HTTPException(400, f"type 必须为 {'/'.join(ALLOWED_TYPES)}")
    if s.risk not in ALLOWED_RISKS:
        raise HTTPException(400, f"risk 必须为 {'/'.join(ALLOWED_RISKS)}")
    kind = (s.implementation or {}).get("kind", "")
    if kind not in ALLOWED_KINDS:
        raise HTTPException(400, f"implementation.kind 必须为 {'/'.join(ALLOWED_KINDS)}")
    if kind == "map_to_action":
        # 写类必须映射到已建模 action，且仅 WRITE_AUDIT 允许（写操作走 action 统一治理）
        if s.risk != "WRITE_AUDIT":
            raise HTTPException(400, "map_to_action（写操作）必须声明 risk=WRITE_AUDIT")
        if not (s.implementation or {}).get("action_name"):
            raise HTTPException(400, "map_to_action 必须配置 action_name")
    else:
        # 只读类：不允许写声明，模板必须存在且只读
        if s.risk == "WRITE_AUDIT":
            raise HTTPException(400, "只读 skill（cypher/aggregate）不能声明为写操作")
        template = (s.implementation or {}).get("template", "")
        if not template:
            raise HTTPException(400, "只读 skill 必须配置 implementation.template")
        if kind in ("cypher_template", "aggregate"):
            from app.services.neo4j_service import Neo4jService
            ok, err = Neo4jService.validate_readonly(template)
            if not ok:
                raise HTTPException(400, f"模板校验失败: {err}")


@router.get("", summary="列出动态 skill")
async def list_skills():
    return {"items": skill_service.all()}


@router.post("", summary="创建 skill")
async def create_skill(s: SkillIn, db: AsyncSession = Depends(get_db)):
    _validate(s)
    repo = SkillRepository(db)
    if await repo.get_by_name(s.name):
        raise HTTPException(409, f"skill 已存在: {s.name}")
    await repo.create(
        name=s.name, display_name=s.display_name, description=s.description,
        type=s.type, concept=s.concept,
        param_schema=json.dumps(s.param_schema, ensure_ascii=False),
        implementation=json.dumps(s.implementation, ensure_ascii=False),
        risk=s.risk, enabled=s.enabled,
    )
    await skill_service.reload()
    return {"ok": True, "name": s.name}


@router.put("/{name}", summary="更新 skill")
async def update_skill(name: str, s: SkillIn, db: AsyncSession = Depends(get_db)):
    _validate(s)
    repo = SkillRepository(db)
    if not await repo.get_by_name(name):
        raise HTTPException(404, f"skill 不存在: {name}")
    await repo.update(
        name, display_name=s.display_name, description=s.description,
        type=s.type, concept=s.concept,
        param_schema=json.dumps(s.param_schema, ensure_ascii=False),
        implementation=json.dumps(s.implementation, ensure_ascii=False),
        risk=s.risk, enabled=s.enabled,
    )
    await skill_service.reload()
    return {"ok": True, "name": name}


@router.delete("/{name}", summary="删除 skill")
async def delete_skill(name: str, db: AsyncSession = Depends(get_db)):
    repo = SkillRepository(db)
    if not await repo.delete(name):
        raise HTTPException(404, f"skill 不存在: {name}")
    await skill_service.reload()
    return {"ok": True, "name": name}


@router.post("/{name}/execute", summary="测试执行 skill")
async def execute_skill(name: str, body: ExecIn):
    return await skill_service.execute(name, body.params)
