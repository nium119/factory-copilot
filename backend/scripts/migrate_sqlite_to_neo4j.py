"""Migrate MES demo data from SQLite to Neo4j.

Run once after Neo4j is available to populate instance nodes and relationships.

Usage:
    cd backend
    python scripts/migrate_sqlite_to_neo4j.py
"""

import asyncio
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.config import settings
from app.services.neo4j_service import Neo4jService

CONCEPT_TABLES = {
    "WorkOrder": "work_orders",
    "Product": "products",
    "QualityCheck": "quality_checks",
    "Equipment": "equipment",
    "Material": "materials",
    "Routing": "routings",
    "WorkCenter": "work_centers",
    "Operation": "operations",
}

# FK relationships inferred from ontology relations
RELATIONSHIPS = [
    ("QualityCheck", "work_order_id", "WorkOrder", "关联工单"),
    ("Operation", "routing_id", "Routing", "属于工艺路线"),
    ("Operation", "work_center_id", "WorkCenter", "分配到工作中心"),
]


async def migrate(db_path: str, neo4j: Neo4jService):
    conn = sqlite3.connect(db_path)

    for concept_name, table_name in CONCEPT_TABLES.items():
        try:
            rows = conn.execute(f"SELECT * FROM {table_name}").fetchall()
            cols = [
                d[1]
                for d in conn.execute(
                    f"PRAGMA table_info({table_name})",
                ).fetchall()
            ]
        except sqlite3.OperationalError:
            print(f"  SKIP: table {table_name} not found")
            continue

        for row in rows:
            props = dict(zip(cols, row))
            props = {k: v for k, v in props.items() if v is not None}

            await neo4j.execute_write(
                f"MERGE (n:{concept_name} {{id: $id}}) SET n += $props",
                {"id": props["id"], "props": props},
            )

        print(f"  {concept_name}: {len(rows)} nodes created")

    # Create relationships
    for from_concept, fk_col, to_concept, rel_label in RELATIONSHIPS:
        cypher = f"""
        MATCH (a:{from_concept})
        MATCH (b:{to_concept} {{id: a.{fk_col}}})
        MERGE (a)-[:{rel_label}]->(b)
        """
        result = await neo4j.execute_write(cypher)
        rels_created = (
            result[0].get("rels_created", 0)
            if result and isinstance(result, list)
            else 0
        )
        print(
            f"  {from_concept}-[:{rel_label}]->{to_concept}: relationships created",
        )

    conn.close()
    print("\nMigration complete.")


if __name__ == "__main__":
    db_path = os.path.join(
        os.path.dirname(__file__), "..", "data", "mes_demo.db",
    )

    neo4j_svc = Neo4jService()

    async def main():
        print(f"Connecting to {settings.NEO4J_URI}...")
        ok = await neo4j_svc.connect()
        if not ok:
            print("ERROR: Cannot connect to Neo4j. Is it running?")
            print(f"  URI: {settings.NEO4J_URI}")
            return

        print(f"Migrating SQLite data from {db_path}...")
        await migrate(db_path, neo4j_svc)

        await neo4j_svc.disconnect()

    asyncio.run(main())
