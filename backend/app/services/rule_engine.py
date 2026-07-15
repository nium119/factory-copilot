"""规则引擎 —— 基于插件的本体规则评估。

架构:
  RuleEvaluator (ABC) — 每种 ruleType 对应一个实现。
  RuleEngine — 从 OntologyService (Neo4j) 加载规则，分发给各评估器。

扩展点: 通过 register_evaluator() 注册新的评估器。
"""
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from operator import ge, gt, le, lt
from typing import Any, Dict, List, Optional

from app.core.logger import log


# ── 数据结构 ───────────────────────────────────────────────────────────────

@dataclass
class ApprovalRequired:
    """约束规则触发的条件审批"""
    rule_name: str
    rule_label: str
    description: str
    approval_roles: list = field(default_factory=list)
    condition_detail: str = ""


@dataclass
class RuleViolation:
    rule_name: str
    rule_label: str
    rule_type: str
    expression: str
    message: str
    failed_condition: str = ""


@dataclass
class InferredAction:
    rule_name: str
    rule_label: str
    description: str
    target_concept: str
    target_property: str = ""           # 旧格式
    target_value: str = ""              # 旧格式
    target_action: str = ""             # 新格式: 要调用的动作名称
    target_params: dict = field(default_factory=dict)  # 新格式: 动作参数值
    condition_met: str = ""
    nextRules: list = field(default_factory=list)
    requires_confirmation: bool = False


@dataclass
class TriggerAlert:
    rule_name: str
    rule_label: str
    description: str
    concept_name: str
    entity_id: str
    trigger_condition: str
    severity: str = "warning"
    agents: list = None


# ── 表达式解析辅助函数（各评估器共享）───────────────────────────────────────

def _safe_numeric(op):
    """包装数值比较操作：当任一操作数无法转为数值时返回 False，避免 TypeError。"""
    def _(a, b):
        na, nb = _try_number(a), _try_number(b)
        if na is None or nb is None:
            return False
        return op(na, nb)
    return _

COMPARE_OPS = {
    ">=": _safe_numeric(ge),
    "<=": _safe_numeric(le),
    "!=": lambda a, b: _try_cmp(a) != _try_cmp(b),
    "==": lambda a, b: _try_cmp(a) == _try_cmp(b),
    ">":  _safe_numeric(gt),
    "<":  _safe_numeric(lt),
}

OP_PATTERN = re.compile(r"\s*(>=|<=|!=|==|>|<)\s*")
FUNC_PATTERN = re.compile(r"\w+\s*\(")
FUNC_CALL_PATTERN = re.compile(r"(\w+)\((\w+)\)")
CONSEQUENCE_PATTERN = re.compile(r"\s*→\s*")


def _try_number(v: Any):
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _try_cmp(v: Any):
    num = _try_number(v)
    if isinstance(num, (int, float)):
        return num
    return str(v).lower() if v is not None else ""


def _parse_condition(cond_str: str) -> Optional[tuple]:
    """将 'prop op value' 解析为 (property, op, value)。"""
    m = OP_PATTERN.search(cond_str)
    if not m:
        return None
    prop = cond_str[: m.start()].strip()
    op = m.group(1)
    val = cond_str[m.end():].strip().strip("'\"")
    if not prop or val == "":
        return None
    return prop, op, val


def _parse_consequence_expression(expression: str) -> Optional[dict]:
    """将推理结论表达式解析为字典。

    新格式: condition → Concept.action(p1='v1', p2='v2')
    旧格式: condition → Concept.prop = value
    """
    parts = CONSEQUENCE_PATTERN.split(expression, maxsplit=1)
    if len(parts) != 2:
        return None
    condition_str = parts[0].strip()
    consequence_str = parts[1].strip()

    # 新格式: Concept.action(p1='v1', p2='v2')
    m = re.match(r"(\w+)\.(\w+)\(([^)]*)\)", consequence_str)
    if m:
        target_params = {}
        params_str = m.group(3)
        for pm in re.finditer(r"(\w+)\s*=\s*'([^']*)'", params_str):
            target_params[pm.group(1)] = pm.group(2)
        return {
            "condition_str": condition_str,
            "target_concept": m.group(1),
            "target_action": m.group(2),
            "target_params": target_params,
        }

    # 旧格式: Concept.prop = value 或 Concept.prop = 'value'
    m = re.match(r"(\w+)\.(\w+)\s*=\s*(.+)", consequence_str)
    if m:
        return {
            "condition_str": condition_str,
            "target_concept": m.group(1),
            "target_property": m.group(2),
            "target_value": m.group(3).strip().strip("'\""),
        }

    return None


def _evaluate_function(expr: str, entity: dict) -> Optional[float]:
    """对实体数据执行函数调用求值。返回数值结果或 None。

    支持的函数:
        days_since(propertyName) — 当前时间与 entity[propertyName] 之间的天数
    """
    m = FUNC_CALL_PATTERN.match(expr.strip())
    if not m:
        return None
    func_name = m.group(1)
    prop_name = m.group(2)
    if func_name == "days_since":
        date_val = entity.get(prop_name)
        if not date_val:
            return None
        try:
            from datetime import datetime
            s = str(date_val).replace("Z", "+00:00")
            dt = datetime.fromisoformat(s)
            return float((datetime.now() - dt.replace(tzinfo=None)).days)
        except (ValueError, TypeError):
            return None
    return None


# ── 评估器抽象基类 ──────────────────────────────────────────────────────────

class RuleEvaluator(ABC):
    """评估某一类本体规则的插件接口。"""

    @property
    @abstractmethod
    def rule_type(self) -> str:
        """此评估器处理的 ruleType 值（例如 'constraint'、'inference'）。"""

    @abstractmethod
    def evaluate(self, rule: dict, params: Dict[str, Any]) -> Optional[Any]:
        """对给定的 params 评估单条规则。

        返回值:
            ConstraintEvaluator → RuleViolation | None
            InferenceEvaluator  → InferredAction | None
            TriggerEvaluator    → TriggerAlert | None（未来扩展）
            None 表示规则通过或不适用。
        """


# ── 约束评估器 ──────────────────────────────────────────────────────────────

class ConstraintEvaluator(RuleEvaluator):
    """校验提交的 params 是否满足约束规则。"""

    @property
    def rule_type(self) -> str:
        return "constraint"

    def evaluate(self, rule: dict, params: Dict[str, Any], action_name: str = ""):
        """评估约束规则。返回 RuleViolation（违规）、ApprovalRequired（审批门禁）或 None（通过）。"""
        # applyToActions 过滤：非空时只对指定操作生效
        apply_to = rule.get("applyToActions") or []
        if apply_to and action_name and action_name not in apply_to:
            return None
        expression = (rule.get("expression") or "").strip()
        if not expression:
            return None
        if FUNC_PATTERN.search(expression):
            return None

        requires_approval = rule.get("requiresApproval") is True or rule.get("requiresApproval") == "True"
        approval_roles = rule.get("approvalRoles", []) or []

        parts = re.split(r"\s+AND\s+", expression, flags=re.IGNORECASE)
        for part in parts:
            part = part.strip()
            if not part:
                continue
            parsed = _parse_condition(part)
            if not parsed:
                continue
            prop, op, val = parsed
            left_val = params.get(prop)
            right_val = params.get(val, val)
            if left_val is None:
                continue
            compare = COMPARE_OPS.get(op)
            matched = compare and compare(left_val, right_val)

            if requires_approval:
                # 审批门禁：条件满足时触发审批
                if not matched:
                    return None  # 条件不满足，不需要审批
                # 所有条件都满足时触发审批
                continue

            # 普通约束：条件不满足时报违规
            if not matched:
                rule_label = rule.get('label', rule.get('name', ''))
                desc = rule.get('description', '')
                right_is_param = val in params
                detail = f"{prop}={left_val}，{val}={right_val}" if right_is_param else f"当前值 {left_val}"
                return RuleViolation(
                    rule_name=rule.get("name", ""),
                    rule_label=rule_label,
                    rule_type="constraint",
                    expression=expression,
                    message=f"「{rule_label}」：{desc}（{detail}）",
                    failed_condition=part,
                )
        if requires_approval:
            rule_label = rule.get('label', rule.get('name', ''))
            return ApprovalRequired(
                rule_name=rule.get("name", ""),
                rule_label=rule_label,
                description=rule.get("description", ""),
                approval_roles=approval_roles,
                condition_detail=expression,
            )
        return None


# ── 推理评估器 ──────────────────────────────────────────────────────────────

class InferenceEvaluator(RuleEvaluator):
    """当推理规则的条件满足时，推导出相应的结论。

    表达式格式:
        新:  condition → TargetConcept.action(p1='v1')
        旧:  condition → TargetConcept.property = value
    """

    @property
    def rule_type(self) -> str:
        return "inference"

    def evaluate(self, rule: dict, params: Dict[str, Any], action_name: str = "") -> Optional[InferredAction]:
        expression = (rule.get("expression") or "").strip()
        if not expression:
            return None

        parsed = _parse_consequence_expression(expression)
        if not parsed:
            log.debug(f"[RuleEngine] 无法解析的推理规则: {rule['name']}")
            return None

        condition_str = parsed["condition_str"]

        # 根据 params 评估条件
        cond_parsed = _parse_condition(condition_str)
        if not cond_parsed:
            return None

        prop, op, val = cond_parsed
        submitted_val = params.get(prop)
        if submitted_val is None:
            return None  # 条件无法评估

        expected = params.get(val, val)
        compare = COMPARE_OPS.get(op)
        if not compare or not compare(submitted_val, expected):
            return None  # 条件不满足

        target_action = parsed.get("target_action", "")
        target_params = parsed.get("target_params", {})

        return InferredAction(
            rule_name=rule.get("name", ""),
            rule_label=rule.get("label", rule.get("name", "")),
            description=rule.get("description", ""),
            target_concept=parsed["target_concept"],
            target_property=parsed.get("target_property", ""),
            target_value=parsed.get("target_value", ""),
            target_action=target_action,
            target_params=target_params,
            condition_met=condition_str,
            nextRules=rule.get("nextRules", []) or [],
            requires_confirmation=rule.get("requiresConfirmation", False),
        )


# ── 触发器评估器 ────────────────────────────────────────────────────────────

class TriggerEvaluator(RuleEvaluator):
    """基于 DataBackend 返回的实体数据评估触发器规则。

    扫描实体状态，当条件满足时触发告警。
    同时支持简单比较和函数调用（如 days_since()）。

    示例:
        stock < safetyStock                     → 库存不足告警
        days_since(lastMaintenance) > 180        → 维保逾期告警
    """

    @property
    def rule_type(self) -> str:
        return "trigger"

    def evaluate(self, rule: dict, entity: Dict[str, Any], action_name: str = "") -> Optional[TriggerAlert]:
        expression = (rule.get("expression") or "").strip()
        if not expression:
            return None

        parts = re.split(r"\s+AND\s+", expression, flags=re.IGNORECASE)
        entity_id = str(entity.get("id") or entity.get("name", "?"))

        for part in parts:
            part = part.strip()
            if not part:
                continue
            parsed = _parse_condition(part)
            if not parsed:
                log.debug(f"[RuleEngine] 无法解析的触发条件: {part}")
                return None

            prop, op, val = parsed

            # 首先尝试函数求值（例如 days_since(lastMaintenance)）
            left_val = _evaluate_function(prop, entity)
            if left_val is None:
                left_val = _try_number(entity.get(prop))

            if left_val is None:
                return None  # 所需数据不可用

            # 解析右侧：字面值或实体属性引用
            right_val = entity.get(val)
            if right_val is not None:
                right_val = _try_number(right_val)
            else:
                right_val = _try_number(val)

            compare = COMPARE_OPS.get(op)
            if compare and not compare(left_val, right_val):
                return None  # 某个条件失败，不触发

        # 所有条件均满足 —— 触发告警
        return TriggerAlert(
            rule_name=rule.get("name", ""),
            rule_label=rule.get("label", rule.get("name", "")),
            description=rule.get("description", ""),
            concept_name=entity.get("_concept", ""),
            entity_id=entity_id,
            trigger_condition=expression,
            severity=rule.get("severity", "warning"),
        )

        return None


# ── 规则引擎（编排器）────────────────────────────────────────────────────────

class RuleEngine:
    """加载本体规则并按 ruleType 分发给已注册的评估器。

    扩展点:
        rule_engine.register_evaluator(MyCustomEvaluator())
    """

    def __init__(self):
        self._concept_index: Dict[str, dict] = {}
        self._evaluators: Dict[str, RuleEvaluator] = {}

        # 注册内置评估器
        self.register_evaluator(ConstraintEvaluator())
        self.register_evaluator(InferenceEvaluator())
        self.register_evaluator(TriggerEvaluator())

    def register_evaluator(self, evaluator: RuleEvaluator) -> None:
        self._evaluators[evaluator.rule_type] = evaluator
        log.info(f"[RuleEngine] 已注册评估器: {evaluator.rule_type}")

    # ── 加载 ──────────────────────────────────────────────────────────

    def _ensure_loaded(self):
        """从 OntologyService (Neo4j) 延迟加载概念索引。"""
        if self._concept_index:
            return
        from app.services.ontology_service import ontology_service
        concepts = ontology_service.get_concepts()
        self._concept_index = {c["name"]: c for c in concepts}
        if self._concept_index:
            log.info(f"[RuleEngine] 已从本体加载 {len(self._concept_index)} 个概念")
            self._validate_bundle()
        else:
            log.warning("[RuleEngine] 本体服务未返回任何概念")

    def _validate_bundle(self) -> None:
        """加载时校验所有规则：检查表达式解析、引用是否存在、评估器是否已注册。"""
        errors = []
        all_concept_names = set(self._concept_index.keys())

        for concept_name, concept in self._concept_index.items():
            concept_props = {p["name"] for p in concept.get("properties", [])}
            for rule in concept.get("rules", []):
                rn = rule.get("name", "?")
                rt = rule.get("ruleType", "?")
                expr = (rule.get("expression") or "").strip()

                if not expr:
                    errors.append(f"{concept_name}.{rn}: 表达式为空")
                    continue

                if rt not in self._evaluators:
                    errors.append(f"{concept_name}.{rn}: 未知的 ruleType '{rt}'（无对应评估器）")
                    continue

                # 解析条件
                parts = re.split(r"\s+AND\s+", expr, flags=re.IGNORECASE)
                for part in parts:
                    # 剥离推理结论部分
                    if "→" in part:
                        cparts = part.split("→", 1)
                        part = cparts[0].strip()
                        cons = cparts[1].strip() if len(cparts) > 1 else ""
                        if cons:
                            # 同时接受旧格式和新格式（动作格式）
                            cm = re.match(r"(\w+)\.(\w+)\s*=\s*\S+", cons)
                            am = re.match(r"(\w+)\.(\w+)\(([^)]*)\)", cons)
                            if cm:
                                cons_concept = cm.group(1)
                                if cons_concept not in all_concept_names:
                                    errors.append(
                                        f"{concept_name}.{rn}: 结论引用了"
                                        f"未知概念 '{cons_concept}'"
                                    )
                            elif am:
                                cons_concept = am.group(1)
                                if cons_concept not in all_concept_names:
                                    errors.append(
                                        f"{concept_name}.{rn}: 结论引用了"
                                        f"未知概念 '{cons_concept}'"
                                    )
                            else:
                                errors.append(
                                    f"{concept_name}.{rn}: 无法解析的结论 '{cons}' "
                                    f"（期望格式: ConceptName.property = value 或 ConceptName.action(...)）"
                                )

                    p = _parse_condition(part.strip())
                    if not p:
                        errors.append(
                            f"{concept_name}.{rn}: 无法解析的条件 '{part.strip()}'"
                        )
                        continue
                    prop, op, val = p

                    # 检查函数调用: days_since(prop)
                    func_m = FUNC_CALL_PATTERN.match(prop)
                    actual_prop = func_m.group(2) if func_m else prop

                    # 检查属性是否存在
                    if actual_prop not in concept_props and prop not in concept_props:
                        errors.append(
                            f"{concept_name}.{rn}: 属性 '{actual_prop}' 在 "
                            f"{concept_name} 上未找到（可用属性: {concept_props}）"
                        )

        if errors:
            log.error(f"[RuleEngine] 规则包校验失败（{len(errors)} 个错误）:")
            for e in errors:
                log.error(f"  • {e}")
        else:
            log.info(f"[RuleEngine] 规则包校验通过（{len(self._concept_index)} 个概念）")

    def _get_rules(self, concept_name: str) -> list[dict]:
        self._ensure_loaded()
        concept = self._concept_index.get(concept_name, {})
        return concept.get("rules", [])

    # ── 公开 API ───────────────────────────────────────────────────────

    def validate(
        self, concept_name: str, params: Dict[str, Any],
    ) -> list[RuleViolation]:
        """校验某个概念的约束规则。（向后兼容）"""
        violations: list[RuleViolation] = []
        evaluator = self._evaluators.get("constraint")
        if not evaluator:
            return violations

        for rule in self._get_rules(concept_name):
            if rule.get("ruleType") != evaluator.rule_type:
                continue
            result = evaluator.evaluate(rule, params)
            if isinstance(result, RuleViolation):
                violations.append(result)

        return violations

    def infer(
        self, concept_name: str, params: Dict[str, Any],
    ) -> list[InferredAction]:
        """评估推理规则 —— 返回需要应用的结论。"""
        inferred: list[InferredAction] = []
        evaluator = self._evaluators.get("inference")
        if not evaluator:
            return inferred

        for rule in self._get_rules(concept_name):
            if rule.get("ruleType") != evaluator.rule_type:
                continue
            result = evaluator.evaluate(rule, params)
            if isinstance(result, InferredAction):
                inferred.append(result)

        return inferred

    def evaluate_all(
        self, concept_name: str, params: Dict[str, Any], action_name: str = "",
    ) -> tuple[list[RuleViolation], list[InferredAction], list[ApprovalRequired]]:
        """对某个概念运行所有适用的评估器。

        支持通过 nextRules 进行规则链式调用：当某条推理规则触发且声明了
        nextRules 时，这些规则会被依次评估，推理结果
        (target_property=target_value) 会合并到 params 中。
        """
        self._ensure_loaded()
        violations: list[RuleViolation] = []
        inferences: list[InferredAction] = []
        approvals: list[ApprovalRequired] = []
        visited: set[str] = set()
        active_params = dict(params)

        # ── 第一轮: 评估入口规则（未被其他规则的 nextRules 引用的规则）──
        # 收集所有被其他规则 nextRules 引用的规则名称
        all_rules = self._get_rules(concept_name)
        chained_names: set[str] = set()
        for rule in all_rules:
            for nr in (rule.get("nextRules") or []):
                chained_names.add(nr.strip())

        pending: list[InferredAction] = []

        for rule in all_rules:
            rule_name = rule.get("name", "")
            if rule_name in chained_names:
                continue  # 将通过链式调用评估
            evaluator = self._evaluators.get(rule.get("ruleType", ""))
            if not evaluator:
                continue
            result = evaluator.evaluate(rule, active_params, action_name)
            if isinstance(result, RuleViolation):
                violations.append(result)
            elif isinstance(result, ApprovalRequired):
                approvals.append(result)
            elif isinstance(result, InferredAction):
                inferences.append(result)
                pending.append(result)
                visited.add(rule_name)

        # ── 链式调用: 穷尽地跟踪 nextRules ──
        chain_depth = 0
        max_chain_depth = 20  # 安全上限
        while pending and chain_depth < max_chain_depth:
            chain_depth += 1
            current_batch = pending[:]
            pending = []

            for inf in current_batch:
                if not inf.nextRules:
                    continue
                for nr_name in inf.nextRules:
                    nr_name = nr_name.strip()
                    if not nr_name or nr_name in visited:
                        continue
                    visited.add(nr_name)

                    next_rule = self._find_rule_by_name(nr_name)
                    if not next_rule:
                        log.warning(f"[RuleEngine] 链式调用: 规则 '{nr_name}' 在所有概念中均未找到")
                        continue
                    if next_rule.get("ruleType") != "inference":
                        continue  # 仅推理规则参与链式调用

                    evaluator = self._evaluators.get("inference")
                    if not evaluator:
                        continue

                    # 将推理结果合并到 params 中，供下游规则使用
                    enriched = dict(active_params)
                    if inf.target_action:
                        enriched.update(inf.target_params)
                    else:
                        enriched[inf.target_property] = inf.target_value

                    log.info(f"[RuleEngine] 链式调用[{chain_depth}]: {nr_name} (来自 {inf.rule_name})")
                    result = evaluator.evaluate(next_rule, enriched)
                    if isinstance(result, InferredAction):
                        inferences.append(result)
                        pending.append(result)
                        # 将推理值传播到活动参数中
                        if result.target_action:
                            active_params.update(result.target_params)
                        else:
                            active_params[result.target_property] = result.target_value

        if chain_depth >= max_chain_depth:
            log.warning(f"[RuleEngine] 链式调用: 已达最大深度 {max_chain_depth}，停止")

        return violations, inferences, approvals

    def _find_rule_by_name(self, rule_name: str) -> Optional[dict]:
        """在所有概念中按名称查找规则。"""
        self._ensure_loaded()
        for concept in self._concept_index.values():
            for rule in concept.get("rules", []):
                if rule.get("name") == rule_name:
                    return rule
        return None

    def invalidate_cache(self):
        """清除缓存的概念索引，下次调用时将从 ontology_service 重新加载。"""
        self._concept_index = {}

    def evaluate_triggers(
        self, concept_name: str, entities: list[dict],
    ) -> list[TriggerAlert]:
        """扫描实体列表，匹配触发器规则 —— 返回触发的告警。

        在查询实体后调用，以展示主动性预警。
        """
        alerts: list[TriggerAlert] = []
        evaluator = self._evaluators.get("trigger")
        if not evaluator:
            return alerts

        for rule in self._get_rules(concept_name):
            if rule.get("ruleType") != "trigger":
                continue
            for entity in entities:
                result = evaluator.evaluate(rule, entity)
                if isinstance(result, TriggerAlert):
                    result.concept_name = concept_name  # 标记来源概念
                    alerts.append(result)

        return alerts


# 单例
rule_engine = RuleEngine()
