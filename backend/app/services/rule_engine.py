"""Rule Engine — plugin-based evaluation of ontology rules.

Architecture:
  RuleEvaluator (ABC) — one implementation per ruleType.
  RuleEngine — loads rules from OntologyService (Neo4j), dispatches to evaluators.

Extension point: register new evaluators via register_evaluator().
"""
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.core.logger import log


# ── Data structures ───────────────────────────────────────────────────────

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
    target_property: str
    target_value: str
    condition_met: str = ""
    nextRules: list = field(default_factory=list)


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


# ── Expression parsing helpers (shared by evaluators) ──────────────────────

COMPARE_OPS = {
    ">=": lambda a, b: _try_number(a) >= _try_number(b),
    "<=": lambda a, b: _try_number(a) <= _try_number(b),
    "!=": lambda a, b: _try_cmp(a) != _try_cmp(b),
    "==": lambda a, b: _try_cmp(a) == _try_cmp(b),
    ">":  lambda a, b: _try_number(a) > _try_number(b),
    "<":  lambda a, b: _try_number(a) < _try_number(b),
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
        return v


def _try_cmp(v: Any):
    num = _try_number(v)
    if isinstance(num, (int, float)):
        return num
    return str(v).lower() if v is not None else ""


def _parse_condition(cond_str: str) -> Optional[tuple]:
    """Parse 'prop op value' into (property, op, value)."""
    m = OP_PATTERN.search(cond_str)
    if not m:
        return None
    prop = cond_str[: m.start()].strip()
    op = m.group(1)
    val = cond_str[m.end():].strip().strip("'\"")
    if not prop or val == "":
        return None
    return prop, op, val


def _parse_consequence_expression(expression: str) -> Optional[tuple]:
    """Parse 'condition → Concept.prop = value' into (condition_str, concept, prop, value)."""
    parts = CONSEQUENCE_PATTERN.split(expression, maxsplit=1)
    if len(parts) != 2:
        return None
    condition_str = parts[0].strip()
    consequence_str = parts[1].strip()
    # Parse consequence: "ConceptName.property = value" or "ConceptName.property = 'value'"
    m = re.match(r"(\w+)\.(\w+)\s*=\s*(.+)", consequence_str)
    if not m:
        return None
    return condition_str, m.group(1), m.group(2), m.group(3).strip().strip("'\"")


def _evaluate_function(expr: str, entity: dict) -> Optional[float]:
    """Evaluate a function call against entity data. Returns numeric result or None.

    Supported functions:
        days_since(propertyName) — days between now and entity[propertyName]
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


# ── Evaluator ABC ──────────────────────────────────────────────────────────

class RuleEvaluator(ABC):
    """Plugin interface for evaluating one type of ontology rule."""

    @property
    @abstractmethod
    def rule_type(self) -> str:
        """The ruleType value this evaluator handles (e.g. 'constraint', 'inference')."""

    @abstractmethod
    def evaluate(self, rule: dict, params: Dict[str, Any]) -> Optional[Any]:
        """Evaluate a single rule against params.

        Returns:
            ConstraintEvaluator → RuleViolation | None
            InferenceEvaluator  → InferredAction | None
            TriggerEvaluator    → TriggerAlert | None (future)
            None means the rule passed or doesn't apply.
        """


# ── Constraint Evaluator ───────────────────────────────────────────────────

class ConstraintEvaluator(RuleEvaluator):
    """Validates that submitted params satisfy constraint rules."""

    @property
    def rule_type(self) -> str:
        return "constraint"

    def evaluate(self, rule: dict, params: Dict[str, Any]) -> Optional[RuleViolation]:
        expression = (rule.get("expression") or "").strip()
        if not expression:
            return None
        if FUNC_PATTERN.search(expression):
            return None  # functions not evaluable yet

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
                continue  # param not supplied, assume pass
            compare = COMPARE_OPS.get(op)
            if compare and not compare(left_val, right_val):
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
        return None


# ── Inference Evaluator ────────────────────────────────────────────────────

class InferenceEvaluator(RuleEvaluator):
    """Derives consequences when inference rule conditions are met.

    Expression format:  condition → TargetConcept.property = value
    Example:  result == '不合格' → WorkOrder.status = '返工'
    """

    @property
    def rule_type(self) -> str:
        return "inference"

    def evaluate(self, rule: dict, params: Dict[str, Any]) -> Optional[InferredAction]:
        expression = (rule.get("expression") or "").strip()
        if not expression:
            return None

        parsed = _parse_consequence_expression(expression)
        if not parsed:
            log.debug(f"[RuleEngine] unparseable inference: {rule['name']}")
            return None

        condition_str, target_concept, target_prop, target_value = parsed

        # Evaluate the condition against params
        cond_parsed = _parse_condition(condition_str)
        if not cond_parsed:
            return None

        prop, op, val = cond_parsed
        submitted_val = params.get(prop)
        if submitted_val is None:
            return None  # condition can't be evaluated

        expected = params.get(val, val)
        compare = COMPARE_OPS.get(op)
        if not compare or not compare(submitted_val, expected):
            return None  # condition not met

        return InferredAction(
            rule_name=rule.get("name", ""),
            rule_label=rule.get("label", rule.get("name", "")),
            description=rule.get("description", ""),
            target_concept=target_concept,
            target_property=target_prop,
            target_value=target_value,
            condition_met=condition_str,
            nextRules=rule.get("nextRules", []) or [],
        )


# ── Trigger Evaluator ──────────────────────────────────────────────────────

class TriggerEvaluator(RuleEvaluator):
    """Evaluates trigger rules against entity data from DataBackend.

    Scans entity state and fires alerts when conditions are met.
    Supports both simple comparisons and function calls like days_since().

    Examples:
        stock < safetyStock                     → low stock alert
        days_since(lastMaintenance) > 180        → overdue maintenance alert
    """

    @property
    def rule_type(self) -> str:
        return "trigger"

    def evaluate(self, rule: dict, entity: Dict[str, Any]) -> Optional[TriggerAlert]:
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
                log.debug(f"[RuleEngine] unparseable trigger condition: {part}")
                return None

            prop, op, val = parsed

            # Try function evaluation first (e.g. days_since(lastMaintenance))
            left_val = _evaluate_function(prop, entity)
            if left_val is None:
                left_val = _try_number(entity.get(prop))

            if left_val is None:
                return None  # required data not available

            # Resolve right side: literal value or entity property reference
            right_val = entity.get(val)
            if right_val is not None:
                right_val = _try_number(right_val)
            else:
                right_val = _try_number(val)

            compare = COMPARE_OPS.get(op)
            if compare and not compare(left_val, right_val):
                return None  # one condition failed, don't fire

        # All conditions passed — fire alert
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


# ── Rule Engine (orchestrator) ─────────────────────────────────────────────

class RuleEngine:
    """Loads ontology rules and dispatches to registered evaluators by ruleType.

    Extension point:
        rule_engine.register_evaluator(MyCustomEvaluator())
    """

    def __init__(self):
        self._concept_index: Dict[str, dict] = {}
        self._evaluators: Dict[str, RuleEvaluator] = {}

        # Register built-in evaluators
        self.register_evaluator(ConstraintEvaluator())
        self.register_evaluator(InferenceEvaluator())
        self.register_evaluator(TriggerEvaluator())

    def register_evaluator(self, evaluator: RuleEvaluator) -> None:
        self._evaluators[evaluator.rule_type] = evaluator
        log.info(f"[RuleEngine] registered evaluator: {evaluator.rule_type}")

    # ── loading ────────────────────────────────────────────────────

    def _ensure_loaded(self):
        """Lazy-load concept index from OntologyService (Neo4j)."""
        if self._concept_index:
            return
        from app.services.ontology_service import ontology_service
        concepts = ontology_service.get_concepts()
        self._concept_index = {c["name"]: c for c in concepts}
        if self._concept_index:
            log.info(f"[RuleEngine] loaded {len(self._concept_index)} concepts from ontology")
            self._validate_bundle()
        else:
            log.warning("[RuleEngine] no concepts available from ontology service")

    def _validate_bundle(self) -> None:
        """Validate all rules on load: check expressions parse, references exist, evaluator registered."""
        errors = []
        all_concept_names = set(self._concept_index.keys())

        for concept_name, concept in self._concept_index.items():
            concept_props = {p["name"] for p in concept.get("properties", [])}
            for rule in concept.get("rules", []):
                rn = rule.get("name", "?")
                rt = rule.get("ruleType", "?")
                expr = (rule.get("expression") or "").strip()

                if not expr:
                    errors.append(f"{concept_name}.{rn}: expression is empty")
                    continue

                if rt not in self._evaluators:
                    errors.append(f"{concept_name}.{rn}: unknown ruleType '{rt}' (no evaluator)")
                    continue

                # Parse conditions
                parts = re.split(r"\s+AND\s+", expr, flags=re.IGNORECASE)
                for part in parts:
                    # Strip inference consequence
                    if "→" in part:
                        cparts = part.split("→", 1)
                        part = cparts[0].strip()
                        cons = cparts[1].strip() if len(cparts) > 1 else ""
                        if cons:
                            cm = re.match(r"(\w+)\.(\w+)\s*=\s*\S+", cons)
                            if not cm:
                                errors.append(
                                    f"{concept_name}.{rn}: unparseable consequence '{cons}' "
                                    f"(expected ConceptName.property = value)"
                                )
                            else:
                                cons_concept = cm.group(1)
                                if cons_concept not in all_concept_names:
                                    errors.append(
                                        f"{concept_name}.{rn}: consequence references "
                                        f"unknown concept '{cons_concept}'"
                                    )

                    p = _parse_condition(part.strip())
                    if not p:
                        errors.append(
                            f"{concept_name}.{rn}: unparseable condition '{part.strip()}'"
                        )
                        continue
                    prop, op, val = p

                    # Check function call: days_since(prop)
                    func_m = FUNC_CALL_PATTERN.match(prop)
                    actual_prop = func_m.group(2) if func_m else prop

                    # Check property exists
                    if actual_prop not in concept_props and prop not in concept_props:
                        errors.append(
                            f"{concept_name}.{rn}: property '{actual_prop}' not found "
                            f"on {concept_name} (available: {concept_props})"
                        )

        if errors:
            log.error(f"[RuleEngine] bundle validation FAILED ({len(errors)} errors):")
            for e in errors:
                log.error(f"  • {e}")
        else:
            log.info(f"[RuleEngine] bundle validation PASSED ({len(self._concept_index)} concepts)")

    def _get_rules(self, concept_name: str) -> list[dict]:
        self._ensure_loaded()
        concept = self._concept_index.get(concept_name, {})
        return concept.get("rules", [])

    # ── Public API ─────────────────────────────────────────────────

    def validate(
        self, concept_name: str, params: Dict[str, Any],
    ) -> list[RuleViolation]:
        """Validate constraint rules for a concept. (backward compatible)"""
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
        """Evaluate inference rules — returns consequences to apply."""
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
        self, concept_name: str, params: Dict[str, Any],
    ) -> tuple[list[RuleViolation], list[InferredAction]]:
        """Run all applicable evaluators against a concept's rules.

        Supports rule chaining via nextRules: when an inference fires and
        declares nextRules, those rules are evaluated in turn, with the
        inferred (target_property=target_value) merged into params.
        """
        self._ensure_loaded()
        violations: list[RuleViolation] = []
        inferences: list[InferredAction] = []
        visited: set[str] = set()
        active_params = dict(params)

        # ── First pass: evaluate entry-point rules (not referenced as nextRules) ──
        # Collect all rule names that are referenced by any rule's nextRules
        all_rules = self._get_rules(concept_name)
        chained_names: set[str] = set()
        for rule in all_rules:
            for nr in (rule.get("nextRules") or []):
                chained_names.add(nr.strip())

        pending: list[InferredAction] = []

        for rule in all_rules:
            rule_name = rule.get("name", "")
            if rule_name in chained_names:
                continue  # will be evaluated via chain
            evaluator = self._evaluators.get(rule.get("ruleType", ""))
            if not evaluator:
                continue
            result = evaluator.evaluate(rule, active_params)
            if isinstance(result, RuleViolation):
                violations.append(result)
            elif isinstance(result, InferredAction):
                inferences.append(result)
                pending.append(result)
                visited.add(rule_name)

        # ── Chain: follow nextRules exhaustively ──
        chain_depth = 0
        max_chain_depth = 20  # safety limit
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
                        log.warning(f"[RuleEngine] chain: rule '{nr_name}' not found in any concept")
                        continue
                    if next_rule.get("ruleType") != "inference":
                        continue  # only inference rules participate in chaining

                    evaluator = self._evaluators.get("inference")
                    if not evaluator:
                        continue

                    # Merge inferred result into params for downstream rules
                    enriched = dict(active_params)
                    enriched[inf.target_property] = inf.target_value

                    log.info(f"[RuleEngine] chain[{chain_depth}]: {nr_name} (from {inf.rule_name})")
                    result = evaluator.evaluate(next_rule, enriched)
                    if isinstance(result, InferredAction):
                        inferences.append(result)
                        pending.append(result)
                        # Propagate inferred values into active params
                        active_params[result.target_property] = result.target_value

        if chain_depth >= max_chain_depth:
            log.warning(f"[RuleEngine] chain: hit max depth {max_chain_depth}, stopping")

        return violations, inferences

    def _find_rule_by_name(self, rule_name: str) -> Optional[dict]:
        """Find a rule by name across all concepts."""
        self._ensure_loaded()
        for concept in self._concept_index.values():
            for rule in concept.get("rules", []):
                if rule.get("name") == rule_name:
                    return rule
        return None

    def evaluate_triggers(
        self, concept_name: str, entities: list[dict],
    ) -> list[TriggerAlert]:
        """Scan entities against trigger rules — returns triggered alerts.

        Call after querying entities to surface proactive warnings.
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
                    result.concept_name = concept_name  # tag with source concept
                    alerts.append(result)

        return alerts


# Singleton
rule_engine = RuleEngine()
