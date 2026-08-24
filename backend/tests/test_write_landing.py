"""写操作落点 + 可回滚性判定单元测试。"""
from app.services.multi_system_backend import MultiSystemBackend, SystemConfig


def _make_backend():
    backend = MultiSystemBackend()
    backend._systems = {
        "neo4j": SystemConfig({"name": "neo4j", "type": "neo4j"}),
        "mes": SystemConfig({
            "name": "mes", "type": "api", "baseUrl": "http://mes",
            "endpoints": [
                # 无 reversible 字段（旧配置）→ 默认不可回滚
                {"concept": "WorkOrder", "method": "POST", "path": "/api/workorders"},
                # 显式 reversible + compensation → B 级
                {"concept": "Equipment", "method": "POST", "path": "/api/equipment",
                 "reversible": True,
                 "compensation": {"method": "DELETE", "path": "/api/equipment/{id}"}},
            ],
        }),
    }
    backend._concept_system = {"WorkOrder": "mes", "Equipment": "mes"}
    return backend


def test_neo4j_landing_is_reversible():
    # 无 API 映射的概念 → 落 neo4j，A 级（可快照回滚）
    backend = _make_backend()
    landing = backend.get_write_landing("ProductionLine")
    assert landing["is_api"] is False
    assert landing["reversible"] is True
    assert landing["compensation"] is None


def test_api_without_reversible_is_irreversible():
    # API 概念但 endpoint 无 reversible 字段（旧配置）→ 默认不可回滚，C 级
    backend = _make_backend()
    landing = backend.get_write_landing("WorkOrder")
    assert landing["is_api"] is True
    assert landing["reversible"] is False
    assert landing["compensation"] is None


def test_api_with_compensation_is_reversible():
    # API 概念且 endpoint 声明 reversible + compensation → B 级
    backend = _make_backend()
    landing = backend.get_write_landing("Equipment")
    assert landing["is_api"] is True
    assert landing["reversible"] is True
    assert landing["compensation"] == {"method": "DELETE", "path": "/api/equipment/{id}"}


def test_unknown_concept_falls_back_to_neo4j():
    # 未映射概念 → 落 neo4j（保守回退），A 级
    backend = _make_backend()
    landing = backend.get_write_landing("SomeUnknownConcept")
    assert landing["is_api"] is False
    assert landing["reversible"] is True
