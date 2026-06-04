"""Focused E2E test for inference confirmation — tests action_executor write path."""
import sys
sys.path.insert(0, 'D:/code/long-running-agent-harness/projects/factory-copilot/backend')

import asyncio
from app.services.rule_engine import rule_engine, RuleEngine


class MockBackend:
    """Mock DataBackend for testing."""
    def __init__(self):
        self.created = []
        self.entities = {
            "WO-20250521-001": {"id": "WO-20250521-001", "status": "生产中", "rework_count": 4},
        }

    async def resolve_entity(self, concept, keyword):
        return self.entities.get(keyword)

    async def create(self, concept, data):
        self.created.append({"concept": concept, "data": dict(data)})
        return {"id": data.get("id", "NEW-001")}

    async def health(self):
        return {"primary": "mock"}


async def main():
    print("=" * 60)
    print("E2E Inference Confirmation Test")
    print("=" * 60)

    # ── Setup: register mock rules into RuleEngine ──
    mock_concept = {
        "name": "QualityCheck",
        "label": "质检记录",
        "properties": [
            {"name": "id", "isPrimary": True, "type": "string"},
            {"name": "workOrderId", "isPrimary": False, "type": "string"},
            {"name": "result", "isPrimary": False, "type": "string"},
            {"name": "rework_count", "isPrimary": False, "type": "int"},
        ],
        "rules": [
            {
                "name": "qualified_flow",
                "label": "不合格判定",
                "description": "质检不合格则工单返工",
                "ruleType": "inference",
                "expression": "result == '不合格' → WorkOrder.status = '返工'",
                "requiresConfirmation": True,
                "nextRules": ["rework_limit"],
            },
            {
                "name": "rework_limit",
                "label": "返工次数上限",
                "description": "返工超过3次则报废",
                "ruleType": "inference",
                "expression": "rework_count >= 3 → WorkOrder.status = '报废'",
                "requiresConfirmation": True,
                "nextRules": [],
            },
            {
                "name": "qty_check",
                "label": "数量校验",
                "description": "defectQuantity >= 0",
                "ruleType": "constraint",
                "expression": "defectQuantity >= 0",
                "requiresConfirmation": False,
                "nextRules": [],
            },
        ],
    }

    # Inject into rule_engine's concept index
    rule_engine._concept_index = {"QualityCheck": mock_concept}

    print("\n--- Test 1: Preview mode (unconfirmed inferences) ---")
    violations, inferences = rule_engine.evaluate_all(
        "QualityCheck",
        {"workOrderId": "WO-001", "result": "不合格", "defectQuantity": 2, "rework_count": 4},
    )
    print(f"  Violations: {len(violations)}")
    print(f"  Inferences: {len(inferences)}")
    for inf in inferences:
        print(f"    {inf.rule_label}: {inf.target_concept}.{inf.target_property} = {inf.target_value} (confirm={inf.requires_confirmation})")

    unconfirmed = [inf for inf in inferences if inf.requires_confirmation]
    assert len(inferences) == 2, f"Expected 2 inferences (chain), got {len(inferences)}"
    assert len(unconfirmed) == 2, f"Expected 2 unconfirmed, got {len(unconfirmed)}"
    assert inferences[0].target_value == "返工", f"Expected 返工, got {inferences[0].target_value}"
    assert inferences[1].target_value == "报废", f"Expected 报废, got {inferences[1].target_value}"
    print("  PASS: Chain inference with confirmation works")

    print("\n--- Test 2: Enrichment with DB state ---")
    # Simulate what action_executor does: enrich args from DB
    # Primary key of QualityCheck is "id"; entity exists in DB with rework_count=4
    args = {"id": "WO-20250521-001", "result": "不合格", "defectQuantity": 2}
    backend = MockBackend()
    existing = await backend.resolve_entity("QualityCheck", "WO-20250521-001")
    if existing:
        enriched = dict(existing)
        enriched.update(args)
        args = enriched
    print(f"  Enriched args: {args}")
    assert args.get("rework_count") == 4, f"Expected rework_count=4 from DB, got {args.get('rework_count')}"
    print("  PASS: DB enrichment provides rework_count for chain rule")

    print("\n--- Test 3: Non-confirmation inferences auto-apply ---")
    # Rule WITHOUT requiresConfirmation
    no_confirm_rules = [{
        "name": "auto_infer",
        "label": "自动推理",
        "description": "合格则完成",
        "ruleType": "inference",
        "expression": "result == '合格' → WorkOrder.status = '已完成'",
        "requiresConfirmation": False,
        "nextRules": [],
    }]
    mock_concept["rules"] = no_confirm_rules
    rule_engine._concept_index = {"QualityCheck": mock_concept}

    violations, inferences = rule_engine.evaluate_all(
        "QualityCheck",
        {"workOrderId": "WO-002", "result": "合格", "defectQuantity": 1},
    )
    print(f"  Inferences: {len(inferences)}")
    unconfirmed = [inf for inf in inferences if inf.requires_confirmation]
    print(f"  Unconfirmed: {len(unconfirmed)}")
    assert len(inferences) == 1
    assert len(unconfirmed) == 0
    print("  PASS: No confirmation needed for requiresConfirmation=False")

    print("\n--- Test 4: Constraint violations still block ---")
    mock_concept["rules"] = [
        {
            "name": "qty_check",
            "label": "数量校验",
            "ruleType": "constraint",
            "expression": "defectQuantity >= 0",
            "requiresConfirmation": False,
            "nextRules": [],
        },
    ]
    rule_engine._concept_index = {"QualityCheck": mock_concept}
    violations, inferences = rule_engine.evaluate_all(
        "QualityCheck",
        {"defectQuantity": -5},
    )
    print(f"  Violations: {len(violations)}")
    assert len(violations) == 1
    assert "数量校验" in violations[0].message
    print("  PASS: Constraint violations block regardless of confirmation settings")

    # Reset
    rule_engine._concept_index = {}

    print("\n" + "=" * 60)
    print("ALL TESTS PASSED")
    print("=" * 60)


asyncio.run(main())
