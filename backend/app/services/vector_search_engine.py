"""向量搜索引擎 — 通用 GraphRAG 混合检索（图 + 向量并行 → RRF 融合 → LLM 精排）。

按 Neo4j Concept 节点的 vectorization 配置自适应，
非特定概念专属——同一套代码服务所有 vectorization.enabled 的概念。
"""

import asyncio
import json
import math
import re
from typing import Any, Optional

from dataclasses import dataclass, field

from app.core.config import settings
from app.core.logger import log


# ── 向量化配置（可从前端调整） ──────────────────────────

@dataclass
class VectorizationSettings:
    maintenance_interval: int = 60    # 后台索引维护间隔（秒）
    default_topK: int = 5             # 默认返回数量
    graph_weight: float = 0.7         # 图匹配权重
    vector_weight: float = 0.3        # 向量匹配权重（自动 = 1 - graph_weight）
    max_candidates: int = 1000        # 单次检索最大候选数

    def to_dict(self):
        return {
            "maintenanceInterval": self.maintenance_interval,
            "defaultTopK": self.default_topK,
            "graphWeight": self.graph_weight,
            "maxCandidates": self.max_candidates,
        }

    def update(self, data: dict):
        if "maintenanceInterval" in data:
            self.maintenance_interval = int(data["maintenanceInterval"])
        if "defaultTopK" in data:
            self.default_topK = int(data["defaultTopK"])
        if "graphWeight" in data:
            self.graph_weight = float(data["graphWeight"])


_vec_settings = VectorizationSettings()


def get_vectorization_settings() -> VectorizationSettings:
    return _vec_settings


# ── RRF 融合 ──────────────────────────────────────────────

def _rrf_fuse(results_a: list[dict], results_b: list[dict], k: int = 60) -> list[dict]:
    """Reciprocal Rank Fusion — 按排序位置融合两路结果。

    score(d) = Σ 1 / (k + rank_i(d))
    k=60 平滑长尾，Elasticsearch / Neo4j Hybrid 标准参数。
    """
    merged: dict[str, dict] = {}
    for rank, item in enumerate(results_a):
        key = item.get("pk") or item.get("name", "")
        if not key:
            continue
        entry = merged.setdefault(key, dict(item))
        entry["_rrf_score"] = entry.get("_rrf_score", 0) + 1.0 / (k + rank + 1)
        entry["_rrf_sources"] = entry.get("_rrf_sources", 0) | 1

    for rank, item in enumerate(results_b):
        key = item.get("pk") or item.get("name", "")
        if not key:
            continue
        entry = merged.setdefault(key, dict(item))
        entry["_rrf_score"] = entry.get("_rrf_score", 0) + 1.0 / (k + rank + 1)
        entry["_rrf_sources"] = entry.get("_rrf_sources", 0) | 2

    return sorted(merged.values(), key=lambda x: x.get("_rrf_score", 0), reverse=True)


# ── 余弦相似度 ────────────────────────────────────────────

def _cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """纯 Python 余弦相似度，numpy 都不需要。"""
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# ── 搜索引擎 ───────────────────────────────────────────────

class VectorSearchEngine:
    """通用向量搜索引擎。

    读取 Neo4j Concept 节点的 vectorization 配置，
    自动适配图检索 + 向量检索 + RRF 融合 + LLM 精排。
    """

    def __init__(self):
        self._concept_cache: dict[str, dict] = {}
        self._maintenance_task = None

    # ── 后台索引维护 ──────────────────────────────────────

    async def start_maintenance(self, interval: int = None):
        if interval is None:
            interval = _vec_settings.maintenance_interval
        """启动后台索引补全循环。FC 启动时调用。"""
        if self._maintenance_task:
            return
        self._maintenance_task = asyncio.create_task(self._maintenance_loop(interval))
        log.info(f"[VectorSearch] 后台索引维护已启动 (间隔 {interval}s)")

    async def stop_maintenance(self):
        if self._maintenance_task:
            self._maintenance_task.cancel()
            self._maintenance_task = None

    async def _maintenance_loop(self, interval: int):
        while True:
            try:
                await asyncio.sleep(interval)
                from app.services.ontology_service import ontology_service
                concepts = ontology_service.get_concepts()
                for c in concepts:
                    vec_cfg = c.get("vectorization")
                    if isinstance(vec_cfg, str):
                        try:
                            vec_cfg = json.loads(vec_cfg)
                        except Exception:
                            continue
                    if not vec_cfg or not vec_cfg.get("enabled"):
                        continue
                    concept_name = c.get("name", "")
                    ns = settings.NEO4J_NAMESPACE or ""
                    pending_rec = await self._neo4j().execute_read(f"""
                        MATCH (n:{concept_name})
                        WHERE n.embedding IS NULL AND (n._namespace = $ns OR $ns = '')
                        RETURN count(n) AS cnt
                    """, {"ns": ns})
                    if pending_rec and pending_rec[0]["cnt"] > 0:
                        log.info(f"[VectorSearch] 后台补全 {concept_name}: {pending_rec[0]['cnt']} 个")
                        await self._rebuild_concept(concept_name)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.warning(f"[VectorSearch] 后台维护异常: {e}")

    def _neo4j(self):
        from app.services.neo4j_service import neo4j_service
        return neo4j_service

    async def _rebuild_concept(self, concept_name: str, progress_cb=None):
        """立即重建指定概念的向量索引。progress_cb(done, total) 用于流式进度。"""
        from app.core.model_config import create_embedding
        ns = settings.NEO4J_NAMESPACE or ""
        config = await self._load_vectorization_config(concept_name)
        if isinstance(config, str):
            config = json.loads(config)
        if not config or not config.get("enabled"):
            return
        fp_cfg = config.get("fingerprint", {})
        pk = await self._get_primary_key(concept_name)
        emb_obj = create_embedding()
        batch, total = 0, 0
        records = await self._neo4j().execute_read(f"""
            MATCH (n:{concept_name})
            WHERE n.embedding IS NULL AND (n._namespace = $ns OR $ns = '')
            RETURN n.{pk} AS pk
        """, {"ns": ns})
        total = len(records or [])
        if progress_cb:
            progress_cb(0, total)
        for rec in (records or []):
            try:
                node_data = await self._load_node_data(concept_name, rec["pk"], config)
                if node_data:
                    fp = self._build_fingerprint(node_data, fp_cfg)
                    if fp:
                        vec = await asyncio.to_thread(emb_obj.embed_query, fp)
                        vec_list = vec if isinstance(vec, list) else []
                        await self._neo4j().execute_write(f"""
                            MATCH (n:{concept_name})
                            WHERE (toString(n.{pk}) = $pk_val OR n.{pk} = toIntegerOrNull($pk_val))
                            SET n.embedding = $embedding
                        """, {"pk_val": rec["pk"], "embedding": json.dumps(vec_list, ensure_ascii=False)})
                        batch += 1
                        if progress_cb:
                            progress_cb(batch, total)
            except Exception as e:
                log.warning(f"[VectorSearch] 重建失败 {concept_name}/{rec['pk']}: {e}")
        log.info(f"[VectorSearch] 重建完成 {concept_name}: {batch}/{total}")

    # ── 公共入口 ──────────────────────────────────────────

    async def find_similar(
        self,
        concept_name: str,
        target_key: str,
        topK: int = None,
        *,
        arguments: dict = None,
    ) -> tuple[str, list]:
        """主入口：读取配置 → 两路并行 → RRF → LLM 精排 → Markdown。

        Args:
            concept_name: 概念名（如 WorkOrderBOM）
            target_key: 目标实体主键值
            topK: 返回数量（默认取全局配置）
            arguments: 原始 arguments（用于从 message 中提取 target_key）

        Returns:
            (result_text: Markdown, records: 候选列表)
        """
        if topK is None:
            topK = _vec_settings.default_topK
        config = await self._load_vectorization_config(concept_name)
        if isinstance(config, str):
            config = json.loads(config)
        if not config or not config.get("enabled"):
            return (f"概念 {concept_name} 未启用向量化。请在向量化配置中启用。", [])

        # 从 arguments.message 中提取目标（如果 target_key 为空）
        if not target_key and arguments:
            target_key = await self._extract_target_from_message(
                concept_name, arguments.get("message", "")
            )
        if not target_key:
            return ("未找到目标实体。请提供主键值或在消息中提及目标。", [])

        # 跨概念目标解析：用户可能用关联概念的标识（如工单号MO001指代BOM）
        resolved_key = await self._resolve_target(concept_name, target_key)
        if resolved_key and resolved_key != target_key:
            log.info(f"[VectorSearch] 跨概念解析: {target_key} → {concept_name}.{resolved_key}")
            target_key = resolved_key

        # 两路并行检索
        fetch_size = topK * 3
        graph_task = asyncio.create_task(
            self._graph_search(concept_name, config, target_key, fetch_size)
        )
        vector_task = asyncio.create_task(
            self._vector_search(concept_name, config, target_key, fetch_size)
        )
        graph_results, vector_results = await asyncio.gather(graph_task, vector_task)

        # RRF 融合
        merged = _rrf_fuse(graph_results, vector_results)

        if not merged:
            return (f"未找到与 {target_key} 相似的 {concept_name} 实例，建议手动创建。", [])

        # LLM 精排
        reranked = await self._llm_rerank(concept_name, config, merged[:topK * 2], target_key, topK)
        return reranked, merged[:topK]

    # ── 配置读取 ──────────────────────────────────────────

    async def _load_vectorization_config(self, concept_name: str) -> Optional[dict]:
        """从 Neo4j Concept 节点读取 vectorization 配置。"""
        if concept_name in self._concept_cache:
            return self._concept_cache[concept_name]

        from app.services.neo4j_service import neo4j_service
        from app.services.ontology_service import ontology_service

        # 优先从 ontology_service 缓存的内存数据读（已有 concepts 列表）
        concepts = ontology_service.get_concepts()
        for c in concepts:
            if c.get("name") == concept_name:
                vec_cfg = c.get("vectorization")
                if vec_cfg:
                    self._concept_cache[concept_name] = vec_cfg
                    return vec_cfg
                break

        # fallback：直接查 Neo4j
        try:
            records = await neo4j_service.execute_read(
                "MATCH (c:Concept {name: $name}) RETURN c.vectorization AS cfg",
                {"name": concept_name}
            )
            if records and records[0].get("cfg"):
                cfg = records[0]["cfg"]
                if isinstance(cfg, str):
                    cfg = json.loads(cfg)
                self._concept_cache[concept_name] = cfg
                return cfg
        except Exception as e:
            log.warning(f"[VectorSearch] 读取 {concept_name} 向量化配置失败: {e}")

        return None

    def invalidate_cache(self):
        """清除配置缓存。"""
        self._concept_cache.clear()

    # ── 参数提取 ──────────────────────────────────────────

    async def _extract_target_from_message(self, concept_name: str, message: str) -> str:
        """从用户消息中提取目标实体的主键值。

        优先匹配已知模式（工单号 MO-xxx、物料编码等），
        兜底用 LLM 提取。
        """
        if not message:
            return ""
        # 常见模式（优先级从高到低）
        patterns = [
            r'(?:MO|WO)[-\s]?\d+(?:[-\s]\d+)*',  # 工单号: MO001, WO-20250521-001
            r'(?:NV|HY|PROD)[-\s]?\d+',            # 产品编码: NV-200, HY200
            r'[A-Z]\d{2,4}[-\s]\d{2,}',            # E34-053, T123-456
            r'[A-Z]{2,4}[-\s]\d{4,}',               # 通用编码: AB-12345
            r'(?<![a-zA-Z0-9])\d{4,8}(?![a-zA-Z0-9])',  # 纯数字ID: 10079（排除ASCII字母数字边界，兼容中文上下文）
        ]
        for pat in patterns:
            m = re.search(pat, message, re.IGNORECASE)
            if m:
                return m.group(0).strip()

        # LLM 兜底
        try:
            from app.services.llm_service import llm_service
            pk = await self._get_primary_key(concept_name)
            prompt = (
                f"从以下消息中提取 {concept_name} 实体的主键({pk})值。"
                f"只返回提取到的值，没有则返回空字符串。\n"
                f"消息: {message}"
            )
            result = await llm_service.chat_sync(prompt, session_id="default", model_name=None)
            return result.strip().strip('"').strip("'")
        except Exception:
            return ""

    async def _resolve_target(self, concept_name: str, target_key: str) -> Optional[str]:
        """跨概念目标解析：用户可能用关联概念的标识指代目标。

        例如：用户说"MO001"（WorkOrder.code）指代 WorkOrderBOM。
        1. 先在目标概念中查找 target_key
        2. 找不到则在 Neo4j 全图中搜索包含该值的节点
        3. 沿关系找回到目标概念
        """
        pk = await self._get_primary_key(concept_name)
        ns = settings.NEO4J_NAMESPACE or ""

        # 1. 目标概念中存在 → 不需要解析
        records = await self._neo4j().execute_read(f"""
            MATCH (n:{concept_name} {{{pk}: $key}})
            WHERE (n._namespace = $ns OR $ns = '')
            RETURN n.{pk} AS pk LIMIT 1
        """, {"key": target_key, "ns": ns})
        if records:
            return target_key  # 已存在，无需解析

        # 2. 全图搜索包含该值的节点
        try:
            search_records = await self._neo4j().execute_read("""
                MATCH (n)
                WHERE (n._namespace = $ns OR $ns = '')
                  AND (toString(n.code) = $key OR toString(n.name) = $key OR toString(n.id) = $key)
                RETURN labels(n) AS labels, n
                LIMIT 5
            """, {"key": target_key, "ns": ns})
        except Exception:
            return target_key  # 搜索失败，保持原值

        if not search_records:
            return target_key  # 找不到任何匹配，保持原值

        # 3. 从找到的节点出发，尝试沿关系达到目标概念
        from app.services.ontology_service import ontology_service
        concepts = ontology_service.get_concepts()
        concept_names = {c.get("name", "") for c in concepts}

        for rec in search_records:
            node_labels = rec.get("labels", [])
            found_concept = None
            for lbl in node_labels:
                if lbl in concept_names:
                    found_concept = lbl
                    break
            if not found_concept:
                continue

            # 3a. 直接就是目标概念？再试一次（可能 key 匹配了其他属性）
            if found_concept == concept_name:
                node = dict(rec.get("n", {}))
                resolved = node.get(pk, "")
                if resolved:
                    return str(resolved)
                continue

            # 3b. 从找到的节点走关系到达目标概念（1跳）
            try:
                path_records = await self._neo4j().execute_read(f"""
                    MATCH (src)-[]->(tgt:{concept_name})
                    WHERE (src._namespace = $ns OR $ns = '')
                      AND (toString(src.code) = $key OR toString(src.name) = $key OR toString(src.id) = $key)
                    RETURN tgt.{pk} AS resolved LIMIT 1
                """, {"key": target_key, "ns": ns})
                if path_records and path_records[0].get("resolved"):
                    return str(path_records[0]["resolved"])
            except Exception:
                pass

            # 3c. 反向关系（目标概念指向找到的节点）
            try:
                rev_records = await self._neo4j().execute_read(f"""
                    MATCH (tgt:{concept_name})-[]->(src)
                    WHERE (src._namespace = $ns OR $ns = '')
                      AND (toString(src.code) = $key OR toString(src.name) = $key OR toString(src.id) = $key)
                    RETURN tgt.{pk} AS resolved LIMIT 1
                """, {"key": target_key, "ns": ns})
                if rev_records and rev_records[0].get("resolved"):
                    return str(rev_records[0]["resolved"])
            except Exception:
                pass

        return target_key  # 无法解析，保持原值

    async def _get_primary_key(self, concept_name: str) -> str:
        """获取概念的主键属性名。"""
        from app.services.ontology_service import ontology_service
        concepts = ontology_service.get_concepts()
        for c in concepts:
            if c.get("name") == concept_name:
                for p in c.get("properties", []):
                    if p.get("isPrimary"):
                        return p.get("name", "name")
        return "name"

    # ── 图结构检索 ────────────────────────────────────────

    async def _graph_search(
        self, concept_name: str, config: dict, target_key: str, topK: int
    ) -> list[dict]:
        """Cypher 多维度图匹配。

        按 config.fingerprint.properties 计算 Jaccard 属性相似度，
        按 config.fingerprint.relationProperties 计算子节点内容匹配。
        """
        from app.services.neo4j_service import neo4j_service

        pk = await self._get_primary_key(concept_name)
        fp = config.get("fingerprint", {})
        fprops = fp.get("properties", [])
        rel_props = fp.get("relationProperties", [])

        namespace = settings.NEO4J_NAMESPACE or ""

        # 1. 获取目标实体（兼容 int/string 类型的主键）
        target_query = f"""
            MATCH (t:{concept_name})
            WHERE (toString(t.{pk}) = $target_key OR t.{pk} = toIntegerOrNull($target_key))
              AND (t._namespace = $ns OR $ns = '')
            RETURN t
        """
        target_records = await neo4j_service.execute_read(target_query, {
            "target_key": target_key, "ns": namespace
        })
        if not target_records:
            return []

        target = target_records[0].get("t", {})
        target_props = {p: str(target.get(p, "") or "") for p in fprops}

        # 2. 查询候选
        candidates = await neo4j_service.execute_read(f"""
            MATCH (n:{concept_name})
            WHERE toString(n.{pk}) <> $target_key
              AND (n._namespace = $ns OR $ns = '')
            RETURN n.{pk} AS pk, n.embedding AS embedding
                {', ' + ', '.join(f'n.{p} AS _{p}' for p in fprops) if fprops else ''}
                {', ' + ', '.join(f'n.{p} AS {p}' for p in fprops if p not in ('pk', 'embedding')) if False else ''}
            LIMIT {_vec_settings.max_candidates}
        """, {"target_key": target_key, "ns": namespace})

        if not candidates:
            return []

        # 3. 如果有关系属性配置，获取子节点内容
        subnode_data = {}
        if rel_props:
            for rp in rel_props:
                rel = rp.get("relation", "")
                rel_label = rp.get("relation", "")  # Neo4j label
                sprops = rp.get("properties", [])
                if rel and sprops:
                    sub_records = await neo4j_service.execute_read(f"""
                        MATCH (n:{concept_name})-[:"{rel}"]->(child:{rel_label})
                        WHERE (n._namespace = $ns OR $ns = '')
                        RETURN n.{pk} AS pk,
                               collect({{ {', '.join(f'{sp}: child.{sp}' for sp in sprops)} }}) AS children
                    """, {"ns": namespace})
                    for sr in sub_records:
                        key = sr.get("pk", "")
                        if key not in subnode_data:
                            subnode_data[key] = {}
                        subnode_data[key][rel] = sr.get("children", [])

        # 4. 计算相似度得分
        scored = []
        for cand in candidates:
            cand_pk = cand.get("pk", "")
            # Jaccard 属性相似
            jaccard = 0.0
            if fprops:
                cand_set = set()
                target_set = set()
                for p in fprops:
                    cv = str(cand.get(p) or cand.get(f"_{p}", "") or "")
                    tv = target_props.get(p, "")
                    if cv:
                        cand_set.add(f"{p}:{cv}")
                    if tv:
                        target_set.add(f"{p}:{tv}")
                union = len(cand_set | target_set)
                if union > 0:
                    jaccard = len(cand_set & target_set) / union

            # 子节点内容相似
            sub_sim = 0.0
            if rel_props and cand_pk in subnode_data:
                target_subs = subnode_data.get(target_key, {})
                cand_subs = subnode_data.get(cand_pk, {})
                for rp in rel_props:
                    rel = rp.get("relation", "")
                    sprops = rp.get("properties", [])
                    t_items = target_subs.get(rel, [])
                    c_items = cand_subs.get(rel, [])
                    if t_items and c_items:
                        t_set = set()
                        c_set = set()
                        for item in t_items:
                            vals = [str(item.get(sp, "")) for sp in sprops]
                            t_set.add("|".join(vals))
                        for item in c_items:
                            vals = [str(item.get(sp, "")) for sp in sprops]
                            c_set.add("|".join(vals))
                        u = len(t_set | c_set)
                        if u > 0:
                            sub_sim += len(t_set & c_set) / u
                if rel_props:
                    sub_sim /= len(rel_props)

            total = _vec_settings.graph_weight * jaccard + (1 - _vec_settings.graph_weight) * sub_sim
            if total > 0:
                scored.append({**cand, "_graph_score": round(total, 4)})

        return sorted(scored, key=lambda x: x.get("_graph_score", 0), reverse=True)[:topK]

    # ── 向量检索 ──────────────────────────────────────────

    async def _vector_search(
        self, concept_name: str, config: dict, target_key: str, topK: int
    ) -> list[dict]:
        """向量语义搜索。

        1. 获取目标实体的指纹文本 → embed
        2. 获取候选的 embedding
        3. 计算 cosine 相似度
        4. 无 embedding 的候选项懒加载生成
        """
        from app.services.neo4j_service import neo4j_service
        from app.core.model_config import create_embedding

        pk = await self._get_primary_key(concept_name)
        namespace = settings.NEO4J_NAMESPACE or ""

        # 1. 获取目标实体的完整数据 + 指纹
        target_data = await self._load_node_data(concept_name, target_key, config)
        if not target_data:
            return []

        target_fp = self._build_fingerprint(target_data, config.get("fingerprint", {}))
        if not target_fp:
            return []

        try:
            emb_obj = await asyncio.to_thread(create_embedding().embed_query, target_fp)
            target_vec = emb_obj if isinstance(emb_obj, list) else []
        except Exception as e:
            log.warning(f"[VectorSearch] 目标嵌入生成失败: {e}")
            return []

        if not target_vec:
            return []

        # 2. 获取所有候选节点（含 embedding 状态）
        pk_col = pk
        candidates = await neo4j_service.execute_read(f"""
            MATCH (n:{concept_name})
            WHERE toString(n.{pk_col}) <> $target_key
              AND (n._namespace = $ns OR $ns = '')
            RETURN n.{pk_col} AS pk, n.embedding AS embedding
        """, {"target_key": target_key, "ns": namespace})

        # 3. 仅用已有 embedding，无索引节点跳过（通过 embed_concepts.py 批量建立）
        with_emb = [n for n in candidates if n.get("embedding")]
        without_emb = len(candidates) - len(with_emb)
        if without_emb > 0:
            log.info(f"[VectorSearch] {concept_name}: {without_emb} 个节点无向量索引，仅用图检索覆盖")

        # 4. 计算余弦相似度
        scored = []
        for node in with_emb:
            try:
                emb_str = node.get("embedding", "")
                if isinstance(emb_str, str):
                    emb = json.loads(emb_str)
                else:
                    emb = emb_str
                if emb:
                    sim = _cosine_similarity(target_vec, emb)
                    if sim > 0:
                        scored.append({**node, "_vector_score": round(sim, 4)})
            except Exception:
                continue

        return sorted(scored, key=lambda x: x.get("_vector_score", 0), reverse=True)[:topK]

    # ── LLM 精排 ──────────────────────────────────────────

    async def _llm_rerank(
        self, concept_name: str, config: dict, candidates: list[dict],
        target_key: str, topK: int,
    ) -> str:
        """LLM 精排：对融合后的候选进行语义比较，输出推荐理由。"""
        if not candidates:
            return "未找到可比较的候选。"

        from app.services.llm_service import llm_service

        # 获取概念中文名
        concept_label = concept_name
        try:
            from app.services.ontology_service import ontology_service
            for c in ontology_service.get_concepts():
                if c.get("name") == concept_name:
                    concept_label = c.get("label", concept_name)
                    break
        except Exception:
            pass

        # 属性中文标签映射
        prop_labels = {}
        try:
            from app.services.ontology_service import ontology_service
            for c in ontology_service.get_concepts():
                if c.get("name") == concept_name:
                    for p in c.get("properties", []):
                        prop_labels[p.get("name", "")] = p.get("label", p.get("name", ""))
                    break
        except Exception:
            pass

        # 构建候选摘要
        fp = config.get("fingerprint", {})
        fprops = fp.get("properties", [])
        lines = [f"用户想找与「{target_key}」最相似的{concept_label}，用于作为模板参考。", ""]
        lines.append("## 候选实例（以下均非目标本身）")
        for i, c in enumerate(candidates[:topK * 2]):
            parts = [f"{i+1}. **{c.get('pk', '?')}**"]
            for p in fprops:
                val = c.get(p, c.get(f"_{p}", ""))
                label = prop_labels.get(p, p)
                if val:
                    parts.append(f"  {label}: {val}")
            if c.get("_graph_score"):
                parts.append(f"  结构相似: {c['_graph_score']:.0%}")
            if c.get("_vector_score"):
                parts.append(f"  语义相似: {c['_vector_score']:.0%}")
            lines.extend(parts)

        prompt = (
            f"{chr(10).join(lines)}\n\n"
            f"你是制造业BOM专家。请从以上候选中推荐最合适的 {topK} 个作为「{target_key}」的BOM模板参考。"
            f"要求：1) 用表格输出排名、ID和推荐理由 2) 说明每个候选的适用性和差异点 "
            f"3) 一句话总结推荐策略（优先同物料系列→同工艺路线→辅材/包材降权）"
        )

        try:
            log.info(f"[VectorSearch] LLM精排: {concept_name} target={target_key} candidates={len(candidates[:topK*2])}")
            result = await llm_service.chat_sync(
                prompt, session_id="default", model_name=None
            )
            if result:
                log.info(f"[VectorSearch] LLM精排完成: {len(result)} chars")
                return result
            else:
                log.warning(f"[VectorSearch] LLM精排返回空, 使用简单格式")
                return self._format_simple_result(candidates[:topK], fprops)
        except Exception as e:
            log.warning(f"[VectorSearch] LLM精排异常: {e}")
            return self._format_simple_result(candidates[:topK], fprops)

    def _format_simple_result(self, candidates: list[dict], fprops: list[str]) -> str:
        """LLM 不可用时的简单格式化（通用，不写死任何概念属性名）。"""
        lines = [f"## 相似匹配结果", ""]
        if not candidates:
            return "\n".join(lines) + "无匹配结果。"
        # 表头：排名 + 实例ID + 各指纹属性 + 得分
        headers = ["排名", "实例ID"] + fprops + ["综合得分"]
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("|" + "|".join(["------"] * len(headers)) + "|")
        for i, c in enumerate(candidates):
            pk = c.get("pk", c.get("name", "?"))
            vals = [str(i + 1), f"**{pk}**"]
            for p in fprops:
                val = str(c.get(p) or c.get(f"_{p}", "") or "")
                vals.append(val[:40] if val else "-")
            score = c.get("_rrf_score") or c.get("_graph_score", 0)
            vals.append(f"{score:.1%}")
            lines.append("| " + " | ".join(vals) + " |")
        return "\n".join(lines)

    # ── 指纹构建 ──────────────────────────────────────────

    def _build_fingerprint(self, node_data: dict, fingerprint_cfg: dict) -> str:
        """按 fingerprint 配置动态构建嵌入文本。

        node_data: 包含节点属性 + 可选 _children（子节点列表）
        """
        parts = []
        fprops = fingerprint_cfg.get("properties", [])
        if fprops:
            prop_texts = []
            for p in fprops:
                val = node_data.get(p, "")
                if val:
                    prop_texts.append(f"{p}: {val}")
            if prop_texts:
                parts.append(" | ".join(prop_texts))

        rel_props = fingerprint_cfg.get("relationProperties", [])
        for rp in rel_props:
            rel = rp.get("relation", "")
            sprops = rp.get("properties", [])
            sep = rp.get("separator", "×")
            children = node_data.get("_children", {}).get(rel, [])
            if children and sprops:
                child_texts = []
                for child in children:
                    vals = []
                    for sp in sprops:
                        v = child.get(sp, "")
                        if v:
                            vals.append(str(v))
                    if vals:
                        child_texts.append(sep.join(vals))
                if child_texts:
                    parts.append(f"{rel}: " + ", ".join(child_texts))

        return "\n".join(parts)

    # ── 数据加载 ──────────────────────────────────────────

    async def _load_node_data(
        self, concept_name: str, pk_val: str, config: dict,
    ) -> Optional[dict]:
        """加载单个节点的完整数据（含关系子节点）。"""
        from app.services.neo4j_service import neo4j_service

        pk = await self._get_primary_key(concept_name)
        namespace = settings.NEO4J_NAMESPACE or ""
        fp = config.get("fingerprint", {})
        fprops = fp.get("properties", [])
        rel_props = fp.get("relationProperties", [])

        # 查询节点自身属性
        records = await neo4j_service.execute_read(f"""
            MATCH (n:{concept_name})
            WHERE (toString(n.{pk}) = $pk_val OR n.{pk} = toIntegerOrNull($pk_val))
              AND (n._namespace = $ns OR $ns = '')
            RETURN n
        """, {"pk_val": pk_val, "ns": namespace})

        if not records:
            return None

        node = dict(records[0].get("n", {}))
        # 过滤内部属性
        for key in list(node.keys()):
            if key.startswith("_"):
                node.pop(key, None)

        # 查询关系子节点
        node["_children"] = {}
        if rel_props:
            for rp in rel_props:
                rel = rp.get("relation", "")
                rel_label = rp.get("relation", "")
                sprops = rp.get("properties", [])
                if rel and sprops:
                    try:
                        child_records = await neo4j_service.execute_read(f"""
                            MATCH (n:{concept_name})-[r:"{rel}"]->(child:{rel_label})
                            WHERE (toString(n.{pk}) = $pk_val OR n.{pk} = toIntegerOrNull($pk_val))
                              AND (n._namespace = $ns OR $ns = '')
                            RETURN child
                        """, {"pk_val": pk_val, "ns": namespace})
                        node["_children"][rel] = [
                            {k: v for k, v in dict(cr.get("child", {})).items()
                             if not k.startswith("_")}
                            for cr in child_records
                        ]
                    except Exception:
                        pass

        return node


# ── 全局单例 ──────────────────────────────────────────────

vector_search_engine = VectorSearchEngine()
