"""Neo4j 管理台组件投影图端点的非集成回归（Issue #36）。

在 Cypher 执行层打桩，覆盖端点契约：鉴权、非法 limit、404、共享实体
内容节点口径（节点数 vs 局部引用数）、截断边界（含恰好达到上限）、
关系按组件归属与可见端点过滤。
"""

import pytest
from fastapi.testclient import TestClient

from app import main
from app.config import settings
from app.neo4j_webui import graph as graph_module


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(settings, "api_password", "test-password")
    return TestClient(main.app)


def _auth():
    return {"X-API-Password": "test-password"}


def _patch_cypher(monkeypatch, *, component, nodes, stats, rels):
    """按查询特征路由到桩数据，未预期查询直接失败。"""
    calls = {"nodes": [], "stats": [], "rels": []}

    def fake_single(query, params=None):
        if "RETURN c.hash_sat AS hash_sat" in query:
            return component
        if "count(DISTINCT e)" in query:
            calls["stats"].append((query, params))
            return stats
        raise AssertionError(f"unexpected single cypher: {query[:60]!r}")

    def fake_multi(query, params=None):
        if "LIMIT $limit" in query:
            calls["nodes"].append((query, params))
            return nodes
        if "TOPOLOGY {component: $h}" in query:
            calls["rels"].append((query, params))
            return rels
        raise AssertionError(f"unexpected cypher: {query[:60]!r}")

    monkeypatch.setattr(graph_module, "_run_single_cypher", fake_single)
    monkeypatch.setattr(graph_module, "_run_cypher", fake_multi)
    return calls


def _node(entity_key, labels, props_json, refs=1):
    return {
        "entity_id": entity_key,
        "entity_key": entity_key,
        "labels": ["V1Entity", *labels],
        "props_json": props_json,
        "reference_count": refs,
    }


HASH = "a" * 64
ROOT = _node("k0", ["Body"], '{"entity_type": "body", "top_level": true}', 1)
FACE = _node(
    "k1", ["Face"],
    '{"entity_type": "face", "geometry": {"type": "plane"}}', 2,
)


def test_entity_graph_requires_admin_password(client):
    r = client.get(f"/api/neo4j/components/{HASH}/entity-graph")
    assert r.status_code == 401


def test_entity_graph_returns_503_without_configured_password(monkeypatch):
    monkeypatch.setattr(settings, "api_password", "")
    client = TestClient(main.app)
    r = client.get(f"/api/neo4j/components/{HASH}/entity-graph")
    assert r.status_code == 503


def test_entity_graph_rejects_invalid_limit(client, monkeypatch):
    # FastAPI 校验先于任何 Neo4j 访问；桩保证一旦触库即失败。
    def fail(query, params=None):
        raise AssertionError("invalid limit must not reach Neo4j")

    monkeypatch.setattr(graph_module, "_run_cypher", fail)
    monkeypatch.setattr(graph_module, "_run_single_cypher", fail)
    assert client.get(
        f"/api/neo4j/components/{HASH}/entity-graph?limit=0",
        headers=_auth(),
    ).status_code == 422
    assert client.get(
        f"/api/neo4j/components/{HASH}/entity-graph?limit={graph_module.MAX_GRAPH_NODES + 1}",
        headers=_auth(),
    ).status_code == 422


def test_entity_graph_404_for_unknown_component(client, monkeypatch):
    _patch_cypher(
        monkeypatch, component=None, nodes=[], stats=None, rels=[]
    )
    r = client.get(f"/api/neo4j/components/{HASH}/entity-graph", headers=_auth())
    assert r.status_code == 404
    assert "not found" in r.json()["detail"]


def test_entity_graph_dedupes_content_nodes_and_reports_references(
    client, monkeypatch
):
    calls = _patch_cypher(
        monkeypatch,
        component={"hash_sat": HASH, "name": "cube"},
        nodes=[ROOT, FACE],
        stats={
            "total_entities": 2,
            "total_entity_references": 3,
            "matched_entities": 2,
        },
        rels=[
            {"id": "e1", "start": "k0", "end": "k1", "rel_type": "body_lump"},
        ],
    )
    r = client.get(f"/api/neo4j/components/{HASH}/entity-graph", headers=_auth())
    assert r.status_code == 200
    data = r.json()
    # 节点身份 = 内容键（entity_key），局部引用数单列，不与节点数混同。
    assert [n["id"] for n in data["nodes"]] == ["k0", "k1"]
    assert data["nodes"][1]["reference_count"] == 2
    assert data["total_entities"] == 2
    assert data["total_entity_references"] == 3
    assert data["returned"] == 2
    assert data["truncated"] is False
    assert data["relationships_truncated"] is False
    assert [(x["start"], x["end"]) for x in data["rels"]] == [("k0", "k1")]
    # 节点查询必须按内容节点聚合去重并优先保留 top-level 根节点。
    nodes_query = calls["nodes"][0][0]
    assert "count(membership) AS reference_count" in nodes_query
    assert "ORDER BY coalesce(e.top_level, false) DESC" in nodes_query
    # 关系查询必须按组件归属过滤并限定可见端点。
    rels_query, rels_params = calls["rels"][0]
    assert "TOPOLOGY {component: $h}" in rels_query
    assert rels_params["h"] == HASH
    assert set(rels_params["visible"]) == {"k0", "k1"}


def test_entity_graph_truncation_boundaries(client, monkeypatch):
    # 恰好达到上限：全部显示，不算截断。
    _patch_cypher(
        monkeypatch,
        component={"hash_sat": HASH, "name": "c"},
        nodes=[ROOT, FACE],
        stats={
            "total_entities": 2,
            "total_entity_references": 2,
            "matched_entities": 2,
        },
        rels=[],
    )
    data = client.get(
        f"/api/neo4j/components/{HASH}/entity-graph?limit=2", headers=_auth()
    ).json()
    assert data["returned"] == 2 and data["truncated"] is False

    # 超过上限：如实报告截断，且截断数 = 匹配数 - 已显示数。
    _patch_cypher(
        monkeypatch,
        component={"hash_sat": HASH, "name": "c"},
        nodes=[ROOT, FACE],
        stats={
            "total_entities": 5,
            "total_entity_references": 5,
            "matched_entities": 5,
        },
        rels=[],
    )
    data = client.get(
        f"/api/neo4j/components/{HASH}/entity-graph?limit=2", headers=_auth()
    ).json()
    assert data["returned"] == 2 and data["truncated"] is True


def test_entity_graph_filters_rels_to_visible_endpoints(client, monkeypatch):
    visible_rels = [
        {"id": str(i), "start": "k0", "end": "k1", "rel_type": "body_lump"}
        for i in range(graph_module.MAX_GRAPH_RELATIONSHIPS)
    ]
    _patch_cypher(
        monkeypatch,
        component={"hash_sat": HASH, "name": "c"},
        nodes=[ROOT, FACE],
        stats={
            "total_entities": 2,
            "total_entity_references": 2,
            "matched_entities": 2,
        },
        # 超上限一行 + 一条端点不可见的关系，都必须被剔除。
        rels=[
            *visible_rels,
            {"id": "x", "start": "k0", "end": "k-missing", "rel_type": "body_lump"},
        ],
    )
    data = client.get(
        f"/api/neo4j/components/{HASH}/entity-graph", headers=_auth()
    ).json()
    assert len(data["rels"]) == graph_module.MAX_GRAPH_RELATIONSHIPS
    assert data["relationships_truncated"] is True
    assert all(r["end"] in {"k0", "k1"} for r in data["rels"])


def test_entity_graph_label_filter_uppercases_needle(client, monkeypatch):
    calls = _patch_cypher(
        monkeypatch,
        component={"hash_sat": HASH, "name": "c"},
        nodes=[FACE],
        stats={
            "total_entities": 2,
            "total_entity_references": 3,
            "matched_entities": 1,
        },
        rels=[],
    )
    data = client.get(
        f"/api/neo4j/components/{HASH}/entity-graph?label=face", headers=_auth()
    ).json()
    assert data["label_filter"] == "face"
    assert calls["nodes"][0][1]["needle"] == "FACE"
    assert calls["stats"][0][1]["needle"] == "FACE"
    assert data["matched_entities"] == 1 and data["truncated"] is False


def test_entity_graph_empty_projection_has_no_visible_rels(client, monkeypatch):
    # 空投影：无可见节点时不应发起关系查询（visible 为空集短路）。
    calls = _patch_cypher(
        monkeypatch,
        component={"hash_sat": HASH, "name": "c"},
        nodes=[],
        stats={
            "total_entities": 0,
            "total_entity_references": 0,
            "matched_entities": 0,
        },
        rels=[],
    )
    data = client.get(
        f"/api/neo4j/components/{HASH}/entity-graph", headers=_auth()
    ).json()
    assert data["nodes"] == [] and data["rels"] == []
    assert data["truncated"] is False
    assert calls["rels"] == []
