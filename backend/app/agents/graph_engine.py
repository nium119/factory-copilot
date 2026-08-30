# -*- coding: utf-8 -*-
"""Graph 引擎：沿本体关系图确定性扩散（执行层确定性工具）。

阶段 C「Graph-Loop 融合」：把散落在 DynamicPlanner 内部的影响链路扩展、BFS 排序、
递归下钻收编成独立组件，规划器（LLM）可显式调用——「影响分析/BOM 展开」不再靠
LLM 逐步猜，而是 Graph 引擎沿本体关系确定性扩散（既快又稳）。

数据源统一用 ontology_service（get_concept 的 relations），不依赖 action_executor 私有态。
"""
from typing import Optional

from app.core.logger import log

# 影响/结构展开类意图关键词（确定性触发，与动态规划里的一致）
_IMPACT_WORDS = ('影响', '取消', '延期', '变更', '减少', '停用', '废止', '呆滞', '库龄', '风险')


class GraphEngine:
    """沿本体关系图的确定性扩散器。"""

    def __init__(self, ontology_service=None):
        self._os = ontology_service

    def _get_os(self):
        if self._os is None:
            from app.services.ontology_service import ontology_service
            return ontology_service
        return self._os

    def is_impact_intent(self, message: str) -> bool:
        """判断消息是否影响/结构展开类意图（确定性触发，任意 namespace 通用）。"""
        return any(w in message for w in _IMPACT_WORDS)

    def expand_impact(self, concepts: list, message: str,
                      max_hops: int = 2, max_add: int = 6) -> list:
        """影响链路扩散：从源头概念沿本体关系 BFS 扩散，返回补入的相邻概念步骤。

        概念名完全来自本体 relations，跟随本体走；只补「有本体关系连接」的相邻概念，
        不写死概念名。返回 [{"concept","reason","type"}]，供规划器/执行器追加到计划。
        """
        if not self.is_impact_intent(message) or not concepts:
            return []
        os_ = self._get_os()
        planned = set(concepts)
        frontier = set(concepts)
        added: list = []
        for _hop in range(max_hops):
            if len(added) >= max_add:
                break
            next_frontier = set()
            for concept_name in list(frontier):
                cdef = os_.get_concept(concept_name)
                if not cdef:
                    continue
                for rel in (cdef.get('relations') or []):
                    target = rel.get('target', '')
                    if not target or target in planned or any(a['concept'] == target for a in added):
                        continue
                    added.append({
                        "concept": target,
                        "reason": f"影响链路关联（{rel.get('label', '')}）",
                        "type": "query",
                    })
                    next_frontier.add(target)
                    if len(added) >= max_add:
                        break
                if len(added) >= max_add:
                    break
            frontier = next_frontier
        if added:
            log.info(f"[GraphEngine] 影响链路扩散补 {len(added)} 概念: "
                     f"{[a['concept'] for a in added]}")
        return added

    def reorder_bfs(self, steps: list) -> list:
        """按本体关系做确定性 BFS 排序，保证「源头→分录→物料→BOM→采购/库存」链式顺序稳定。

        从源头（第一步概念）沿正向 relation（joinOn 非空）BFS；源头不可达的无关概念排最后。
        概念名/关系完全来自本体，不写死。
        """
        if len(steps) <= 2:
            return steps
        concept_set = {s["concept"] for s in steps}
        if len(concept_set) <= 1:
            return steps
        os_ = self._get_os()

        adj: dict = {c: [] for c in concept_set}
        for s in steps:
            c = s["concept"]
            cdef = os_.get_concept(c) or {}
            for rel in cdef.get("relations", []):
                tgt = rel.get("target", "")
                if tgt in concept_set and rel.get("joinOn") and tgt not in adj[c]:
                    adj[c].append(tgt)

        # 递归关系目标优先展开（本体 traversal 声明驱动，不写死概念名）
        def _recursive_rank(frm: str, tgt: str) -> int:
            fdef = os_.get_concept(frm) or {}
            rec_tgts = [r.get("target") for r in fdef.get("relations", [])
                        if (r.get("traversal") or "one_hop") == "recursive" and r.get("joinOn")]
            if tgt not in rec_tgts:
                return 2
            for other in rec_tgts:
                if other == tgt:
                    continue
                odef = os_.get_concept(other) or {}
                for r in odef.get("relations", []):
                    if r.get("target") == tgt and (r.get("traversal") or "one_hop") == "recursive":
                        return 1
            return 0

        for _c in adj:
            adj[_c].sort(key=lambda x: _recursive_rank(_c, x))

        start = steps[0]["concept"]
        reachable: list = []
        queue = [start]
        while queue:
            c = queue.pop(0)
            if c in reachable:
                continue
            reachable.append(c)
            for nb in adj.get(c, []):
                if nb not in reachable and nb not in queue:
                    queue.append(nb)

        step_map = {s["concept"]: s for s in steps}
        reordered = [step_map[c] for c in reachable if c in step_map]
        for s in steps:
            if s["concept"] not in reachable:
                reordered.append(s)
        if len(reordered) < len(concept_set):
            return steps
        if [s["concept"] for s in reordered] != [s["concept"] for s in steps]:
            log.info(f"[GraphEngine] BFS 排序: {[s['concept'] for s in reordered]}")
        return reordered

    def insert_child_material(self, steps: list) -> list:
        """递归关系下钻（本体 traversal 驱动，不写死概念名）。

        对每个步骤：若该概念存在 traversal=recursive 的出边关系，且 target 已在计划中
        （作为父层已查），则在其后补一步同概念查询，使报告能显示子层主数据。
        """
        os_ = self._get_os()
        pos = {}
        for i, s in enumerate(steps):
            pos.setdefault(s["concept"], i)
        planned_set = set(pos.keys())
        result: list = []
        appended: list = []
        for i, s in enumerate(steps):
            result.append(s)
            concept = s["concept"]
            cdef = os_.get_concept(concept) or {}
            for rel in cdef.get("relations", []):
                tgt = rel.get("target", "")
                if (rel.get("traversal") or "one_hop") != "recursive":
                    continue
                if not rel.get("joinOn"):
                    continue
                # 仅当 target 已作为父层查过（位置在当前步骤之前）才补子层查询
                if tgt in planned_set and tgt != concept and pos.get(tgt, 10**9) < i:
                    _step = {"concept": tgt, "reason": f"递归下钻（{rel.get('label') or tgt}）", "type": "query"}
                    result.append(_step)
                    appended.append(_step)
        if appended:
            log.info(f"[GraphEngine] 递归下钻补入: {[x['concept'] for x in appended]}")
        return result

    def recursive_pending(self, concept: str, records: list, depth: int, expanded: set) -> list:
        """执行时递归展开（本体 traversal=recursive 驱动，不写死概念名）。

        expanded 为去重集合，由调用方管理状态（防跨对话污染）；用 (概念,target,join值)
        去重防死循环，深度超过关系声明的 maxDepth 停止。
        """
        os_ = self._get_os()
        cdef = os_.get_concept(concept) or {}
        pending: list = []
        for rel in cdef.get("relations", []):
            tgt = rel.get("target", "")
            if (rel.get("traversal") or "one_hop") != "recursive" or not rel.get("joinOn"):
                continue
            # direction=incoming 是「引用回溯」，不是下钻方向——递归展开只沿 outgoing 走
            if (rel.get("direction") or "outgoing") != "outgoing":
                continue
            max_d = int(rel.get("maxDepth", 5) or 5)
            if depth + 1 > max_d:
                continue
            jk, _tk = self._parse_join_on(rel["joinOn"], concept, tgt)
            if not jk:
                continue
            has_new = False
            for rec in (records or []):
                v = rec.get(jk)
                if v is None:
                    continue
                key = (concept, tgt, str(v))
                if key not in expanded:
                    expanded.add(key)
                    has_new = True
            if has_new:
                pending.append({
                    "concept": tgt,
                    "reason": f"递归展开 L{depth + 1}（{rel.get('label') or tgt}）",
                    "type": "query",
                    "_depth": depth + 1,
                })
        return pending

    @staticmethod
    def _parse_join_on(join_on: str, from_concept: str, to_concept: str) -> tuple:
        """解析 joinOn 字符串，提取 from/to 两侧的属性名。"""
        from_key, to_key = None, None
        for part in join_on.split("="):
            part = part.strip()
            if part.startswith(from_concept + "."):
                from_key = part.split(".")[1].strip()
            elif part.startswith(to_concept + "."):
                to_key = part.split(".")[1].strip()
        return (from_key, to_key)


# 全局单例
graph_engine = GraphEngine()
