"""Native MCP tool surface; all tool execution is delegated to the API job.

Executed in the configured research environment, which supplies the upstream MCP
dependency. Neither ACIS nor provider credentials are imported by this process.
"""

from __future__ import annotations

import base64
import json
import socket


MAX_RPC_BYTES = 32 * 1024 * 1024


def rpc(socket_path, operation, payload):
    """One bounded request over a private, per-job Unix socket."""
    encoded = json.dumps({"operation": operation, "payload": payload}, allow_nan=False).encode() + b"\n"
    if len(encoded) > MAX_RPC_BYTES:
        raise ValueError("Native agent RPC request exceeds its limit")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(300)
        connection.connect(socket_path)
        connection.sendall(encoded)
        data = bytearray()
        while not data.endswith(b"\n"):
            part = connection.recv(min(65536, MAX_RPC_BYTES + 1 - len(data)))
            if not part:
                raise RuntimeError("Native agent RPC closed before its response")
            data.extend(part)
            if len(data) > MAX_RPC_BYTES:
                raise ValueError("Native agent RPC response exceeds its limit")
    result = json.loads(data)
    if not result.get("ok"):
        raise RuntimeError(result.get("error", "Native agent RPC failed"))
    return result.get("result")


def main(socket_path):
    from mcp.server.fastmcp import FastMCP, Image

    server = FastMCP("resumacad-acis", json_response=True)

    def tool(name, **arguments):
        return rpc(socket_path, "tool", {"name": name, "arguments": arguments})

    @server.tool()
    def run_step(code: str) -> dict:
        """Execute one bounded native cad program atomically in the preview.

        Variables persist. Use the documented cad API, not CadQuery or imports.
        A failed step restores the manifest and variable bindings. Only geometry
        changes create undo entries; queries do not publish project versions.
        """
        return tool("run_step", code=code)

    @server.tool()
    def inspect() -> dict:
        """Inspect actual ACIS components, geometry properties, IDs and native bindings."""
        return tool("inspect")

    @server.tool()
    def list_faces(component: str | None = None, limit: int = 50, offset: int = 0) -> dict:
        """Query actual faces with component IDs, current indices, centers, area and normals.

        Use component and index together. Indices are local to current topology;
        query again after every edit. Paginate rather than assuming a truncated
        list is the complete model.
        """
        return tool("list_faces", component=component, limit=limit, offset=offset)

    @server.tool()
    def list_edges(component: str | None = None, limit: int = 50, offset: int = 0) -> dict:
        """Query actual edges with component IDs, current indices, length, centers and radius."""
        return tool("list_edges", component=component, limit=limit, offset=offset)

    @server.tool()
    def render_views(views: list[str] | None = None, highlight_faces: list[list[float]] | None = None,
                     highlight_edges: list[list[float]] | None = None):
        """Render named views with optional real face/edge selection highlighted red.

        Available views: toprightiso, front, back, left, right, top, bottom.
        Highlight centers come from current list_faces/list_edges. Compare actual
        images and exact measurements before finishing.
        """
        result = tool("render_views", views=views, highlight_faces=highlight_faces,
                      highlight_edges=highlight_edges)
        if not result.get("ok"):
            return result
        return [Image(data=base64.b64decode(data, validate=True), format="png")
                for data in result["images"]] + [{"views": result["views"]}]

    @server.tool()
    def undo() -> dict:
        """Undo the last successful geometry-changing run_step, including its variables."""
        return tool("undo")

    @server.tool()
    def reset() -> dict:
        """Clear the entire isolated preview and native variable/undo state; does not publish."""
        return tool("reset")

    @server.tool()
    def export_step(output_dir: str) -> dict:
        """Export actual STEP through the native exporter. SAT is never reported as STEP."""
        return tool("export_step", output_dir=output_dir)

    server.run(transport="stdio")

