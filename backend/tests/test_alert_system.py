"""告警系统端到端测试 — 补充测试数据并验证各组件。

运行: cd backend && python tests/test_alert_system.py
"""
import asyncio
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Ensure backend is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.chdir(str(Path(__file__).resolve().parent.parent))


# ── 1. 补充测试数据 ────────────────────────────────────────────────────────

def _seed_neo4j():
    """将触发告警的测试数据同步到 Neo4j。

    注意：必须在已有 event loop 的上下文中调用，不能使用 asyncio.run()。
    """
    import asyncio as _aio

    async def _run():
        from app.services.neo4j_service import neo4j_service
        if not neo4j_service.connected:
            try:
                ok = await neo4j_service.connect()
                if not ok:
                    print("  Neo4j 不可用，跳过同步")
                    return
            except Exception:
                print("  Neo4j 不可用，跳过同步")
                return

        # 更新 Material 节点添加 safetyStock
        await neo4j_service.execute_write(
            "MATCH (n:Material {id: 'MAT-001'}) SET n.safetyStock = 200, n.stock = 150"
        )
        await neo4j_service.execute_write(
            "MATCH (n:Material {id: 'MAT-002'}) SET n.safetyStock = 500, n.stock = 80"
        )
        await neo4j_service.execute_write("""
            MERGE (n:Material {id: 'MAT-004'})
            SET n.name = '精密弹簧', n.type = '零部件', n.stock = 30,
                n.unit = '个', n.safetyStock = 200
        """)

        from datetime import datetime, timedelta
        old_date = (datetime.now() - timedelta(days=250)).strftime("%Y-%m-%d")

        await neo4j_service.execute_write(
            "MATCH (n:Equipment {id: 'EQUIP-001'}) SET n.oee = 65"
        )
        await neo4j_service.execute_write(
            "MATCH (n:Equipment {id: 'EQUIP-003'}) SET n.oee = 72"
        )
        await neo4j_service.execute_write(f"""
            MERGE (n:Equipment {{id: 'EQUIP-006'}})
            SET n.name = '老旧冲压机', n.status = '运行',
                n.lastMaintenance = '{old_date}', n.power_kw = 40, n.oee = 78
        """)

        await neo4j_service.execute_write(
            "MATCH (n:QualityCheck {id: 'QC-002'}) SET n.defectRate = 5.2"
        )
        await neo4j_service.execute_write(
            "MATCH (n:QualityCheck {id: 'QC-003'}) SET n.defectRate = 4.8"
        )

        print("  Neo4j 测试数据已同步")

    return _run()


async def seed_test_data():
    """向 mes_demo.db 补充能触发告警的测试数据。"""
    db_path = "data/mes_demo.db"
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # 1.1 给 materials 表添加 safetyStock 列（如果不存在）
    try:
        cur.execute("ALTER TABLE materials ADD COLUMN safetyStock REAL DEFAULT 100")
    except sqlite3.OperationalError:
        pass  # 列已存在

    # 1.2 更新物料库存数据，造一个库存不足的
    cur.execute("UPDATE materials SET safetyStock = 200 WHERE id = 'MAT-001'")
    cur.execute("UPDATE materials SET stock = 150 WHERE id = 'MAT-001'")
    cur.execute("UPDATE materials SET safetyStock = 500 WHERE id = 'MAT-002'")
    cur.execute("UPDATE materials SET stock = 80 WHERE id = 'MAT-002'")

    # 1.3 添加更多物料（触发 stock < safetyStock）
    cur.execute("""
        INSERT OR REPLACE INTO materials (id, name, type, stock, unit, safetyStock)
        VALUES ('MAT-004', '精密弹簧', '零部件', 30, '个', 200)
    """)

    # 1.4 给 equipment 表添加 oee 列
    try:
        cur.execute("ALTER TABLE equipment ADD COLUMN oee REAL DEFAULT 85")
    except sqlite3.OperationalError:
        pass

    # 1.5 更新设备 OEE（触发 OEE 偏低告警）
    cur.execute("UPDATE equipment SET oee = 65 WHERE id = 'EQUIP-001'")
    cur.execute("UPDATE equipment SET oee = 72 WHERE id = 'EQUIP-003'")

    # 1.6 添加维护超期的设备（>180天）
    old_date = (datetime.now() - timedelta(days=250)).strftime("%Y-%m-%d")
    cur.execute("""
        INSERT OR REPLACE INTO equipment (id, name, status, last_maintenance, power_kw, oee)
        VALUES ('EQUIP-006', '老旧冲压机', '运行', ?, 40, 78)
    """, (old_date,))

    # 1.7 给 quality_checks 表添加 defectRate 列
    try:
        cur.execute("ALTER TABLE quality_checks ADD COLUMN defectRate REAL DEFAULT 1.5")
    except sqlite3.OperationalError:
        pass

    # 1.8 添加高缺陷率的质检记录
    cur.execute("UPDATE quality_checks SET defectRate = 5.2 WHERE id = 'QC-002'")
    cur.execute("UPDATE quality_checks SET defectRate = 4.8 WHERE id = 'QC-003'")

    conn.commit()

    # 验证数据
    print("=== 补充后的测试数据 ===")
    print("\n📦 Materials (触发 stock_low):")
    for r in cur.execute("SELECT id, name, stock, safetyStock FROM materials WHERE stock < safetyStock"):
        print(f"  {r[1]} (stock={r[2]}, safety={r[3]})")

    print("\n🔧 Equipment (触发 OEE 偏低):")
    for r in cur.execute("SELECT id, name, oee FROM equipment WHERE oee < 80"):
        print(f"  {r[1]} (OEE={r[2]}%)")

    print("\n📅 Equipment (触发 maintenance_alert):")
    for r in cur.execute("SELECT id, name, last_maintenance FROM equipment"):
        try:
            d = datetime.strptime(r[2], "%Y-%m-%d")
            days_ago = (datetime.now() - d).days
            if days_ago > 180:
                print(f"  {r[1]} (上次维护: {r[2]}, {days_ago}天前)")
        except Exception:
            pass

    print("\n📊 QualityCheck (触发 defectRate):")
    for r in cur.execute("SELECT id, work_order_id, result, defectRate FROM quality_checks WHERE defectRate > 3.0"):
        print(f"  {r[0]} (WO={r[1]}, result={r[2]}, defectRate={r[3]}%)")

    conn.close()
    print("\n✅ SQLite 测试数据补充完成")

    # 1.9 同步测试数据到 Neo4j
    await _seed_neo4j()
    print("✅ Neo4j 测试数据同步完成\n")


# ── 2. 测试 ExplorerService ────────────────────────────────────────────────

async def test_explorer_service():
    """测试 ExplorerService（使用 SQLite 后端）。"""
    from app.services.data_backend import data_backend

    print("=" * 60)
    print("🧪 测试 ExplorerService")
    print("=" * 60)

    await data_backend.initialize()
    health = await data_backend.health()
    print(f"DataBackend: {health['primary']}, OK={health['ok']}")
    for name, h in health.get("backends", {}).items():
        print(f"  {name}: OK={h['ok']}")

    from app.services.explorer_service import explorer_service

    result = await explorer_service.analyze(hours=24)
    print(f"\n📋 分析结果: {result['anomaly_count']} 项异常")
    print(f"摘要: {result['summary']}")

    for i, a in enumerate(result["anomalies"], 1):
        icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(a["severity"], "⚪")
        print(f"\n  {i}. {icon} [{a['severity']}] {a['title']}")
        print(f"     来源: {a['source']}")
        print(f"     描述: {a['description']}")
        print(f"     建议: {a['suggestion']}")

    return result


# ── 3. 测试 RuleTriggerScanner + AlertRepository ───────────────────────────

async def test_rule_trigger_scanner():
    """测试 RuleTriggerScanner 是否正常扫描并创建告警。"""
    from app.services.explorer_service import RuleTriggerScanner

    print("\n" + "=" * 60)
    print("🧪 测试 RuleTriggerScanner")
    print("=" * 60)

    scanner = RuleTriggerScanner()
    anomalies = await scanner.analyze(hours=24)
    print(f"发现 {len(anomalies)} 项 trigger 告警")
    for a in anomalies:
        print(f"  🔔 [{a['severity']}] {a['title']}")
        print(f"     agents: {a.get('agents', [])}")
        print(f"     desc: {a['description']}")

    return anomalies


# ── 4. 测试 AlertRepository ────────────────────────────────────────────────

async def test_alert_repository():
    """测试 AlertRepository 的 CRUD 操作。"""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.core.startup import DB_PATH
    from app.repositories.alert_repository import AlertRepository

    print("\n" + "=" * 60)
    print("🧪 测试 AlertRepository")
    print("=" * 60)

    engine = create_async_engine(f"sqlite+aiosqlite:///{DB_PATH}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with session_factory() as db:
            repo = AlertRepository(db)

            # 4.1 列出活跃告警
            active = await repo.list_active(limit=10)
            print(f"活跃告警: {len(active)} 条")
            for a in active[:5]:
                agents = json.loads(a.agents or "[]")
                print(f"  [{a.severity}] {a.rule_label} ({a.entity_id}) → agents={agents}")

            if active:
                # 4.2 确认第一个
                first = active[0]
                acked = await repo.acknowledge(first.id)
                print(f"\n✅ 已确认: {acked.id} → status={acked.status}")

                # 4.3 解决第一个
                resolved = await repo.resolve(first.id)
                print(f"✅ 已解决: {resolved.id} → status={resolved.status}")

                # 4.4 去重测试
                exists = await repo.exists(first.rule_name, first.entity_id)
                print(f"去重检查 (rule={first.rule_name}, entity={first.entity_id}): exists={exists}")

            # 4.5 升级测试
            escalated = await repo.escalate_stale(hours=0)  # escalate all
            print(f"升级过期告警: {escalated} 条")
    finally:
        await engine.dispose()


# ── 5. 测试告警 API（本地调用）─────────────────────────────────────────────

async def test_alerts_api():
    """通过直接调用内部函数测试告警 API 逻辑。"""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.core.startup import DB_PATH
    from app.repositories.alert_repository import AlertRepository

    print("\n" + "=" * 60)
    print("🧪 测试告警 API 逻辑")
    print("=" * 60)

    engine = create_async_engine(f"sqlite+aiosqlite:///{DB_PATH}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with session_factory() as db:
            repo = AlertRepository(db)

            # GET /api/alerts
            active = await repo.list_active(limit=50)
            high = sum(1 for a in active if a.severity == "high")
            print(f"GET /alerts → {len(active)} 活跃告警")
            print(f"GET /alerts/count → total={len(active)}, high={high}")

            # 按 agent 过滤
            for agent in ["andon", "equipment", "monitor"]:
                filtered = await repo.list_active(limit=50, agent_name=agent)
                if filtered:
                    print(f"GET /alerts?agent_name={agent} → {len(filtered)} 条")

            # 确保至少有一条活跃告警用于 ack/resolve 测试
            if not active:
                # 手动创建一条测试告警
                await repo.create({
                    "rule_name": "test_rule",
                    "rule_label": "测试告警",
                    "concept_name": "Equipment",
                    "entity_id": "EQUIP-006",
                    "severity": "high",
                    "agents": ["equipment"],
                    "trigger_condition": "days_since(lastMaintenance) > 180",
                    "description": "老旧冲压机已250天未维护",
                })
                active = await repo.list_active(limit=50)
                print(f"创建测试告警 → {len(active)} 条活跃")

            if active:
                a = active[0]
                print(f"\nPOST /alerts/{a.id}/acknowledge → ", end="")
                acked = await repo.acknowledge(a.id)
                print(f"status={acked.status}")

                print(f"POST /alerts/{a.id}/resolve → ", end="")
                resolved = await repo.resolve(a.id)
                print(f"status={resolved.status}")

                # 验证状态变更
                after = await repo.list_active(limit=50)
                print(f"操作后活跃告警: {len(after)} 条")
    finally:
        await engine.dispose()


# ── 6. 测试 MonitorScheduler ──────────────────────────────────────────────

async def test_monitor_scheduler():
    """测试 MonitorScheduler 单次扫描。"""
    from app.services.monitor_scheduler import monitor_scheduler

    print("\n" + "=" * 60)
    print("🧪 测试 MonitorScheduler 单次扫描")
    print("=" * 60)

    # 直接调用 _scan（不等 30s 延迟）
    start = datetime.now()
    await monitor_scheduler._scan()
    elapsed = (datetime.now() - start).total_seconds()
    print(f"扫描耗时: {elapsed:.1f}s")
    print("✅ MonitorScheduler 扫描正常")


# ── 7. 需要 Neo4j 的后端测试 ───────────────────────────────────────────

async def test_with_neo4j():
    """使用 Neo4j 后端测试（如果可用）。"""
    from app.services.data_backend import data_backend
    from app.services.explorer_service import explorer_service

    print("\n" + "=" * 60)
    print("🧪 Neo4j 后端测试（如果可用）")
    print("=" * 60)

    await data_backend.initialize()
    health = await data_backend.health()

    if health.get("backends", {}).get("neo4j", {}).get("ok"):
        # 检查 Neo4j 中是否有数据
        from app.services.neo4j_service import neo4j_service
        count = await neo4j_service.execute_read("MATCH (n) RETURN count(n) AS cnt", {})
        total = count[0]["cnt"] if count else 0
        print(f"Neo4j 节点总数: {total}")

        if total > 0:
            result = await explorer_service.analyze(hours=24)
            print(f"Neo4j 分析结果: {result['anomaly_count']} 项异常")
        else:
            print("Neo4j 中无数据（通过 Sqlite fallback 测试已覆盖）")
    else:
        print("Neo4j 不可用，跳过")


# ── 主入口 ─────────────────────────────────────────────────────────────────

async def main():
    print("╔══════════════════════════════════════════════════╗")
    print("║      🔔 告警系统端到端测试                         ║")
    print("╚══════════════════════════════════════════════════╝\n")

    # Step 0: 确保数据库表存在
    from app.core.startup import ensure_database
    await ensure_database()
    print("✅ 数据库表已就绪\n")

    # Step 1: 补充测试数据
    await seed_test_data()

    # Step 2-6: 逐项测试
    try:
        await test_explorer_service()
    except Exception as e:
        print(f"❌ ExplorerService 测试失败: {e}")
        import traceback; traceback.print_exc()

    try:
        await test_rule_trigger_scanner()
    except Exception as e:
        print(f"❌ RuleTriggerScanner 测试失败: {e}")
        import traceback; traceback.print_exc()

    try:
        await test_alert_repository()
    except Exception as e:
        print(f"❌ AlertRepository 测试失败: {e}")
        import traceback; traceback.print_exc()

    try:
        await test_alerts_api()
    except Exception as e:
        print(f"❌ Alerts API 测试失败: {e}")
        import traceback; traceback.print_exc()

    try:
        await test_monitor_scheduler()
    except Exception as e:
        print(f"❌ MonitorScheduler 测试失败: {e}")
        import traceback; traceback.print_exc()

    try:
        await test_with_neo4j()
    except Exception as e:
        print(f"❌ Neo4j 测试失败: {e}")

    print("\n" + "=" * 60)
    print("✅ 告警系统测试完成")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
