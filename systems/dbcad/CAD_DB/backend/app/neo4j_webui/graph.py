"""Graph endpoints for the administrator dashboard (V1 entity projection)."""

import json
from typing import Any
from fastapi import APIRouter, HTTPException, Query
from .support import _run_cypher, _run_single_cypher, _semantic_label

router = APIRouter()
MAX_GRAPH_NODES = 5000
MAX_GRAPH_RELATIONSHIPS = 20000


@router.get("/components/{hash_sat}/entity-graph")
def get_component_entity_graph(
    hash_sat: str,
    label: str | None = Query(default=None),
    limit: int = Query(default=400, ge=1, le=MAX_GRAPH_NODES),
) -> dict[str, Any]:
    """组件实体级投影图（P6/ADR-3）：实体节点 + TOPOLOGY 拓扑边。

    label 过滤（face/Face 皆可）只筛节点，边保留全图拓扑中两端都可见的边；
    按共享内容节点去重，ID 与关系端点一致；局部实体引用数另行统计。
    节点和关系均有明确上限及截断标志，不修改任何存储数据。
    """
    component = _run_single_cypher(
        "MATCH (c:V1Component {hash_sat: $h}) RETURN c.hash_sat AS hash_sat, c.name AS name",
        {"h": hash_sat},
    )
    if component is None:
        raise HTTPException(status_code=404, detail=f"Component '{hash_sat[:12]}' not found")

    filter_clause = ""
    params: dict[str, Any] = {"h": hash_sat, "limit": limit, "needle": None}
    if label:
        filter_clause = "WHERE $needle IN [l IN labels(e) | toUpper(l)]"
        params["needle"] = label.upper()

    nodes = _run_cypher(
        f"""
        MATCH (c:V1Component {{hash_sat: $h}})-[membership:HAS_ENTITY]->(e:V1Entity)
        {filter_clause}
        WITH e, count(membership) AS reference_count
        RETURN coalesce(e.entity_key, e.entity_id, elementId(e)) AS entity_id,
               e.entity_key AS entity_key, labels(e) AS labels,
               e.props_json AS props_json, reference_count
        ORDER BY coalesce(e.top_level, false) DESC, entity_id
        LIMIT $limit
        """,
        params,
    )
    visible = {str(n["entity_id"]) for n in nodes}
    stats = _run_single_cypher(
        """
        MATCH (:V1Component {hash_sat: $h})-[membership:HAS_ENTITY]->(e:V1Entity)
        RETURN count(DISTINCT e) AS total_entities,
               count(membership) AS total_entity_references,
               count(DISTINCT CASE
                 WHEN $needle IS NULL OR $needle IN [l IN labels(e) | toUpper(l)]
                 THEN e END) AS matched_entities
        """,
        {"h": hash_sat, "needle": params["needle"]},
    )
    rels = []
    if visible:
        rels = _run_cypher(
            """
            MATCH (s:V1Entity)-[t:TOPOLOGY {component: $h}]->(e:V1Entity)
            WHERE coalesce(s.entity_key, s.entity_id, elementId(s)) IN $visible
              AND coalesce(e.entity_key, e.entity_id, elementId(e)) IN $visible
            RETURN elementId(t) AS id,
                   coalesce(s.entity_key, s.entity_id, elementId(s)) AS start,
                   coalesce(e.entity_key, e.entity_id, elementId(e)) AS end,
                   t.rel_type AS rel_type
            ORDER BY id
            LIMIT $relationship_limit
            """,
            {"h": hash_sat, "visible": sorted(visible),
             "relationship_limit": MAX_GRAPH_RELATIONSHIPS + 1},
        )

    node_items: list[dict[str, Any]] = []
    for node in nodes:
        labels = [str(l) for l in (node.get("labels") or []) if l != "V1Entity"]
        try:
            props = json.loads(str(node.get("props_json") or "{}"))
        except (TypeError, ValueError):
            props = {}
        if not isinstance(props, dict):
            props = {}
        geometry = props.get("geometry") if isinstance(props.get("geometry"), dict) else {}
        node_items.append(
            {
                "id": str(node["entity_id"]),
                "entity_key": str(node.get("entity_key") or ""),
                "reference_count": int(node.get("reference_count") or 0),
                "labels": labels,
                "semantic": _semantic_label(labels, props),
                "entity_type": str(props.get("entity_type") or ""),
                "geometry_type": str(geometry.get("type") or ""),
                "top_level": bool(props.get("top_level")),
                "props": props,
            }
        )

    stats = stats or {}
    matched = int(stats.get("matched_entities") or 0)
    return {
        "hash_sat": str(component.get("hash_sat") or ""),
        "name": str(component.get("name") or ""),
        "total_entities": int(stats.get("total_entities") or 0),
        "total_entity_references": int(stats.get("total_entity_references") or 0),
        "matched_entities": matched,
        "node_limit": limit,
        "returned": len(node_items),
        "truncated": matched > len(node_items),
        "relationship_limit": MAX_GRAPH_RELATIONSHIPS,
        "relationships_truncated": len(rels) > MAX_GRAPH_RELATIONSHIPS,
        "label_filter": label,
        "nodes": node_items,
        "rels": [
            {"id": str(r["id"]), "start": str(r["start"]), "end": str(r["end"]),
             "type": str(r["rel_type"])}
            for r in rels[:MAX_GRAPH_RELATIONSHIPS]
            if str(r["start"]) in visible and str(r["end"]) in visible
        ],
    }
