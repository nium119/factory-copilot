# -*- coding: utf-8 -*-
"""阶段 C 单元测试：GraphEngine 沿本体关系图确定性扩散。

用 mock 本体关系图验证：影响链路 BFS 扩散 + 确定性 BFS 排序（不写死概念名）。
"""
from app.agents.graph_engine import GraphEngine, _IMPACT_WORDS


# 概念关系图（mock 本体）：
#   WorkOrder →(joinOn) Material →(joinOn) BOM →(joinOn) BOMItem
#   WorkOrder →(joinOn) Operation（叶子）
_RELATIONS = {
    "WorkOrder": [
        {"target": "Material", "label": "使用物料", "joinOn": "materialCode"},
        {"target": "Operation", "label": "工序", "joinOn": "workOrderId"},
    ],
    "Material": [
        {"target": "BOM", "label": "对应BOM", "joinOn": "materialCode"},
    ],
    "BOM": [
        {"target": "BOMItem", "label": "清单项", "joinOn": "bomId"},
    ],
    "Operation": [],
    "BOMItem": [],
}


class _FakeOntology:
    def get_concept(self, name):
        rels = _RELATIONS.get(name)
        if rels is None:
            return None
        return {"name": name, "relations": rels}


def _engine():
    return GraphEngine(_FakeOntology())


class TestIsImpactIntent:
    def test_impact_words(self):
        e = _engine()
        assert e.is_impact_intent("分析工单影响哪些物料") is True
        assert e.is_impact_intent("取消订单后有什么风险") is True

    def test_non_impact(self):
        e = _engine()
        assert e.is_impact_intent("查询工单") is False


class TestExpandImpact:
    def test_bfs_two_hops(self):
        e = _engine()
        added = e.expand_impact(["WorkOrder"], "分析影响哪些物料")
        concepts = [a["concept"] for a in added]
        # hop1: Material, Operation; hop2: BOM
        assert "Material" in concepts
        assert "Operation" in concepts
        assert "BOM" in concepts

    def test_no_impact_word_returns_empty(self):
        e = _engine()
        assert e.expand_impact(["WorkOrder"], "查询工单") == []

    def test_empty_concepts_returns_empty(self):
        e = _engine()
        assert e.expand_impact([], "影响分析") == []


class TestReorderBfs:
    def test_chain_order(self):
        e = _engine()
        steps = [
            {"concept": "WorkOrder"},
            {"concept": "BOMItem"},
            {"concept": "Material"},
            {"concept": "BOM"},
        ]
        reordered = e.reorder_bfs(steps)
        # BFS 从 WorkOrder 出发：WorkOrder → Material → BOM → BOMItem
        assert [s["concept"] for s in reordered] == [
            "WorkOrder", "Material", "BOM", "BOMItem",
        ]

    def test_short_steps_unchanged(self):
        e = _engine()
        steps = [{"concept": "WorkOrder"}, {"concept": "Material"}]
        assert e.reorder_bfs(steps) == steps


# ── 递归关系图（traversal=recursive）──
_RECURSIVE_RELATIONS = {
    "BOMItem": [
        {"target": "Material", "label": "子件物料",
         "joinOn": "BOMItem.materialCode=Material.materialCode",
         "traversal": "recursive", "maxDepth": 3},
    ],
    "Material": [],
}


class _RecursiveOntology:
    def get_concept(self, name):
        rels = _RECURSIVE_RELATIONS.get(name)
        return {"name": name, "relations": rels} if rels is not None else None


def _rec_engine():
    return GraphEngine(_RecursiveOntology())


class TestInsertChildMaterial:
    def test_child_material_inserted_after_parent(self):
        e = _rec_engine()
        steps = [
            {"concept": "Material"},  # 父层（子件物料）先查，位置 0
            {"concept": "BOMItem"},   # BOM分录，位置 1
        ]
        result = e.insert_child_material(steps)
        # BOMItem 有 recursive 出边到 Material，且 Material 在位置 0 < 1 → 补一步
        assert [s["concept"] for s in result] == ["Material", "BOMItem", "Material"]

    def test_no_parent_before_does_not_insert(self):
        e = _rec_engine()
        steps = [{"concept": "BOMItem"}, {"concept": "Material"}]
        result = e.insert_child_material(steps)
        # Material 在 BOMItem 之后（子层本来就在后面），不补
        assert [s["concept"] for s in result] == ["BOMItem", "Material"]


class TestRecursivePending:
    def test_recursive_expansion_and_dedup(self):
        e = _rec_engine()
        expanded = set()
        records = [{"materialCode": "MAT-9002"}]
        pending = e.recursive_pending("BOMItem", records, 1, expanded)
        assert pending and pending[0]["concept"] == "Material"
        # 同一 join 值再次展开 → 已去重，返回空
        assert e.recursive_pending("BOMItem", records, 1, expanded) == []

    def test_depth_limit_stops(self):
        e = _rec_engine()
        expanded = set()
        records = [{"materialCode": "MAT-9002"}]
        # depth 已达 maxDepth（3）→ 不再展开
        pending = e.recursive_pending("BOMItem", records, 3, expanded)
        assert pending == []

