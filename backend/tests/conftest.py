"""Pytest fixtures for eval tests."""

import pytest_asyncio


@pytest_asyncio.fixture(autouse=True)
async def setup_eval():
    """Connect Neo4j, load ontology, and reset cache before each test.

    Disconnects and reconnects Neo4j each time to avoid stale connection pool
    issues across pytest-asyncio event loop boundaries.
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
