"""A2A 服务端管理 API — API Key 管理 + 业务域列表

FC 作为 A2A 服务端（被外部系统调用）时的管理端点：
- /a2a/keys    — API Key CRUD（创建/吊销/启停），开放能力由各 Key 的 scopes 单独配置
- /a2a/domains — 列出当前 namespace 所有业务域（供前端配置 Key 能力时勾选）

Key 只存 SHA256 hash（不存明文），创建时返回完整 key 仅一次；列表只回脱敏前缀。
鉴权由 AuthMiddleware 全局兜底（/api 前缀 Bearer JWT），本文件不再逐路由校验。
"""
import hashlib
import json
import secrets
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.repositories.a2a_api_key_repo import A2aApiKeyRepository
from app.repositories.namespace_config_repo import NamespaceConfigRepository

keys_router = APIRouter(prefix="/a2a/keys", tags=["A2A 能力开放"])
domains_router = APIRouter(prefix="/a2a/domains", tags=["A2A 能力开放"])


# ─────────────────── 通用 helper ───────────────────

async def _get_active_namespace() -> str:
    """读取活跃 namespace（延迟 import，避免装配期循环依赖）"""
    from app.api.chains import _get_active_namespace as _resolve_ns
    return await _resolve_ns()


def _iso(dt) -> str:
    """datetime → ISO 字符串（可空）"""
    return dt.isoformat() if dt else ""


# ─────────────────── API Key 管理 ───────────────────

class ApiKeyCreate(BaseModel):
    name: str
    scopes: List[str] = []


class ApiKeyUpdate(BaseModel):
    scopes: Optional[List[str]] = None
    enabled: Optional[bool] = None


def _key_to_out(m) -> dict:
    """脱敏输出：只回前缀，不回 hash/明文"""
    scopes: List[str] = []
    try:
        scopes = json.loads(m.scopes) if m.scopes else []
    except (json.JSONDecodeError, TypeError):
        scopes = []
    return {
        "name": m.name,
        "key": m.key_plain or "",
        "key_prefix": m.key_prefix or "",
        "scopes": scopes,
        "enabled": m.enabled,
        "last_used_at": _iso(m.last_used_at),
        "created_at": _iso(m.created_at),
        "updated_at": _iso(m.updated_at),
    }


@keys_router.get("", summary="列出所有 API Key（脱敏）")
async def list_keys(db: AsyncSession = Depends(get_db)):
    repo = A2aApiKeyRepository(db)
    return [_key_to_out(k) for k in await repo.list_all()]


@keys_router.post("", summary="创建 API Key（返回完整 key 仅一次）")
async def create_key(body: ApiKeyCreate, db: AsyncSession = Depends(get_db)):
    repo = A2aApiKeyRepository(db)
    if await repo.get_by_name(body.name):
        raise HTTPException(409, f"Key 备注名已存在: {body.name}")
    key = "a2a_" + secrets.token_hex(24)
    key_hash = hashlib.sha256(key.encode("utf-8")).hexdigest()
    await repo.create(
        name=body.name,
        key_hash=key_hash,
        key_prefix=key[:16],
        key_plain=key,
        scopes=json.dumps(body.scopes or [], ensure_ascii=False),
        enabled=True,
    )
    return {"ok": True, "name": body.name, "key": key, "key_prefix": key[:16]}


@keys_router.put("/{name}", summary="更新 API Key（作用域 / 启停）")
async def update_key(name: str, body: ApiKeyUpdate, db: AsyncSession = Depends(get_db)):
    repo = A2aApiKeyRepository(db)
    if not await repo.get_by_name(name):
        raise HTTPException(404, f"Key 不存在: {name}")
    kwargs = {}
    if body.scopes is not None:
        kwargs["scopes"] = json.dumps(body.scopes, ensure_ascii=False)
    if body.enabled is not None:
        kwargs["enabled"] = body.enabled
    if kwargs:
        await repo.update(name, **kwargs)
    return {"ok": True, "name": name}


@keys_router.delete("/{name}", summary="吊销 API Key")
async def delete_key(name: str, db: AsyncSession = Depends(get_db)):
    repo = A2aApiKeyRepository(db)
    if not await repo.get_by_name(name):
        raise HTTPException(404, f"Key 不存在: {name}")
    await repo.delete(name)
    return {"ok": True, "name": name}


# ─────────────────── 业务域列表（供配置 Key 能力勾选）───────────────────

@domains_router.get("", summary="列出当前 namespace 所有业务域")
async def list_domains(db: AsyncSession = Depends(get_db)):
    """动态读本体域列表（NamespaceConfig domains）。

    开放能力不再由全局开关决定，而是落到每个 API Key 的 scopes（见 /a2a/keys）。
    本端点只提供「有哪些域」供前端勾选。
    """
    ns = await _get_active_namespace()
    domains = (await NamespaceConfigRepository(db).get(ns, "domains")) or {}
    # 概念中文标签映射（英文 name → 中文 label，无映射回退原名）
    label_map = {}
    try:
        from app.services.ontology_service import ontology_service
        label_map = ontology_service.get_concept_label_map()
    except Exception:
        pass
    result = []
    for key, d in domains.items():
        if not isinstance(d, dict):
            continue  # 过滤顶层 mode/_applied 标记
        result.append({
            "domain_key": key,
            "display_name": d.get("display_name", key),
            "description": d.get("description", ""),
            "concepts": [label_map.get(cn, cn) for cn in d.get("concepts", [])],
        })
    result.sort(key=lambda x: x["domain_key"])
    return {"namespace": ns, "domains": result}
