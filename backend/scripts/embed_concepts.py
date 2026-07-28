"""批量生成概念向量嵌入。

扫描所有 vectorization.enabled 概念的节点，
按各概念的 fingerprint 配置生成语义嵌入，写入 Neo4j 节点。

用法:  cd backend && python scripts/embed_concepts.py [--concept WorkOrderBOM]
       --concept 不指定则处理所有已启用概念
"""

import asyncio
import json
import sys
import os
from pathlib import Path

# 确保项目路径可导入
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings
from app.core.logger import log
from app.services.neo4j_service import neo4j_service
from app.services.ontology_service import ontology_service
from app.services.vector_search_engine import vector_search_engine
from app.core.model_config import create_embedding


async def main(concept_filter: str = None):
    """主流程。"""
    # 1. 确保 Neo4j 连接
    if not neo4j_service.connected:
        log.info("正在连接 Neo4j...")
        ok = await neo4j_service.connect()
        if not ok:
            log.error("Neo4j 连接失败，退出")
            return

    namespace = settings.NEO4J_NAMESPACE or ""

    # 2. 获取所有 vectorization.enabled 的概念
    concepts = ontology_service.get_concepts()
    if not concepts:
        # 触发加载
        await ontology_service.load()
        concepts = ontology_service.get_concepts()

    enabled = []
    for c in concepts:
        name = c.get("name", "")
        if concept_filter and name != concept_filter:
            continue
        vec_cfg = c.get("vectorization")
        if isinstance(vec_cfg, str):
            try:
                vec_cfg = json.loads(vec_cfg)
            except Exception:
                continue
        if vec_cfg and vec_cfg.get("enabled"):
            enabled.append((name, c, vec_cfg))

    if not enabled:
        log.info("没有启用向量化的概念")
        return

    log.info(f"找到 {len(enabled)} 个启用向量化的概念")

    # 3. 逐个概念处理
    for concept_name, concept, vec_cfg in enabled:
        await embed_concept(concept_name, concept, vec_cfg, namespace)

    log.info("全部完成")


async def embed_concept(concept_name: str, concept: dict, vec_cfg: dict, namespace: str):
    """为单个概念生成嵌入。"""
    pk = next(
        (p.get("name", "name") for p in concept.get("properties", [])
         if p.get("isPrimary")), "name"
    )
    fingerprint_cfg = vec_cfg.get("fingerprint", {})

    # 查询无 embedding 的节点
    if namespace:
        records = await neo4j_service.execute_read(f"""
            MATCH (n:{concept_name})
            WHERE n.embedding IS NULL
              AND n._namespace = $ns
            RETURN n.{pk} AS pk
        """, {"ns": namespace})
    else:
        records = await neo4j_service.execute_read(f"""
            MATCH (n:{concept_name})
            WHERE n.embedding IS NULL
            RETURN n.{pk} AS pk
        """)

    if not records:
        log.info(f"  {concept_name}: 所有节点已索引")
        return

    total = len(records)
    log.info(f"  {concept_name}: {total} 个待索引")

    emb_obj = create_embedding()
    done = 0
    for i, rec in enumerate(records):
        pk_val = rec.get("pk", "")
        if not pk_val:
            continue

        try:
            # 读取完整节点数据
            node_data = await vector_search_engine._load_node_data(
                concept_name, pk_val, vec_cfg
            )
            if not node_data:
                continue

            # 生成指纹文本
            fp_text = vector_search_engine._build_fingerprint(node_data, fingerprint_cfg)
            if not fp_text:
                continue

            # 生成嵌入
            vec = await asyncio.to_thread(emb_obj.embed_query, fp_text)
            vec_list = vec if isinstance(vec, list) else []
            if not vec_list:
                continue

            # 写入
            emb_json = json.dumps(vec_list, ensure_ascii=False)
            await neo4j_service.execute_write(f"""
                MATCH (n:{concept_name} {{{pk}: $pk_val}})
                SET n.embedding = $embedding
            """, {"pk_val": pk_val, "embedding": emb_json})

            done += 1
            if (i + 1) % 50 == 0:
                log.info(f"    {concept_name}: {done}/{total}")

        except Exception as e:
            log.warning(f"    {concept_name}/{pk_val} 失败: {e}")
            continue

    log.info(f"    {concept_name}: 完成 {done}/{total}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="生成概念向量嵌入")
    parser.add_argument("--concept", type=str, default=None, help="只处理指定概念")
    args = parser.parse_args()
    asyncio.run(main(concept_filter=args.concept))
