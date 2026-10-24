"""Build an immutable scene manifest from the authoritative worker result."""

from fastapi import HTTPException, status
from .v1_store import SceneComponent


def manifest_from_result(head_components, result):
    removed = {sid for sid in result.get("removed", []) if isinstance(sid, str)}
    manifest: list[SceneComponent] = [
        c for c in head_components if c.hash_sat not in removed
    ]
    previous = {c.hash_sat: c for c in head_components}
    added: list[SceneComponent] = []
    for entry in result.get("modified", []):
        target = previous.get(str(entry.get("sid", "")))
        if target is None or "sat_bytes" not in entry:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                "worker reported an unknown modified component",
            )
        replacement = SceneComponent(
            hash_sat=entry["sat_bytes"],
            hash_glb=entry.get("glb_bytes", target.hash_glb),
            name=target.name,
            color=target.color,
            bbox_min=[
                float(v) for v in entry.get("bbox", {}).get("min", target.bbox_min)
            ],
            bbox_max=[
                float(v) for v in entry.get("bbox", {}).get("max", target.bbox_max)
            ],
            triangles=int(entry.get("triangles", target.triangles)),
            vertices=int(entry.get("vertices", target.vertices)),
            graph=entry.get("graph_bytes", target.graph),
        )
        manifest[manifest.index(target)] = replacement
    for entry in result.get("added", []):
        if "sat_bytes" not in entry:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY, "worker emitted no component bytes"
            )
        bbox = entry.get("bbox", {})
        component = SceneComponent(
            hash_sat=entry["sat_bytes"],
            hash_glb=entry.get("glb_bytes", ""),
            name=str(entry.get("kind") or "component"),
            color=[float(v) for v in entry.get("color", [0.8, 0.8, 0.8])],
            bbox_min=[float(v) for v in bbox.get("min", [0.0, 0.0, 0.0])],
            bbox_max=[float(v) for v in bbox.get("max", [0.0, 0.0, 0.0])],
            triangles=int(entry.get("triangles", 0)),
            vertices=int(entry.get("vertices", 0)),
            graph=entry.get("graph_bytes", ""),
        )
        manifest.append(component)
        added.append(component)

    previous_by_sid = {c.hash_sat: c.hash_sat for c in head_components}
    projection_pairs = [
        (
            entry.get("sat_bytes"),
            entry.get("graph_json"),
            previous_by_sid.get(str(entry.get("sid", ""))),
        )
        for entry in result.get("modified", [])
    ]
    projection_pairs += [
        (entry.get("sat_bytes"), entry.get("graph_json"), None)
        for entry in result.get("added", [])
    ]
    return manifest, projection_pairs, added, removed
