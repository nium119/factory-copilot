"""Pytest fixtures for eval tests.

Note: Neo4j/ontology setup lives in tests/conftest.py
since pytest discovers conftest upward from test files.
"""

import pytest_asyncio

from .runner import eval_runner as _eval_runner


@pytest_asyncio.fixture(autouse=True)
async def reset_executor_cache():
    """Reset executor cache before each test."""
    _eval_runner.invalidate_cache()
    yield
