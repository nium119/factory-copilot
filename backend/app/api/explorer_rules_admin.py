"""异常检测规则管理 API — 管理阈值规则和 Graph Pattern 规则."""

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.repositories.explorer_rule_repo import ExplorerRuleRepository

router = APIRouter(prefix="/admin/explorer-rules", tags=["检测规则"])

_RULE_TYPES = ["threshold", "graph_pattern"]
_SEVERITIES = ["low", "medium", "high"]
_CHECK_OPS = [">", "<", ">=", "<=", "==", "!="]


# ---------- seed / reload (ORM + asyncio.run 桥接) ----------

def seed_from_defaults():
    """首次运行时从 Python 默认规则填充 DB"""
    async def _do():
        from app.services.explorer_service import DEFAULT_THRESHOLDS, GRAPH_PATTERNS
        async for session in get_db():
            repo = ExplorerRuleRepository(session)
            existing_names = {r.name for r in await repo.list_all()}
            inserted = 0
            for rule in DEFAULT_THRESHOLDS:
                if rule["name"] not in existing_names:
                    chk = rule.get("check", {})
                    await repo.create(
                        name=rule["name"],
                        rule_type="threshold",
                        concept=rule.get("concept", ""),
                        check_property=chk.get("property", ""),
                        check_op=chk.get("op", ">"),
                        check_value=str(chk.get("value", "")),
                        severity=rule.get("severity", "medium"),
                        title_template=rule.get("title_template", ""),
                        description_template=rule.get("description_template", ""),
                        suggestion=rule.get("suggestion", ""),
                    )
                    inserted += 1
            for rule in GRAPH_PATTERNS:
                if rule["name"] not in existing_names:
                    await repo.create(
                        name=rule["name"],
                        rule_type="graph_pattern",
                        graph_query=rule.get("query", ""),
                        graph_params=json.dumps(rule.get("params", {})),
                        severity=rule.get("severity", "medium"),
                        title_template=rule.get("title_template", ""),
                        description_template=rule.get("description_template", ""),
                        suggestion=rule.get("suggestion", ""),
                    )
                    inserted += 1
            if inserted:
                from app.core.logger import log
                log.info(f"[ExplorerRules] 种子数据已写入: {inserted} 条规则")

    try:
        asyncio.run(_do())
    except RuntimeError:
        pass


def load_rules_from_db():
    """从 DB 读取启用的规则，返回 (thresholds_list, graph_patterns_list)"""
    async def _do():
        async for session in get_db():
            repo = ExplorerRuleRepository(session)
            rules = await repo.list_all()
            thresholds = []
            patterns = []
            for r in rules:
                if not r.enabled:
                    continue
                if r.rule_type == "threshold":
                    thresholds.append({
                        "name": r.name, "concept": r.concept,
                        "check": {"property": r.check_property, "op": r.check_op, "value": _parse_check_value(r.check_value)},
                        "severity": r.severity, "title_template": r.title_template,
                        "description_template": r.description_template, "suggestion": r.suggestion,
                    })
                elif r.rule_type == "graph_pattern":
                    patterns.append({
                        "name": r.name, "description": r.title_template,
                        "query": r.graph_query, "params": json.loads(r.graph_params),
                        "severity": r.severity, "title_template": r.title_template,
                        "description_template": r.description_template, "suggestion": r.suggestion,
                    })
            return thresholds, patterns

    try:
        return asyncio.run(_do())
    except RuntimeError:
        return [], []


def _parse_check_value(val: str):
    try:
        return float(val)
    except ValueError:
        return val


def reload_explorer_rules():
    """重建 explorer_service 的检测器（从 DB）"""
    thresholds, patterns = load_rules_from_db()
    from app.services.explorer_service import explorer_service, ThresholdDetector, GraphPatternDetector
    # 替换检测器
    explorer_service._detectors = [d for d in explorer_service._detectors if d.name not in ("threshold", "graph_pattern")]
    if thresholds:
        explorer_service.register_detector(ThresholdDetector(thresholds))
    if patterns:
        explorer_service.register_detector(GraphPatternDetector(patterns))
    from app.core.logger import log
    log.info(f"[ExplorerRules] 已重载: {len(thresholds)} 阈值规则 + {len(patterns)} 图规则")


# ---------- Pydantic schemas ----------

class ExplorerRuleIn(BaseModel):
    name: str
    rule_type: str = "threshold"
    concept: str = ""
    check_property: str = ""
    check_op: str = ">"
    check_value: str = ""
    graph_query: str = ""
    graph_params: str = "{}"
    severity: str = "medium"
    title_template: str = ""
    description_template: str = ""
    suggestion: str = ""
    enabled: bool = True


class ExplorerRuleOut(BaseModel):
    name: str
    rule_type: str
    concept: str
    check_property: str
    check_op: str
    check_value: str
    graph_query: str
    graph_params: str
    severity: str
    title_template: str
    description_template: str
    suggestion: str
    enabled: bool
    created_at: str = ""
    updated_at: str = ""


def _model_to_out(m) -> ExplorerRuleOut:
    return ExplorerRuleOut(
        name=m.name,
        rule_type=m.rule_type,
        concept=m.concept,
        check_property=m.check_property,
        check_op=m.check_op,
        check_value=m.check_value,
        graph_query=m.graph_query,
        graph_params=m.graph_params,
        severity=m.severity,
        title_template=m.title_template,
        description_template=m.description_template,
        suggestion=m.suggestion,
        enabled=m.enabled,
        created_at=m.created_at.isoformat() if m.created_at else "",
        updated_at=m.updated_at.isoformat() if m.updated_at else "",
    )


# ---------- CRUD endpoints (ORM) ----------

@router.get("", summary="列出所有检测规则")
async def list_rules(db: AsyncSession = Depends(get_db)):
    repo = ExplorerRuleRepository(db)
    rules = await repo.list_all()
    return [_model_to_out(r) for r in rules]


@router.post("", summary="新增检测规则")
async def create_rule(rule: ExplorerRuleIn, db: AsyncSession = Depends(get_db)):
    if rule.rule_type not in _RULE_TYPES:
        raise HTTPException(400, f"rule_type 必须是 {_RULE_TYPES} 之一")
    if rule.severity not in _SEVERITIES:
        raise HTTPException(400, f"severity 必须是 {_SEVERITIES} 之一")
    repo = ExplorerRuleRepository(db)
    existing = await repo.get_by_name(rule.name)
    if existing:
        raise HTTPException(409, f"规则已存在: {rule.name}")
    await repo.create(
        name=rule.name,
        rule_type=rule.rule_type,
        concept=rule.concept,
        check_property=rule.check_property,
        check_op=rule.check_op,
        check_value=rule.check_value,
        graph_query=rule.graph_query,
        graph_params=rule.graph_params,
        severity=rule.severity,
        title_template=rule.title_template,
        description_template=rule.description_template,
        suggestion=rule.suggestion,
        enabled=rule.enabled,
    )
    return {"ok": True, "name": rule.name}


@router.put("/{name}", summary="更新检测规则")
async def update_rule(name: str, rule: ExplorerRuleIn, db: AsyncSession = Depends(get_db)):
    if rule.rule_type not in _RULE_TYPES:
        raise HTTPException(400, f"rule_type 必须是 {_RULE_TYPES} 之一")
    if rule.severity not in _SEVERITIES:
        raise HTTPException(400, f"severity 必须是 {_SEVERITIES} 之一")
    repo = ExplorerRuleRepository(db)
    existing = await repo.get_by_name(name)
    if not existing:
        raise HTTPException(404, f"规则不存在: {name}")
    await repo.update(
        name,
        rule_type=rule.rule_type,
        concept=rule.concept,
        check_property=rule.check_property,
        check_op=rule.check_op,
        check_value=rule.check_value,
        graph_query=rule.graph_query,
        graph_params=rule.graph_params,
        severity=rule.severity,
        title_template=rule.title_template,
        description_template=rule.description_template,
        suggestion=rule.suggestion,
        enabled=rule.enabled,
    )
    return {"ok": True, "name": name}


@router.delete("/{name}", summary="删除检测规则")
async def delete_rule(name: str, db: AsyncSession = Depends(get_db)):
    repo = ExplorerRuleRepository(db)
    existing = await repo.get_by_name(name)
    if not existing:
        raise HTTPException(404, f"规则不存在: {name}")
    await repo.delete(name)
    return {"ok": True, "name": name}


@router.post("/reload", summary="重新加载规则到检测器")
def reload_rules():
    reload_explorer_rules()
    return {"ok": True}
