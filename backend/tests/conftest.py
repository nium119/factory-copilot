"""Pytest fixtures for eval tests."""

import pytest_asyncio


@pytest_asyncio.fixture(scope="session", autouse=True, loop_scope="session")
async def setup_session():
    """连接 Neo4j + 加载本体：整个测试会话只做一次（快，不再每测试重连重载）。

    原按每个测试 disconnect+reconnect+reload（为避开 pytest-asyncio 事件循环边界的
    连接池失效），代价是 17 个测试 × 每次重载本体（59 概念/82 动作）累计极慢、易超时。
    neo4j_service._get_sys_cfg 已改同步 sqlite3（不依赖 AsyncEngine 跨循环），
    读锁也按当前事件循环重建，故连接/加载可安全复用。
    """
    from app.services.ontology_service import ontology_service
    from app.services.neo4j_service import neo4j_service

    await neo4j_service.disconnect()
    ok = await neo4j_service.connect()
    if not ok:
        import pytest
        pytest.skip("Neo4j not available")

    if not ontology_service.loaded:
        ok = await ontology_service.load()
        if not ok:
            import pytest
            pytest.skip("Ontology not available")

    from tests.eval.runner import eval_runner
    eval_runner.invalidate_cache()
    yield
    await neo4j_service.disconnect()


@pytest_asyncio.fixture(autouse=True)
async def reset_eval_per_test(setup_session):
    """每个测试前清缓存，保证测试间隔离（session 级只复用连接/本体，状态不共享）。"""
    from tests.eval.runner import eval_runner
    eval_runner.invalidate_cache()
    yield
