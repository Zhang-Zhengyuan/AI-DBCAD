"""Neo4j 管理台投影图端点的真库集成回归（Issue #36）。

前提与清理同 test_v1_projection_neo4j.py（连不上自动跳过）。验证
内容寻址共享下的端点口径：组件内重复实体去重、节点数与局部引用数
分开统计、TOPOLOGY 只保留当前组件（共享实体不引入其他组件关系）、
截断边界（含恰好达到上限）与 top-level 根节点优先保留。
"""

import secrets
import uuid

import pytest
from fastapi.testclient import TestClient

from app.config import settings

pytestmark = pytest.mark.integration

_SALT = secrets.token_hex(8)
_ROOT_SALT_B = secrets.token_hex(8)


def _client_or_skip(monkeypatch):
    if not settings.neo4j_uri:
        pytest.skip("未配置 CAD_DB_NEO4J_URI")
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
    )
    try:
        driver.verify_connectivity()
    except Exception as exc:  # noqa: BLE001
        driver.close()
        pytest.skip(f"Neo4j 不可用: {exc}")

    from app import main
    from app.neo4j_webui import support

    monkeypatch.setattr(settings, "api_password", "test-password")
    client = TestClient(main.app)
    return client, driver, support


def _graph(root_salt: str) -> dict:
    """root + 两个同内容 plane face（组件内重复）+ 一个 sphere face。"""
    return {
        "schema": "dbcad.entity_graph.v2",
        "serializer_build": "webui-limit-test",
        "nodes": [
            {
                "id": "temp-root",
                "labels": ["body"],
                "props": {
                    "entity_type": "body",
                    "top_level": True,
                    "fallback": "fine_grained",
                    "probe_salt": root_salt,
                },
            },
            {
                "id": "n1",
                "labels": ["face"],
                "props": {
                    "entity_type": "face",
                    "sense": 1,
                    "geometry": {"type": "plane", "normal": [0.0, 0.0, 1.0]},
                    "probe_salt": _SALT,
                },
            },
            {
                "id": "n1b",
                "labels": ["face"],
                "props": {
                    "entity_type": "face",
                    "sense": 1,
                    "geometry": {"type": "plane", "normal": [0.0, 0.0, 1.0]},
                    "probe_salt": _SALT,
                },
            },
            {
                "id": "n2",
                "labels": ["face"],
                "props": {
                    "entity_type": "face",
                    "sense": 0,
                    "geometry": {"type": "sphere", "radius": 4.0},
                    "probe_salt": _SALT,
                },
            },
        ],
        "rels": [
            {"type": "body_lump", "start": "temp-root", "end": "n1"},
            {"type": "body_lump", "start": "temp-root", "end": "n1b"},
            {"type": "face_loop", "start": "n1", "end": "n2"},
        ],
    }


def _cleanup(store, project_id: str, *component_hashes: str) -> None:
    with store._driver.session(database=store._database) as session:
        session.run(
            "MATCH (v:V1Version {project_id: $pid}) DETACH DELETE v", pid=project_id
        ).consume()
        for component_hash in component_hashes:
            session.run(
                "MATCH (c:V1Component {hash_sat: $h})"
                " WHERE NOT ()-[:CONTAINS]->(c) DETACH DELETE c",
                h=component_hash,
            ).consume()
        session.run(
            "MATCH (e:V1Entity) WHERE e.props_json CONTAINS $salt"
            " AND NOT EXISTS { MATCH ()-[:HAS_ENTITY]->(e) } DETACH DELETE e",
            salt=_SALT,
        ).consume()
        session.run(
            "MATCH (p:V1Project {id: $pid}) DETACH DELETE p", pid=project_id
        ).consume()


def test_webui_entity_graph_content_node_identity_and_component_scoped_rels(
    monkeypatch,
):
    client, driver, support = _client_or_skip(monkeypatch)
    from app.v1_store import Neo4jV1Store, SceneComponent

    store = Neo4jV1Store(driver, settings.neo4j_database)
    store.initialize()
    hash_a = "w36a" + "a" * 59
    hash_b = "w36b" + "b" * 59
    project = store.create_project(f"w36-{uuid.uuid4().hex[:8]}")
    headers = {"X-API-Password": "test-password"}
    try:
        store.commit_version(
            project.id,
            message="a",
            author="test",
            components=[SceneComponent(hash_sat=hash_a, hash_glb="glb-a")],
        )
        store.commit_version(
            project.id,
            message="b",
            author="test",
            components=[SceneComponent(hash_sat=hash_b, hash_glb="glb-b")],
        )
        assert store.write_entity_projection(hash_a, _graph(_SALT))["written"]
        # 组件 B：共享 plane/sphere face 内容节点，root 内容不同（盐不同）。
        graph_b = _graph(_ROOT_SALT_B)
        graph_b["rels"] = [
            {"type": "body_lump", "start": "temp-root", "end": "n1"},
            {"type": "face_loop", "start": "n1", "end": "n2"},
        ]
        assert store.write_entity_projection(hash_b, graph_b)["written"]

        def fetch(hash_sat, **params):
            query = "&".join(f"{k}={v}" for k, v in params.items())
            suffix = f"?{query}" if query else ""
            r = client.get(
                f"/api/neo4j/components/{hash_sat}/entity-graph{suffix}",
                headers=headers,
            )
            assert r.status_code == 200, r.text
            return r.json()

        # A：4 条 HAS_ENTITY 引用收敛为 3 个内容节点；重复 plane face 去重。
        data = fetch(hash_a, limit=10)
        assert data["total_entities"] == 3
        assert data["total_entity_references"] == 4
        assert data["returned"] == 3 and data["truncated"] is False
        faces = [n for n in data["nodes"] if "Face" in n["labels"]]
        assert len(faces) == 2  # plane + sphere，两个局部 plane 引用合一
        plane = next(
            n for n in faces if n["geometry_type"] == "plane"
        )
        assert plane["reference_count"] == 2
        # 关系只含组件 A 的 TOPOLOGY：同 (rel_type, component) 的重复局部
        # 引用合并，共 body_lump×1 + face_loop×1 = 2 条。
        assert sorted(r["type"] for r in data["rels"]) == [
            "body_lump", "face_loop",
        ]
        assert all(r["start"] in {n["id"] for n in data["nodes"]}
                   and r["end"] in {n["id"] for n in data["nodes"]}
                   for r in data["rels"])
        # 共享内容节点上不存在 B 的 TOPOLOGY 边（端点键相同也不会混入）。
        b_types = {r["type"] for r in fetch(hash_b, limit=10)["rels"]}
        assert b_types == {"body_lump", "face_loop"}

        # 恰好达到上限：不算截断；超限：如实截断且 root 优先保留。
        exact = fetch(hash_a, limit=3)
        assert exact["truncated"] is False and exact["returned"] == 3
        cut = fetch(hash_a, limit=2)
        assert cut["truncated"] is True and cut["returned"] == 2
        assert cut["nodes"][0]["top_level"] is True
        assert "Body" in cut["nodes"][0]["labels"]

        # label 过滤：只统计匹配的内容节点，关系端点随可见集收缩。
        filtered = fetch(hash_a, label="face", limit=10)
        assert filtered["matched_entities"] == 2
        assert all("Face" in n["labels"] for n in filtered["nodes"])
        # body→face 边因 root 不可见被剔除，face→face 的 face_loop 保留。
        assert [r["type"] for r in filtered["rels"]] == ["face_loop"]

        # 非法 limit 与未知组件。
        assert client.get(
            f"/api/neo4j/components/{hash_a}/entity-graph?limit=0",
            headers=headers,
        ).status_code == 422
        assert client.get(
            f"/api/neo4j/components/{'z' * 64}/entity-graph",
            headers=headers,
        ).status_code == 404
    finally:
        _cleanup(store, project.id, hash_a, hash_b)
        driver.close()
        support._close_driver()
