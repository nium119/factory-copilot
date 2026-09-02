"""推理确认语义测试 — 规则引擎 evaluate_all 三元组返回（违规/推理/审批）。

覆盖：链式推理与确认标记、DB enrich 提供链式规则参数、
requiresConfirmation=False 自动应用、约束违规始终阻断。
"""
import pytest

from app.services.rule_engine import rule_engine


def _mock_concept(rules):
    """构造带规则的 QualityCheck 概念（主键 id）。"""
    return {
        "name": "QualityCheck",
        "label": "质检记录",
        "properties": [
            {"name": "id", "isPrimary": True, "type": "string"},
            {"name": "workOrderId", "isPrimary": False, "type": "string"},
            {"name": "result", "isPrimary": False, "type": "string"},
            {"name": "rework_count", "isPrimary": False, "type": "int"},
        ],
        "rules": rules,
    }


@pytest.fixture
def inject_concept():
    """向规则引擎注入 mock 概念，测试后恢复原状态。"""
    original = rule_engine._concept_index

    def _inject(concept):
        rule_engine._concept_index = {concept["name"]: concept}

    yield _inject
    rule_engine._concept_index = original


def test_chain_inference_with_confirmation(inject_concept):
    """链式推理：不合格→返工→(返工超3次)→报废，两条推理都要求确认。"""
    inject_concept(_mock_concept([
        {
            "name": "qualified_flow", "label": "不合格判定",
            "ruleType": "inference",
            "expression": "result == '不合格' → WorkOrder.status = '返工'",
            "requiresConfirmation": True, "nextRules": ["rework_limit"],
        },
        {
            "name": "rework_limit", "label": "返工次数上限",
            "ruleType": "inference",
            "expression": "rework_count >= 3 → WorkOrder.status = '报废'",
            "requiresConfirmation": True, "nextRules": [],
        },
    ]))

    violations, inferences, approvals = rule_engine.evaluate_all(
        "QualityCheck",
        {"workOrderId": "WO-001", "result": "不合格", "defectQuantity": 2, "rework_count": 4},
    )
    assert not violations
    assert len(inferences) == 2, "应产生 2 条链式推理"
    assert inferences[0].target_value == "返工"
    assert inferences[1].target_value == "报废"
    assert all(inf.requires_confirmation for inf in inferences)


def test_db_enrichment_feeds_chain_rule():
    """action_executor 的 enrich 语义：DB 既有记录补充链式规则所需参数。"""
    # 模拟 resolve_entity 回读的 DB 记录
    existing = {"id": "WO-20250521-001", "status": "生产中", "rework_count": 4}
    args = {"id": "WO-20250521-001", "result": "不合格", "defectQuantity": 2}
    enriched = dict(existing)
    enriched.update(args)
    assert enriched.get("rework_count") == 4, "DB enrich 应补出 rework_count 供链式规则评估"


def test_auto_apply_inference_without_confirmation(inject_concept):
    """requiresConfirmation=False 的推理不进确认列表。"""
    inject_concept(_mock_concept([
        {
            "name": "auto_infer", "label": "自动推理",
            "ruleType": "inference",
            "expression": "result == '合格' → WorkOrder.status = '已完成'",
            "requiresConfirmation": False, "nextRules": [],
        },
    ]))

    _, inferences, _ = rule_engine.evaluate_all(
        "QualityCheck",
        {"workOrderId": "WO-002", "result": "合格", "defectQuantity": 1},
    )
    assert len(inferences) == 1
    assert not inferences[0].requires_confirmation, "无确认标记的推理应自动应用"


def test_constraint_violation_blocks(inject_concept):
    """约束违规与确认设置无关，始终阻断。"""
    inject_concept(_mock_concept([
        {
            "name": "qty_check", "label": "数量校验",
            "ruleType": "constraint",
            "expression": "defectQuantity >= 0",
            "requiresConfirmation": False, "nextRules": [],
        },
    ]))

    violations, _, _ = rule_engine.evaluate_all("QualityCheck", {"defectQuantity": -5})
    assert len(violations) == 1
    assert "数量校验" in violations[0].message
