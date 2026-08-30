# -*- coding: utf-8 -*-
"""阶段 D 单元测试：六闸门治理流水线 + 审计报告。"""
import pytest

from app.agents.governance import (
    GATE_ORDER,
    OUTCOME_APPROVE,
    OUTCOME_BLOCK,
    OUTCOME_DELEGATE,
    OUTCOME_PASS,
    RISK_BY_OUTPUT,
    GateResult,
    GovernancePipeline,
    GovernanceReport,
    governance_pipeline,
)


class TestGateToolBoundary:
    def test_empty_tool_blocks(self):
        r = governance_pipeline.gate_tool_boundary("", None)
        assert r.outcome == OUTCOME_BLOCK

    def test_unregistered_tool_blocks(self):
        r = governance_pipeline.gate_tool_boundary("Nope_create", {"A_query": {}})
        assert r.outcome == OUTCOME_BLOCK

    def test_registered_tool_passes(self):
        r = governance_pipeline.gate_tool_boundary("A_query", {"A_query": {}})
        assert r.outcome == OUTCOME_PASS

    def test_none_sigs_passes(self):
        # sigs=None 表示不校验注册（兼容未加载场景）
        r = governance_pipeline.gate_tool_boundary("A_query", None)
        assert r.outcome == OUTCOME_PASS


class TestGateRisk:
    def test_read_passes(self):
        assert governance_pipeline.gate_risk("query").outcome == OUTCOME_PASS

    def test_write_approves(self):
        for ot in ("create", "delete", "update", "schedule", "write"):
            assert governance_pipeline.gate_risk(ot).outcome == OUTCOME_APPROVE

    def test_unknown_defaults_approve(self):
        assert governance_pipeline.gate_risk("mystery").outcome == OUTCOME_APPROVE


class TestGovernanceReport:
    def test_blocked(self):
        r = GovernanceReport(tool_name="X")
        r.results = [GateResult(gate="tool_boundary", outcome=OUTCOME_BLOCK, reason="")]
        assert r.blocked is True

    def test_needs_approval(self):
        r = GovernanceReport(tool_name="X")
        r.results = [GateResult(gate="risk", outcome=OUTCOME_APPROVE, reason="")]
        assert r.blocked is False
        assert r.needs_approval is True

    def test_summary(self):
        r = GovernanceReport(tool_name="A_create")
        r.results = [
            GateResult(gate="tool_boundary", outcome=OUTCOME_PASS),
            GateResult(gate="risk", outcome=OUTCOME_APPROVE, reason="写操作"),
        ]
        assert "A_create" in r.summary()
        assert "risk=approve" in r.summary()


class TestEvaluate:
    @pytest.mark.asyncio
    async def test_blocked_short_circuits(self):
        report = await governance_pipeline.evaluate("", "query")
        assert report.blocked is True
        assert len(report.results) == 1  # 工具边界拦截，后续闸门不跑
        assert report.results[0].gate == "tool_boundary"

    @pytest.mark.asyncio
    async def test_write_tool_needs_approval(self, monkeypatch):
        # mock 数据权限，避免依赖 Neo4j
        async def fake_filters(tool, uid, args):
            return []
        from app.services import action_executor as _ae
        monkeypatch.setattr(_ae.action_executor, "apply_data_filters", fake_filters)
        report = await governance_pipeline.evaluate(
            "WorkOrder_create", "create", sigs={"WorkOrder_create": {}},
            concept_name="WorkOrder", requires_confirmation=True)
        assert report.blocked is False
        assert report.needs_approval is True
        # 六闸门顺序
        assert [r.gate for r in report.results] == [
            "tool_boundary", "rbac", "data_permission", "rule_constraint", "risk", "approval",
        ]
        # 审批门禁 outcome = approve（写操作需确认）
        assert report.results[-1].outcome == OUTCOME_APPROVE


# ── ② RBAC ──

class TestGateRbac:
    @pytest.mark.asyncio
    async def test_no_roles_passes(self):
        assert (await governance_pipeline.gate_rbac("u1", [])).outcome == OUTCOME_PASS

    @pytest.mark.asyncio
    async def test_user_has_role_passes(self, monkeypatch):
        async def fake_roles(uid):
            return {"admin", "planner"}
        from app.services import auth_service as _as
        monkeypatch.setattr(_as.auth_service, "get_effective_roles", fake_roles)
        r = await governance_pipeline.gate_rbac("u1", ["admin"])
        assert r.outcome == OUTCOME_PASS

    @pytest.mark.asyncio
    async def test_user_lacks_role_delegates(self, monkeypatch):
        async def fake_roles(uid):
            return {"viewer"}
        from app.services import auth_service as _as
        monkeypatch.setattr(_as.auth_service, "get_effective_roles", fake_roles)
        r = await governance_pipeline.gate_rbac("u1", ["admin"])
        assert r.outcome == OUTCOME_DELEGATE

    @pytest.mark.asyncio
    async def test_no_user_delegates(self):
        r = await governance_pipeline.gate_rbac("", ["admin"])
        assert r.outcome == OUTCOME_DELEGATE


# ── ③ 数据权限 ──

class TestGateDataPermission:
    @pytest.mark.asyncio
    async def test_filters_recorded(self, monkeypatch):
        async def fake_filters(tool, uid, args):
            args["_scope_value"] = "P01"
            return ["scope: P01"]
        from app.services import action_executor as _ae
        monkeypatch.setattr(_ae.action_executor, "apply_data_filters", fake_filters)
        params = {}
        r = await governance_pipeline.gate_data_permission("X_query", "u1", params)
        assert r.outcome == OUTCOME_PASS
        assert r.detail["filters"] == ["scope: P01"]


# ── ④ 业务规则 constraint ──

class TestGateRuleConstraint:
    @pytest.mark.asyncio
    async def test_no_concept_passes(self):
        r = await governance_pipeline.gate_rule_constraint("", {}, "")
        assert r.outcome == OUTCOME_PASS

    @pytest.mark.asyncio
    async def test_violations_block(self, monkeypatch):
        from unittest.mock import MagicMock
        from app.services import rule_engine as _re
        monkeypatch.setattr(
            _re.rule_engine, "evaluate_all",
            lambda c, p, a: ([MagicMock(message="数量超限")], [], []))
        r = await governance_pipeline.gate_rule_constraint("WorkOrder", {"qty": 999}, "create")
        assert r.outcome == OUTCOME_BLOCK
        assert "数量超限" in r.reason

    @pytest.mark.asyncio
    async def test_no_violations_passes(self, monkeypatch):
        from app.services import rule_engine as _re
        monkeypatch.setattr(_re.rule_engine, "evaluate_all", lambda c, p, a: ([], [], []))
        r = await governance_pipeline.gate_rule_constraint("WorkOrder", {}, "create")
        assert r.outcome == OUTCOME_PASS


# ── ⑥ 审批门禁 ──

class TestGateApproval:
    def test_delegate(self):
        assert governance_pipeline.gate_approval(True, True).outcome == OUTCOME_DELEGATE

    def test_approve(self):
        assert governance_pipeline.gate_approval(True, False).outcome == OUTCOME_APPROVE

    def test_pass(self):
        assert governance_pipeline.gate_approval(False, False).outcome == OUTCOME_PASS
