# -*- coding: utf-8 -*-
"""阶段 D 单元测试：反馈器（验证 + 轨迹回滚）。"""
import pytest

from app.agents.reflector import Reflector, VerifyResult, reflector


class TestVerifyWriteResult:
    def test_rule_engine_blocks(self):
        r = reflector.verify_write_result({"source": "rule_engine", "actionType": "create", "rowCount": 0})
        assert r.ok is False
        assert r.needs_review is True

    def test_write_zero_rows_fails(self):
        r = reflector.verify_write_result({"source": "neo4j", "actionType": "create", "rowCount": 0})
        assert r.ok is False
        assert r.needs_review is True

    def test_write_positive_rows_passes(self):
        r = reflector.verify_write_result({"source": "neo4j", "actionType": "create", "rowCount": 1})
        assert r.ok is True

    def test_query_always_passes(self):
        # 查询不验证 rowCount（0 行也是正常"无数据"）
        r = reflector.verify_write_result({"source": "neo4j", "actionType": "query", "rowCount": 0})
        assert r.ok is True

    def test_empty_result_passes(self):
        r = reflector.verify_write_result({})
        assert r.ok is True


class TestRollbackEntity:
    @pytest.mark.asyncio
    async def test_rollback_create_deletes_new(self, monkeypatch):
        from app.services import data_backend as _db

        deleted = []

        async def fake_delete(concept, pk_name, pk_val):
            deleted.append(pk_val)
            return True

        monkeypatch.setattr(_db.data_backend, "delete", fake_delete)
        result = await reflector.rollback_entity(
            "WorkOrder", True, [{"code": "WO-NEW"}], pk_name="code")
        assert result["deleted"] == 1
        assert deleted == ["WO-NEW"]

    @pytest.mark.asyncio
    async def test_rollback_delete_restores(self, monkeypatch):
        from app.services import data_backend as _db

        restored = []

        async def fake_create(concept, record):
            restored.append(record)
            return {"id": record.get("code", "")}

        monkeypatch.setattr(_db.data_backend, "create", fake_create)
        result = await reflector.rollback_entity(
            "WorkOrder", False, [{"code": "WO-OLD", "qty": 5}], pk_name="code")
        assert result["restored"] == 1
        assert restored == [{"code": "WO-OLD", "qty": 5}]
