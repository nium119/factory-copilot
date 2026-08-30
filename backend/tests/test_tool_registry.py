# -*- coding: utf-8 -*-
"""ToolRegistry 纯单元测试 + 能力发现确定性辅助函数。

不依赖 Neo4j / LLM，是统一循环「执行层」底座（工具注册表）的回归基线。
"""
from app.services.tool_registry import (
    ToolRegistry,
    build_capability_fallback,
    filter_writes_by_verb,
)


# ═══════════════════════════════════════════════════════════════
# 注册 / 查询基础
# ═══════════════════════════════════════════════════════════════

class TestRegisterAndQuery:
    def test_register_and_get(self):
        reg = ToolRegistry()
        reg.register({"name": "WorkOrder_query"})
        assert reg.get("WorkOrder_query") == {"name": "WorkOrder_query"}
        assert reg.has("WorkOrder_query") is True

    def test_get_missing_returns_none(self):
        reg = ToolRegistry()
        assert reg.get("nope") is None
        assert reg.has("nope") is False

    def test_register_ignores_empty_name(self):
        reg = ToolRegistry()
        reg.register({"name": ""})
        reg.register({})
        assert reg.get_all() == []

    def test_get_all_returns_copy(self):
        reg = ToolRegistry()
        reg.register({"name": "a"})
        reg.register({"name": "b"})
        names = {t["name"] for t in reg.get_all()}
        assert names == {"a", "b"}

    def test_get_writes_excludes_read(self):
        reg = ToolRegistry()
        reg.register({"name": "A_query", "risk": "READ"})
        reg.register({"name": "B_create", "risk": "WRITE_APPROVE"})
        reg.register({"name": "C_delete", "risk": "WRITE_APPROVE"})
        writes = reg.get_writes()
        assert {w["name"] for w in writes} == {"B_create", "C_delete"}

    def test_get_by_source(self):
        reg = ToolRegistry()
        reg.register({"name": "a", "source": "ontology"})
        reg.register({"name": "b", "source": "mcp"})
        reg.register({"name": "c", "source": "ontology"})
        assert {t["name"] for t in reg.get_by_source("ontology")} == {"a", "c"}
        assert {t["name"] for t in reg.get_by_source("mcp")} == {"b"}


# ═══════════════════════════════════════════════════════════════
# collect_ontology — 风险分级
# ═══════════════════════════════════════════════════════════════

def _sig(fn, output_type="write", **kw):
    sig = {"functionName": fn, "outputType": output_type,
           "actionLabel": f"{fn}标签", "description": "desc",
           "conceptName": "WorkOrder", "conceptLabel": "工单",
           "params": [], "actionName": "", "requiresConfirmation": False,
           "authorized_roles": []}
    sig.update(kw)
    return sig


class TestCollectOntology:
    def test_query_suffix_is_read(self):
        reg = ToolRegistry()
        reg.collect_ontology([_sig("WorkOrder_query")])
        assert reg.get("WorkOrder_query")["risk"] == "READ"
        assert reg.get("WorkOrder_query")["output_type"] == "query"

    def test_delete_suffix_is_write_approve(self):
        reg = ToolRegistry()
        reg.collect_ontology([_sig("WorkOrder_delete")])
        assert reg.get("WorkOrder_delete")["risk"] == "WRITE_APPROVE"

    def test_similarity_suffix_is_read(self):
        reg = ToolRegistry()
        reg.collect_ontology([_sig("WorkOrder_findSimilar")])
        assert reg.get("WorkOrder_findSimilar")["risk"] == "READ"

    def test_schedule_risky_even_without_suffix(self):
        # 排程动作名如 WorkOrder_schedule，无 _query 后缀 → outputType 兜底
        reg = ToolRegistry()
        reg.collect_ontology([_sig("WorkOrder_schedule", output_type="schedule")])
        assert reg.get("WorkOrder_schedule")["risk"] == "WRITE_APPROVE"

    def test_unknown_output_defaults_write_approve(self):
        reg = ToolRegistry()
        reg.collect_ontology([_sig("WorkOrder_create", output_type="")])
        assert reg.get("WorkOrder_create")["risk"] == "WRITE_APPROVE"

    def test_skip_empty_function_name(self):
        reg = ToolRegistry()
        count = reg.collect_ontology([_sig(""), _sig("WorkOrder_query")])
        assert count == 1

    def test_fields_passthrough(self):
        reg = ToolRegistry()
        reg.collect_ontology([_sig(
            "WorkOrder_create", output_type="create",
            conceptName="WorkOrder", conceptLabel="工单",
            actionName="create", requiresConfirmation=True,
            authorized_roles=["admin"], params=[{"name": "id"}],
        )])
        t = reg.get("WorkOrder_create")
        assert t["concept_name"] == "WorkOrder"
        assert t["concept_label"] == "工单"
        assert t["action_name"] == "create"
        assert t["requires_confirmation"] is True
        assert t["authorized_roles"] == ["admin"]
        assert t["params"] == [{"name": "id"}]
        assert t["source"] == "ontology"


# ═══════════════════════════════════════════════════════════════
# rebuild / ensure_loaded
# ═══════════════════════════════════════════════════════════════

class _FakeOntology:
    def __init__(self, sigs):
        self._sigs = sigs

    def get_action_signatures(self):
        return self._sigs


class TestRebuildAndLazyLoad:
    def test_rebuild_clears_and_counts(self):
        reg = ToolRegistry()
        fake = _FakeOntology([_sig("A_query"), _sig("B_create")])
        total = reg.rebuild(fake)
        # ontology 2 个（MCP registry 不可用时 collect_mcp 返回 0）
        assert total >= 2
        assert reg.has("A_query") and reg.has("B_create")

    def test_ensure_loaded_rebuilds_once(self):
        reg = ToolRegistry()
        fake = _FakeOntology([_sig("A_query")])
        assert reg.ensure_loaded(fake) >= 1
        # 第二次调用：已有数据，不再重建（注册表保持原样）
        reg.register({"name": "manual_tool", "source": "manual"})
        n = reg.ensure_loaded(fake)
        assert reg.has("manual_tool"), "ensure_loaded 不应清空已有工具"
        assert n >= 1


# ═══════════════════════════════════════════════════════════════
# 能力发现确定性辅助函数
# ═══════════════════════════════════════════════════════════════

class TestFilterWritesByVerb:
    def test_no_verb_returns_all(self):
        ops = [{"name": "A_create"}, {"name": "B_delete"}]
        assert filter_writes_by_verb(ops, "帮我做点事情") == ops

    def test_create_verb_filters(self):
        ops = [{"name": "SalesOrder_create"}, {"name": "WorkOrder_delete"}]
        result = filter_writes_by_verb(ops, "创建单据")
        assert [c["name"] for c in result] == ["SalesOrder_create"]

    def test_filter_empty_falls_back_to_all(self):
        ops = [{"name": "WorkOrder_delete"}]
        # 用户说"创建"但没有 create 类操作 → 保留原样，避免空清单
        assert filter_writes_by_verb(ops, "创建单据") == ops

    def test_schedule_verb_matches_suffix(self):
        ops = [{"name": "WorkOrder_schedule"}, {"name": "SalesOrder_create"}]
        result = filter_writes_by_verb(ops, "运行自动排程")
        assert [c["name"] for c in result] == ["WorkOrder_schedule"]

    def test_insertorder_verb_matches_camelcase(self):
        ops = [{"name": "SalesOrder_insertOrder"}, {"name": "WorkOrder_schedule"}]
        result = filter_writes_by_verb(ops, "插单")
        assert [c["name"] for c in result] == ["SalesOrder_insertOrder"]


class TestBuildCapabilityFallback:
    def test_fallback_lists_ops_with_labels(self):
        ops = [
            {"name": "SalesOrder_create", "label": "创建销售订单",
             "concept_label": "销售订单", "concept_name": "SalesOrder"},
            {"name": "WorkOrder_create", "label": "创建工单",
             "concept_label": "工单", "concept_name": "WorkOrder"},
        ]
        text = build_capability_fallback("创建单据", ops)
        assert "创建单据" in text
        assert "• 创建销售订单（销售订单）" in text
        assert "• 创建工单（工单）" in text

    def test_fallback_respects_limit(self):
        ops = [{"name": f"T{i}_create", "label": f"操作{i}",
                "concept_label": f"概念{i}", "concept_name": f"T{i}"}
               for i in range(20)]
        text = build_capability_fallback("创建", ops, limit=8)
        assert "• 操作0" in text
        assert "• 操作8" not in text  # 只列前 8 个

    def test_fallback_falls_back_to_name(self):
        ops = [{"name": "X_create", "label": "", "concept_label": "", "concept_name": ""}]
        text = build_capability_fallback("创建", ops)
        assert "X_create" in text
