"""探测器服务 — 基于插件的异常检测，用于主动监控。

架构:
  AnomalyDetector (抽象基类) — 每种检测策略一个实现。
  ExplorerService — 编排探测器、合并结果。

扩展点: 通过 register_detector() 注册自定义探测器。

向后兼容:
  analyze_production_data() 和 format_explorer_report() 保留为模块级函数，
  委托给 ExplorerService 单例。
"""
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, List

from app.core.logger import log


# ── 检测器抽象基类 ─────────────────────────────────────────────

class AnomalyDetector(ABC):
    """异常检测策略的插件接口。"""

    @property
    @abstractmethod
    def name(self) -> str:
        """人类可读的检测器名称，用于日志。"""

    @abstractmethod
    async def analyze(self, hours: int) -> List[Dict[str, Any]]:
        """执行异常检测，返回异常字典列表。

        每条异常:
            {"source": str, "severity": "high"|"medium"|"low",
             "title": str, "description": str, "suggestion": str}
        """


# ── 阈值检测器 ─────────────────────────────────────────────────

# 默认阈值配置 — 可在生产环境中扩展或覆盖。
DEFAULT_THRESHOLDS: List[Dict[str, Any]] = [
    {
        "name": "defect_rate_high",
        "concept": "QualityCheck",
        "filters": {},
        "check": {
            "property": "defectRate",
            "op": ">",
            "value": 3.0,
        },
        "severity": "high",
        "title_template": "{concept_label} 缺陷率持续偏高",
        "description_template": (
            "最近 {hours} 小时检测到 {concept_label} 缺陷率超过 {threshold}%，"
            "当前值 {actual_value}%"
        ),
        "suggestion": "建议检查工艺参数和来料质量",
    },
    {
        "name": "oee_low",
        "concept": "Equipment",
        "filters": {},
        "check": {
            "property": "oee",
            "op": "<",
            "value": 80.0,
        },
        "severity": "medium",
        "title_template": "{entity_name} 设备 OEE 偏低",
        "description_template": (
            "{entity_name} OEE 仅 {actual_value}%，"
            "低于目标 {threshold}%"
        ),
        "suggestion": "建议安排设备维护检查",
    },
    {
        "name": "stock_low",
        "concept": "Material",
        "filters": {},
        "check": {
            "property": "stock",
            "op": "<",
            "value": "safetyStock",
        },
        "severity": "medium",
        "title_template": "{entity_name} 库存低于安全线",
        "description_template": (
            "{entity_name} 当前库存 {actual_value}，"
            "低于安全库存 {threshold}"
        ),
        "suggestion": "建议安排补货计划",
    },
]


class ThresholdDetector(AnomalyDetector):
    """通过检查实体属性与阈值的对比来检测异常。

    从 DataBackend 查询实体数据，然后评估阈值规则。
    """

    def __init__(self, thresholds: List[Dict[str, Any]] = None):
        self._thresholds = thresholds or DEFAULT_THRESHOLDS

    @property
    def name(self) -> str:
        return "threshold"

    async def analyze(self, hours: int) -> List[Dict[str, Any]]:
        from app.services.data_backend import data_backend

        results: List[Dict[str, Any]] = []

        for t in self._thresholds:
            try:
                records = await data_backend.query(
                    t["concept"], t.get("filters", {}), [],
                )
                if not records:
                    continue

                check = t["check"]
                prop = check["property"]
                op = check["op"]
                threshold_val = check["value"]

                for record in records:
                    actual = record.get(prop)
                    if actual is None:
                        continue

                    # 解析阈值（可能是另一个字段名）
                    if isinstance(threshold_val, str) and threshold_val in record:
                        resolved_threshold = record[threshold_val]
                    else:
                        resolved_threshold = threshold_val

                    try:
                        actual_f = float(actual)
                        threshold_f = float(resolved_threshold)
                    except (ValueError, TypeError):
                        continue

                    triggered = False
                    if op == ">" and actual_f > threshold_f:
                        triggered = True
                    elif op == "<" and actual_f < threshold_f:
                        triggered = True
                    elif op == ">=" and actual_f >= threshold_f:
                        triggered = True
                    elif op == "<=" and actual_f <= threshold_f:
                        triggered = True

                    if not triggered:
                        continue

                    # 解析实体显示名称
                    entity_name = record.get("name") or record.get("id", "未知")
                    concept_label = record.get("_label", t["concept"])

                    title = t["title_template"].format(
                        entity_name=entity_name,
                        concept_label=concept_label,
                    )
                    description = t["description_template"].format(
                        entity_name=entity_name,
                        concept_label=concept_label,
                        threshold=resolved_threshold,
                        actual_value=actual_f,
                        hours=hours,
                    )

                    results.append({
                        "source": t["name"],
                        "severity": t["severity"],
                        "title": title,
                        "description": description,
                        "suggestion": t["suggestion"],
                    })

            except Exception as e:
                log.debug(f"[{self.name}] 阈值检查 '{t['name']}' 失败: {e}")

        return results


# ── 图模式检测器 ──────────────────────────────────────────────

GRAPH_PATTERNS: List[Dict[str, Any]] = [
    {
        "name": "andon_frequency_spike",
        "description": "安灯呼叫频率异常",
        "query": """
            MATCH (a:AndonEvent)
            WHERE a.timestamp >= datetime() - duration({hours: $hours})
            WITH a.workstationId AS ws, count(a) AS cnt
            WHERE cnt > $expected_max
            RETURN ws AS entity, cnt AS actual, $expected_max AS threshold
        """,
        "params": {"expected_max": 3},
        "severity": "medium",
        "title_template": "{entity} 安灯呼叫频率偏高",
        "description_template": "最近 {hours} 小时安灯呼叫 {actual} 次，高于正常值 {threshold} 次",
        "suggestion": "建议安排设备巡检，提前排查隐患",
    },
]


class GraphPatternDetector(AnomalyDetector):
    """通过对 Neo4j 执行 Cypher 模式查询来检测异常。

    Neo4j 不可用时返回空结果。
    """

    def __init__(self, patterns: List[Dict[str, Any]] = None):
        self._patterns = patterns or GRAPH_PATTERNS

    @property
    def name(self) -> str:
        return "graph_pattern"

    async def analyze(self, hours: int) -> List[Dict[str, Any]]:
        try:
            from app.services.neo4j_service import neo4j_service
            if not neo4j_service.is_connected():
                return []
        except Exception:
            return []

        results: List[Dict[str, Any]] = []

        for pattern in self._patterns:
            try:
                params = dict(pattern.get("params", {}))
                params["hours"] = hours

                rows = await neo4j_service.execute_read(
                    pattern["query"], params,
                )

                for row in rows:
                    entity = row.get("entity", row.get("ws", "未知"))
                    actual = row.get("actual", "?")
                    threshold = row.get("threshold", "?")

                    results.append({
                        "source": pattern["name"],
                        "severity": pattern["severity"],
                        "title": pattern["title_template"].format(entity=entity),
                        "description": pattern["description_template"].format(
                            entity=entity, actual=actual,
                            threshold=threshold, hours=hours,
                        ),
                        "suggestion": pattern["suggestion"],
                    })

            except Exception as e:
                log.debug(f"[{self.name}] 模式 '{pattern['name']}' 失败: {e}")

        return results


# ── 规则触发扫描器 ────────────────────────────────────────────

class RuleTriggerScanner(AnomalyDetector):
    """扫描所有含触发规则的概念，评估实体状态，去重。

    使用 AlertRepository 避免对同一实体重复触发相同告警。
    """

    @property
    def name(self) -> str:
        return "rule_trigger"

    async def analyze(self, hours: int = 24) -> List[Dict[str, Any]]:
        from app.services.rule_engine import rule_engine
        from app.services.data_backend import data_backend
        from app.repositories.alert_repository import AlertRepository
        from app.core.startup import DB_PATH
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

        results: List[Dict[str, Any]] = []

        # 收集含触发规则的概念
        rule_engine._ensure_loaded()
        trigger_concepts = []
        for cname, concept in rule_engine._concept_index.items():
            for rule in concept.get("rules", []):
                if rule.get("ruleType") == "trigger":
                    trigger_concepts.append(cname)
                    break

        if not trigger_concepts:
            return results

        # 通过 AlertRepository（SQLite）去重
        engine = create_async_engine(f"sqlite+aiosqlite:///{DB_PATH}")
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        try:
            async with session_factory() as db:
                alert_repo = AlertRepository(db)

                for concept_name in trigger_concepts:
                    try:
                        entities = await data_backend.query(concept_name, {}, [])
                        if not entities:
                            continue

                        alerts = rule_engine.evaluate_triggers(concept_name, entities)
                        for alert in alerts:
                            # 去重: 相同规则+实体已活跃则跳过
                            if await alert_repo.exists(alert.rule_name, alert.entity_id):
                                continue

                            alert.agents = ["production_execution", "production_management", "quality_equipment", "analysis_monitor"]
                            await alert_repo.create({
                                "rule_name": alert.rule_name,
                                "rule_label": alert.rule_label,
                                "concept_name": concept_name,
                                "entity_id": alert.entity_id,
                                "severity": alert.severity,
                                "agents": alert.agents,
                                "trigger_condition": alert.trigger_condition,
                                "description": alert.description,
                            })

                            results.append({
                                "source": f"trigger:{alert.rule_name}",
                                "severity": alert.severity,
                                "title": alert.rule_label,
                                "description": alert.description,
                                "suggestion": f"检查 {concept_name} {alert.entity_id}：{alert.trigger_condition}",
                                "agents": alert.agents,
                            })
                    except Exception as e:
                        log.warning(f"[rule_trigger] 扫描失败 {concept_name}: {e}")

                # 升级过期告警
                escalated = await alert_repo.escalate_stale(hours=24)
                if escalated:
                    log.info(f"[rule_trigger] 已升级 {escalated} 条过期告警")
        finally:
            await engine.dispose()

        return results


# ── 探测器服务（编排器）───────────────────────────────────────

class ExplorerService:
    """编排异常检测器，生成监控报告。

    扩展点:
        explorer_service.register_detector(MyCustomDetector())
    """

    def __init__(self):
        self._detectors: List[AnomalyDetector] = []
        self.register_detector(ThresholdDetector())
        self.register_detector(GraphPatternDetector())
        self.register_detector(RuleTriggerScanner())

    def register_detector(self, detector: AnomalyDetector) -> None:
        self._detectors.append(detector)
        log.info(f"[ExplorerService] 已注册检测器: {detector.name}")

    async def analyze(self, hours: int = 24) -> Dict[str, Any]:
        """运行所有检测器并聚合结果。"""
        log.info(f"[ExplorerService] 分析生产数据，最近 {hours} 小时")

        all_anomalies: List[Dict[str, Any]] = []
        for detector in self._detectors:
            try:
                results = await detector.analyze(hours)
                if results:
                    log.info(f"[ExplorerService] {detector.name}: {len(results)} 项异常")
                    all_anomalies.extend(results)
            except Exception as e:
                log.warning(f"[ExplorerService] 检测器 '{detector.name}' 失败: {e}")

        return {
            "analyzed_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "analysis_period_hours": hours,
            "anomaly_count": len(all_anomalies),
            "anomalies": all_anomalies,
            "summary": _generate_summary(all_anomalies),
        }


# ── 公共辅助函数 ──────────────────────────────────────────────

def _generate_summary(anomalies: List[Dict[str, Any]]) -> str:
    if not anomalies:
        return "未发现异常。"

    high_count = sum(1 for a in anomalies if a["severity"] == "high")
    medium_count = sum(1 for a in anomalies if a["severity"] == "medium")
    low_count = sum(1 for a in anomalies if a["severity"] == "low")
    warning_count = sum(1 for a in anomalies if a["severity"] == "warning")

    parts = [f"共发现 {len(anomalies)} 项异常"]
    if high_count:
        parts.append(f"高优先级 {high_count} 项")
    if medium_count:
        parts.append(f"中优先级 {medium_count} 项")
    if low_count:
        parts.append(f"低优先级 {low_count} 项")
    if warning_count:
        parts.append(f"预警 {warning_count} 项")

    return "，".join(parts) + "。"


def format_explorer_report(data: Dict[str, Any]) -> str:
    """将探测器结果格式化为 Markdown 报告。（向后兼容）"""
    lines = ["## 生产数据探索报告\n"]
    lines.append(f"**分析时间**: {data['analyzed_at']}")
    lines.append(f"**分析范围**: 最近 {data['analysis_period_hours']} 小时\n")
    lines.append(f"**摘要**: {data['summary']}\n")

    if data["anomalies"]:
        for i, anomaly in enumerate(data["anomalies"], 1):
            sev = {"high": "🔴", "medium": "🟡", "low": "🟢", "warning": "🟠"}
            icon = sev.get(anomaly["severity"], "⚪")
            lines.append(f"### {i}. {icon} {anomaly['title']}")
            lines.append(f"**来源**: {anomaly['source']} | **优先级**: {anomaly['severity']}")
            lines.append(f"**描述**: {anomaly['description']}")
            lines.append(f"**建议**: {anomaly['suggestion']}")
            lines.append("")
    else:
        lines.append("未发现异常，生产运行正常。")

    return "\n".join(lines)


# ── 单例 & 模块级 API（向后兼容）────────────────────────────

explorer_service = ExplorerService()


async def analyze_production_data(hours: int = 24) -> Dict[str, Any]:
    """分析生产数据中的异常。（向后兼容）"""
    return await explorer_service.analyze(hours)
