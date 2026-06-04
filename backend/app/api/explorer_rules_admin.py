"""异常检测规则管理 API — 管理阈值规则和 Graph Pattern 规则."""

import json
import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/admin/explorer-rules", tags=["检测规则"])

_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "agent.db")
_RULE_TYPES = ["threshold", "graph_pattern"]
_SEVERITIES = ["low", "medium", "high"]
_CHECK_OPS = [">", "<", ">=", "<=", "==", "!="]


def _get_db():
    import sqlite3
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_table():
    conn = _get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS explorer_rules (
            name TEXT PRIMARY KEY,
            rule_type TEXT NOT NULL DEFAULT 'threshold',
            concept TEXT NOT NULL DEFAULT '',
            check_property TEXT NOT NULL DEFAULT '',
            check_op TEXT NOT NULL DEFAULT '>',
            check_value TEXT NOT NULL DEFAULT '',
            graph_query TEXT NOT NULL DEFAULT '',
            graph_params TEXT NOT NULL DEFAULT '{}',
            severity TEXT NOT NULL DEFAULT 'medium',
            title_template TEXT NOT NULL DEFAULT '',
            description_template TEXT NOT NULL DEFAULT '',
            suggestion TEXT NOT NULL DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.commit()
    conn.close()


def seed_from_defaults():
    """首次运行时从 Python 默认规则填充 DB"""
    from app.services.explorer_service import DEFAULT_THRESHOLDS, GRAPH_PATTERNS
    _ensure_table()
    conn = _get_db()
    inserted = 0
    for rule in DEFAULT_THRESHOLDS:
        existing = conn.execute("SELECT 1 FROM explorer_rules WHERE name=?", (rule["name"],)).fetchone()
        if not existing:
            chk = rule.get("check", {})
            conn.execute(
                """INSERT INTO explorer_rules (name, rule_type, concept, check_property, check_op, check_value, severity, title_template, description_template, suggestion)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (rule["name"], "threshold", rule.get("concept", ""), chk.get("property", ""),
                 chk.get("op", ">"), str(chk.get("value", "")), rule.get("severity", "medium"),
                 rule.get("title_template", ""), rule.get("description_template", ""), rule.get("suggestion", "")),
            )
            inserted += 1
    for rule in GRAPH_PATTERNS:
        existing = conn.execute("SELECT 1 FROM explorer_rules WHERE name=?", (rule["name"],)).fetchone()
        if not existing:
            conn.execute(
                """INSERT INTO explorer_rules (name, rule_type, graph_query, graph_params, severity, title_template, description_template, suggestion)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (rule["name"], "graph_pattern", rule.get("query", ""),
                 json.dumps(rule.get("params", {})), rule.get("severity", "medium"),
                 rule.get("title_template", ""), rule.get("description_template", ""),
                 rule.get("suggestion", "")),
            )
            inserted += 1
    conn.commit()
    conn.close()
    if inserted:
        from app.core.logger import log
        log.info(f"[ExplorerRules] 种子数据已写入: {inserted} 条规则")


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


@router.get("", summary="列出所有检测规则")
def list_rules():
    _ensure_table()
    conn = _get_db()
    try:
        rows = conn.execute("SELECT * FROM explorer_rules ORDER BY severity DESC, name").fetchall()
        result = []
        for r in rows:
            result.append(ExplorerRuleOut(
                name=r["name"], rule_type=r["rule_type"], concept=r["concept"],
                check_property=r["check_property"], check_op=r["check_op"], check_value=r["check_value"],
                graph_query=r["graph_query"], graph_params=r["graph_params"],
                severity=r["severity"], title_template=r["title_template"],
                description_template=r["description_template"], suggestion=r["suggestion"],
                enabled=bool(r["enabled"]), created_at=r["created_at"], updated_at=r["updated_at"],
            ))
        return result
    finally:
        conn.close()


@router.post("", summary="新增检测规则")
def create_rule(rule: ExplorerRuleIn):
    if rule.rule_type not in _RULE_TYPES:
        raise HTTPException(400, f"rule_type 必须是 {_RULE_TYPES} 之一")
    if rule.severity not in _SEVERITIES:
        raise HTTPException(400, f"severity 必须是 {_SEVERITIES} 之一")
    _ensure_table()
    conn = _get_db()
    try:
        existing = conn.execute("SELECT 1 FROM explorer_rules WHERE name=?", (rule.name,)).fetchone()
        if existing:
            raise HTTPException(409, f"规则已存在: {rule.name}")
        conn.execute(
            """INSERT INTO explorer_rules (name, rule_type, concept, check_property, check_op, check_value,
               graph_query, graph_params, severity, title_template, description_template, suggestion, enabled)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (rule.name, rule.rule_type, rule.concept, rule.check_property, rule.check_op,
             rule.check_value, rule.graph_query, rule.graph_params, rule.severity,
             rule.title_template, rule.description_template, rule.suggestion, int(rule.enabled)),
        )
        conn.commit()
        return {"ok": True, "name": rule.name}
    finally:
        conn.close()


@router.put("/{name}", summary="更新检测规则")
def update_rule(name: str, rule: ExplorerRuleIn):
    if rule.rule_type not in _RULE_TYPES:
        raise HTTPException(400, f"rule_type 必须是 {_RULE_TYPES} 之一")
    if rule.severity not in _SEVERITIES:
        raise HTTPException(400, f"severity 必须是 {_SEVERITIES} 之一")
    _ensure_table()
    conn = _get_db()
    try:
        existing = conn.execute("SELECT 1 FROM explorer_rules WHERE name=?", (name,)).fetchone()
        if not existing:
            raise HTTPException(404, f"规则不存在: {name}")
        conn.execute(
            """UPDATE explorer_rules SET rule_type=?, concept=?, check_property=?, check_op=?,
               check_value=?, graph_query=?, graph_params=?, severity=?, title_template=?,
               description_template=?, suggestion=?, enabled=?, updated_at=datetime('now','localtime')
               WHERE name=?""",
            (rule.rule_type, rule.concept, rule.check_property, rule.check_op, rule.check_value,
             rule.graph_query, rule.graph_params, rule.severity, rule.title_template,
             rule.description_template, rule.suggestion, int(rule.enabled), name),
        )
        conn.commit()
        return {"ok": True, "name": name}
    finally:
        conn.close()


@router.delete("/{name}", summary="删除检测规则")
def delete_rule(name: str):
    _ensure_table()
    conn = _get_db()
    try:
        existing = conn.execute("SELECT 1 FROM explorer_rules WHERE name=?", (name,)).fetchone()
        if not existing:
            raise HTTPException(404, f"规则不存在: {name}")
        conn.execute("DELETE FROM explorer_rules WHERE name=?", (name,))
        conn.commit()
        return {"ok": True, "name": name}
    finally:
        conn.close()


@router.post("/reload", summary="重新加载规则到检测器")
def reload_rules():
    reload_explorer_rules()
    return {"ok": True}


def load_rules_from_db():
    """从 DB 读取启用的规则，返回 (thresholds_list, graph_patterns_list)"""
    _ensure_table()
    conn = _get_db()
    rows = conn.execute("SELECT * FROM explorer_rules WHERE enabled=1").fetchall()
    conn.close()
    thresholds = []
    patterns = []
    for r in rows:
        if r["rule_type"] == "threshold":
            thresholds.append({
                "name": r["name"], "concept": r["concept"],
                "check": {"property": r["check_property"], "op": r["check_op"], "value": _parse_check_value(r["check_value"])},
                "severity": r["severity"], "title_template": r["title_template"],
                "description_template": r["description_template"], "suggestion": r["suggestion"],
            })
        elif r["rule_type"] == "graph_pattern":
            patterns.append({
                "name": r["name"], "description": r["title_template"],
                "query": r["graph_query"], "params": json.loads(r["graph_params"]),
                "severity": r["severity"], "title_template": r["title_template"],
                "description_template": r["description_template"], "suggestion": r["suggestion"],
            })
    return thresholds, patterns


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
