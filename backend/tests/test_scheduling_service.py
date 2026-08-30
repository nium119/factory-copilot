# -*- coding: utf-8 -*-
"""通用排程引擎纯单元测试：DP 精确 + 遗传算法 + auto_schedule 守卫分支。

只测确定性核心（_dp_schedule / _genetic_schedule）与「无硬编码」守卫路径，
不依赖 Neo4j 数据。auto_schedule 的落库/读图由活体对话测试覆盖。
"""
import pytest

from app.services.scheduling_service import (
    _dp_schedule,
    _genetic_schedule,
    scheduling_service,
)


def _wo(wid, *ops):
    """构造工单：ops 为 (op_id, seq, machine, duration) 元组。"""
    return {
        "id": wid,
        "operations": [
            {"id": oid, "seq": seq, "machineId": m, "duration": dur}
            for oid, seq, m, dur in ops
        ],
    }


# ═══════════════════════════════════════════════════════════════
# 动态规划（精确）
# ═══════════════════════════════════════════════════════════════

class TestDpSchedule:
    def test_covers_all_operations(self):
        wos = [_wo("WO-1", ("OP-1-1", 1, "M1", 2.0), ("OP-1-2", 2, "M2", 3.0)),
               _wo("WO-2", ("OP-2-1", 1, "M1", 4.0))]
        makespan, schedule = _dp_schedule(wos)
        assert set(schedule) == {"OP-1-1", "OP-1-2", "OP-2-1"}
        assert makespan > 0

    def test_seq_order_preserved_within_workorder(self):
        """同一工单的工序必须按 seq 顺序执行（后序不早于前序结束）。"""
        wos = [_wo("WO-1", ("OP-1-1", 1, "M1", 1.0), ("OP-1-2", 2, "M2", 2.0),
                   ("OP-1-3", 3, "M3", 3.0))]
        _, schedule = _dp_schedule(wos)
        assert schedule["OP-1-2"]["start"] >= schedule["OP-1-1"]["end"]
        assert schedule["OP-1-3"]["start"] >= schedule["OP-1-2"]["end"]

    def test_same_machine_no_overlap(self):
        wos = [_wo("WO-1", ("OP-1", 1, "M1", 2.0)),
               _wo("WO-2", ("OP-2", 1, "M1", 3.0))]
        _, schedule = _dp_schedule(wos)
        a, b = schedule["OP-1"], schedule["OP-2"]
        assert a["end"] <= b["start"] or b["end"] <= a["start"]

    def test_two_jobs_one_machine_optimal(self):
        """两工单各一道工序共用一台机器：makespan = 时长和。"""
        wos = [_wo("WO-1", ("OP-1", 1, "M1", 2.0)),
               _wo("WO-2", ("OP-2", 1, "M1", 3.0))]
        makespan, _ = _dp_schedule(wos)
        assert makespan == pytest.approx(5.0 * 60.0)

    def test_makespan_at_least_longest_chain(self):
        """makespan 不小于任一工单的工序时长总和。"""
        wos = [_wo("WO-1", ("OP-1", 1, "M1", 2.0), ("OP-2", 2, "M2", 3.0))]
        makespan, _ = _dp_schedule(wos)
        assert makespan >= 5.0 * 60.0


# ═══════════════════════════════════════════════════════════════
# 遗传算法（近似）
# ═══════════════════════════════════════════════════════════════

class TestGeneticSchedule:
    def test_covers_all_operations(self):
        wos = [_wo("WO-1", ("OP-1-1", 1, "M1", 2.0), ("OP-1-2", 2, "M2", 3.0)),
               _wo("WO-2", ("OP-2-1", 1, "M1", 4.0))]
        makespan, order, schedule = _genetic_schedule(wos)
        assert set(schedule) == {"OP-1-1", "OP-1-2", "OP-2-1"}
        assert makespan > 0

    def test_deterministic_with_fixed_seed(self):
        wos = [_wo("WO-1", ("OP-1-1", 1, "M1", 1.5), ("OP-1-2", 2, "M2", 2.5)),
               _wo("WO-2", ("OP-2-1", 1, "M3", 3.0), ("OP-2-2", 2, "M1", 1.0))]
        m1, o1, s1 = _genetic_schedule(wos, seed=42)
        m2, o2, s2 = _genetic_schedule(wos, seed=42)
        assert m1 == m2
        assert o1 == o2
        assert s1 == s2

    def test_seq_order_preserved_within_workorder(self):
        wos = [_wo("WO-1", ("OP-1-1", 1, "M1", 1.0), ("OP-1-2", 2, "M2", 2.0))]
        _, _, schedule = _genetic_schedule(wos)
        assert schedule["OP-1-2"]["start"] >= schedule["OP-1-1"]["end"]

    def test_same_machine_no_overlap(self):
        wos = [_wo("WO-1", ("OP-1", 1, "M1", 2.0)),
               _wo("WO-2", ("OP-2", 1, "M1", 3.0))]
        _, _, schedule = _genetic_schedule(wos)
        a, b = schedule["OP-1"], schedule["OP-2"]
        assert a["end"] <= b["start"] or b["end"] <= a["start"]


# ═══════════════════════════════════════════════════════════════
# auto_schedule 守卫分支（无硬编码、确定性报错）
# ═══════════════════════════════════════════════════════════════

class TestAutoScheduleGuards:
    @pytest.mark.asyncio
    async def test_missing_concept_reports_error(self, monkeypatch):
        from app.services import ontology_service as os_mod

        monkeypatch.setattr(os_mod.ontology_service, "get_concept", lambda name: None)
        result = await scheduling_service.auto_schedule("APS", "NoSuchConcept")
        assert result["scheduled"] == 0
        assert "不存在" in result["message"]

    @pytest.mark.asyncio
    async def test_no_operation_child_reports_error(self, monkeypatch):
        from app.services import ontology_service as os_mod

        # 概念存在但无 HasMany 关系 → 找不到工序
        concept = {"name": "WorkOrder", "label": "工单",
                   "properties": [{"name": "id", "isPrimary": True, "type": "string"}],
                   "relations": []}
        monkeypatch.setattr(os_mod.ontology_service, "get_concept", lambda name: concept)
        result = await scheduling_service.auto_schedule("APS", "WorkOrder")
        assert result["scheduled"] == 0
        assert "工序" in result["message"]

    @pytest.mark.asyncio
    async def test_child_without_machine_ref_reports_error(self, monkeypatch):
        from app.services import ontology_service as os_mod

        # 工单 HasMany → Operation，但 Operation 只有 ref→工单，缺 ref→机器
        wo = {
            "name": "WorkOrder", "label": "工单",
            "properties": [{"name": "id", "isPrimary": True, "type": "string"}],
            "relations": [{"type": "hasMany", "target": "Operation"}],
        }
        op = {
            "name": "Operation", "label": "工序",
            "properties": [
                {"name": "id", "isPrimary": True, "type": "string"},
                {"name": "workOrderId", "type": "ref", "refConcept": "WorkOrder"},
            ],
            "relations": [],
        }
        concepts = {"WorkOrder": wo, "Operation": op}
        monkeypatch.setattr(os_mod.ontology_service, "get_concept",
                            lambda name: concepts.get(name))
        result = await scheduling_service.auto_schedule("APS", "WorkOrder")
        assert result["scheduled"] == 0
        assert "机器" in result["message"]
