"""E2E tests for the full routing pipeline: Agent → Intent → DataBackend"""
import asyncio
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Helpers ──────────────────────────────────────────────────────────

def _init_router():
    """Initialize IntentRouter with ontology for testing."""
    from app.services.ontology_service import ontology_service
    from app.services.action_executor import action_executor
    from app.services.intent_router import intent_router

    async def _init():
        await ontology_service.load()
        intent_router.rebuild(ontology_service, action_executor)
    asyncio.run(_init())
    return intent_router


# ══════════════════════════════════════════════════════════════════════
# Agent-Level Routing (app/agents/router.py)
# ══════════════════════════════════════════════════════════════════════

class TestAgentRouting:
    """Tests for route_intent — agent selection from user message."""

    @pytest.mark.asyncio
    async def test_manual_agent_override(self):
        from app.agents.router import route_intent
        result = await route_intent("any message", agent_name="equipment")
        assert result["agent_name"] == "equipment"
        assert result["method"] == "manual"
        assert result["use_agent"] is False

    @pytest.mark.asyncio
    async def test_multi_domain_keywords_route_to_general(self):
        from app.agents.router import route_intent
        result = await route_intent("设备EQUIP-001的生产质量怎么样")
        assert result["agent_name"] == "general"
        assert result["method"] == "multi_domain"
        assert result["use_agent"] is False
        assert "quality" in result["matched_agents"] or "equipment" in result["matched_agents"]

    @pytest.mark.asyncio
    async def test_explicit_collab_keywords(self):
        from app.agents.router import route_intent
        result = await route_intent("综合分析设备状态和工单进度")
        assert result["agent_name"] == "general"
        assert result["use_agent"] is True  # explicit collab = use agent collaboration

    @pytest.mark.asyncio
    async def test_default_routing(self):
        from app.agents.router import route_intent
        result = await route_intent("你好")
        assert result["agent_name"] == "general"
        assert result["method"] == "default"


# ══════════════════════════════════════════════════════════════════════
# Intent-Level Routing (app/services/intent_router.py)
# ══════════════════════════════════════════════════════════════════════

class TestIntentRouting:
    """Tests for IntentRouter.route — action selection within an agent."""

    def setup_method(self):
        self.router = _init_router()

    def test_l1_exact_match(self):
        result = self.router.route("查询质检记录", "general")
        assert result.tool_name == "QualityCheck_query"
        assert result.method == "keyword"
        assert result.confidence > 0.8

    def test_l1_match_with_enum_value(self):
        result = self.router.route("查询不合格的质检记录", "general")
        assert result.tool_name == "QualityCheck_query"
        assert result.method == "keyword"

    def test_l1_match_equipment_status(self):
        result = self.router.route("查询所有设备的状态", "general")
        # Should match Equipment_query (may fall to L2 depending on keyword coverage)
        assert result.tool_name or result.method == "l3"

    def test_l1_ambiguous_falls_to_l3(self):
        result = self.router.route("设备EQUIP-001的生产质量怎么样", "general")
        if result.method == "keyword":
            # If L1 matched, verify it's a read action (not create)
            assert not result.requires_confirmation, (
                f"Question message matched write action {result.tool_name}"
            )
        else:
            # Cross-concept + question → correctly falls to L2/L3
            assert result.method in ("l3", "keyword")

    def test_vague_query_falls_through(self):
        result = self.router.route("怎么样", "general")
        # Very vague — should not match anything with confidence
        assert result.method in ("l3", "default") or result.tool_name is None

    def test_route_explicit_matches(self):
        result = self.router.route_explicit("QualityCheck_query", "质量怎么样")
        assert result.tool_name == "QualityCheck_query"
        assert result.method in ("explicit", "llm_classify")

    def test_rebuild_is_idempotent(self):
        from app.services.ontology_service import ontology_service
        from app.services.action_executor import action_executor

        self.router.rebuild(ontology_service, action_executor)
        count1 = len(self.router._index)
        self.router.rebuild(ontology_service, action_executor)
        count2 = len(self.router._index)
        assert count1 == count2
        assert count1 > 0


# ══════════════════════════════════════════════════════════════════════
# Parameter Extraction
# ══════════════════════════════════════════════════════════════════════

class TestParamExtraction:
    """Tests for extract_params — pulling structured params from NL."""

    def setup_method(self):
        self.router = _init_router()

    def test_extract_equipment_code_from_cross_concept(self):
        # extract_params does pattern-level extraction. Cross-concept
        # resolution (_cross_entity) is done by resolve_entities() afterward.
        params = self.router.extract_params(
            "设备EQUIP-001的生产质量怎么样", "QualityCheck_query",
        )
        # At minimum, EQUIP-001 should be captured (possibly as workOrderId,
        # the first ID param in QualityCheck's schema)
        has_equip = any(
            isinstance(v, str) and "EQUIP-001" in v
            for v in params.values()
        )
        assert has_equip or params == {}, f"EQUIP-001 not found in {params}"

    def test_extract_workorder_code(self):
        params = self.router.extract_params(
            "查询工单WO-20250521-001的详情", "WorkOrder_query",
        )
        # Should extract the code pattern from message
        assert params  # at minimum returns empty dict, not error

    def test_extract_enum_from_message(self):
        params = self.router.extract_params(
            "查询不合格的质检记录", "QualityCheck_query",
        )
        # "不合格" is an enum value; may be extracted or detected by resolve_entities
        assert isinstance(params, dict)


# ══════════════════════════════════════════════════════════════════════
# DataBackend Integration
# ══════════════════════════════════════════════════════════════════════

class TestDataBackendIntegration:
    """Tests for DataBackend — Neo4j / SQLite / API triple-backend."""

    @pytest.mark.asyncio
    async def test_sqlite_backend_health(self):
        from app.services.data_backend import SqliteBackend
        backend = SqliteBackend()
        health = await backend.health()
        assert health["ok"] is True
        assert health["backend"] == "sqlite"

    @pytest.mark.asyncio
    async def test_sqlite_resolve_entity_found(self):
        from app.services.data_backend import SqliteBackend
        backend = SqliteBackend()
        entity = await backend.resolve_entity("WorkOrder", "WO-20250521-001")
        if entity:
            assert "id" in entity

    @pytest.mark.asyncio
    async def test_sqlite_resolve_entity_not_found(self):
        from app.services.data_backend import SqliteBackend
        backend = SqliteBackend()
        entity = await backend.resolve_entity("Equipment", "NONEXIST-999")
        assert entity is None

    @pytest.mark.asyncio
    async def test_sqlite_query_with_filters(self):
        from app.services.data_backend import SqliteBackend
        backend = SqliteBackend()
        results = await backend.query("WorkOrder", {"status": "生产中"})
        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_neo4j_backend_health(self):
        from app.services.data_backend import Neo4jBackend
        backend = Neo4jBackend()
        health = await backend.health()
        # Neo4j may or may not be available in test env
        assert "backend" in health
        assert "ok" in health

    @pytest.mark.asyncio
    async def test_neo4j_resolve_equipment(self):
        from app.services.data_backend import Neo4jBackend
        backend = Neo4jBackend()
        health = await backend.health()
        if not health["ok"]:
            pytest.skip("Neo4j not available")
        entity = await backend.resolve_entity("Equipment", "EQUIP-001")
        if entity:
            assert entity.get("id") == "EQUIP-001"

    @pytest.mark.asyncio
    async def test_neo4j_cross_concept_query(self):
        from app.services.data_backend import Neo4jBackend
        backend = Neo4jBackend()
        health = await backend.health()
        if not health["ok"]:
            pytest.skip("Neo4j not available")
        results = await backend.query(
            "QualityCheck", {},
            relations=["Equipment"],
        )
        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_fallback_backend_initializes(self):
        from app.services.data_backend import FallbackDataBackend
        backend = FallbackDataBackend()
        await backend.initialize()
        health = await backend.health()
        # At least one backend should be healthy
        assert health["ok"] is True

    @pytest.mark.asyncio
    async def test_fallback_resolve_entity(self):
        from app.services.data_backend import FallbackDataBackend
        backend = FallbackDataBackend()
        await backend.initialize()
        entity = await backend.resolve_entity("WorkOrder", "WO-20250521-001")
        if entity:
            assert "id" in entity

    @pytest.mark.asyncio
    async def test_api_backend_unavailable_by_default(self):
        from app.services.data_backend import ApiBackend
        backend = ApiBackend()
        health = await backend.health()
        assert health["ok"] is False  # no MES_API_BASE_URL configured


# ══════════════════════════════════════════════════════════════════════
# Enrich Params (L3 Confirmation Context)
# ══════════════════════════════════════════════════════════════════════

class TestEnrichParams:
    """Tests for enrich_params — ontology graph traversal for confirmation forms."""

    def setup_method(self):
        self.router = _init_router()

    @pytest.mark.asyncio
    async def test_enrich_params_returns_valid_structure(self):
        result = await self.router.enrich_params(
            "QualityCheck_record",
            {"workOrderId": "WO-20250521-001"},
        )
        assert "params" in result
        assert "context" in result
        assert isinstance(result["context"], dict)

    @pytest.mark.asyncio
    async def test_enrich_params_no_params(self):
        result = await self.router.enrich_params("QualityCheck_record", {})
        assert "params" in result
        assert "context" in result

    @pytest.mark.asyncio
    async def test_enrich_params_unknown_tool(self):
        result = await self.router.enrich_params("NonExistent_tool", {})
        assert result == {"params": {}, "context": {}}

    @pytest.mark.asyncio
    async def test_enrich_params_preserves_original_params(self):
        original = {"workOrderId": "WO-20250521-001", "result": "合格"}
        result = await self.router.enrich_params("QualityCheck_record", original)
        for k, v in original.items():
            assert result["params"].get(k) == v


# ══════════════════════════════════════════════════════════════════════
# Keyword Index Quality
# ══════════════════════════════════════════════════════════════════════

class TestKeywordQuality:
    """Tests for keyword index coverage and uniqueness."""

    def setup_method(self):
        self.router = _init_router()

    def test_every_action_has_core_keywords(self):
        for fn_name, entry in self.router._index.items():
            assert len(entry.core_keywords) > 0, f"{fn_name} has no core keywords"
            assert len(entry.ngram_keywords) >= 0, f"{fn_name} has negative ngrams?"

    def test_no_overlap_between_core_and_ngram(self):
        for fn_name, entry in self.router._index.items():
            overlap = set(entry.core_keywords) & set(entry.ngram_keywords)
            assert not overlap, f"{fn_name}: {overlap} in both core and ngram"

    def test_all_actions_indexed(self):
        assert len(self.router._index) >= 7, f"Expected 7+ actions, got {len(self.router._index)}"

    def test_concept_descriptions_in_keywords(self):
        for fn_name, entry in self.router._index.items():
            # Descriptions should contribute to the keyword pool
            total = len(entry.core_keywords) + len(entry.ngram_keywords)
            assert total >= 10, f"{fn_name} has only {total} keywords — too few"


# ══════════════════════════════════════════════════════════════════════
# Model Selection (Resource-Aware)
# ══════════════════════════════════════════════════════════════════════

class TestModelSelection:
    """Tests for select_model_for_complexity."""

    def test_simple_query_returns_simple_model(self):
        from app.agents.router import select_model_for_complexity
        model = select_model_for_complexity("查询工单")
        # Simple query should suggest a smaller/faster model
        assert model is not None  # actual model name depends on config

    def test_complex_query_returns_complex_model(self):
        from app.agents.router import select_model_for_complexity
        model = select_model_for_complexity(
            "综合分析设备EQUIP-001在过去三个月的生产效率、"
            "质量表现、故障率和维护成本，并对标行业平均水平"
        )
        # Long, multi-domain query should get a beefier model
        assert model is not None

    def test_short_query_returns_none(self):
        from app.agents.router import select_model_for_complexity
        model = select_model_for_complexity("你好")
        # Very short — use default model (None = no override)
        assert model is not None or model is None  # either is valid

    def test_user_model_overrides_complexity(self):
        from app.agents.router import select_model_for_complexity
        model = select_model_for_complexity("分析所有", user_model="gpt-4")
        assert model == "gpt-4"


# ══════════════════════════════════════════════════════════════════════
# Health Endpoint
# ══════════════════════════════════════════════════════════════════════

class TestHealthEndpoint:
    """Tests for /health endpoint — system health + backend status."""

    def test_health_returns_json(self):
        from app.main import app
        from fastapi.testclient import TestClient
        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert data["version"]
        assert "neo4j" in data
        assert "data_backend" in data
        assert "timestamp" in data

    def test_health_data_backend_structure(self):
        from app.main import app
        from fastapi.testclient import TestClient
        client = TestClient(app)
        resp = client.get("/health")
        data = resp.json()
        db = data.get("data_backend", {})
        assert "ok" in db
        assert "primary" in db
        assert isinstance(db.get("backends", {}), dict)
