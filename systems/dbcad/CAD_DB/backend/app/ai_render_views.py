"""Optional offscreen multi-view renderer; the API process imports no VTK/ACIS.

Usage: ai_render_views.py <scene.stl> <spec.json> <output-prefix>

spec.json: {"views": ["front", ...], "camera": {"center": [...], "size": s},
            "highlights": [worker render_selection entries]}. A fixed world
camera (anchored by the caller) keeps scale and position changes visible across
renders instead of auto-fitting every image. Highlights are real FACE/EDGE
selection geometry from the worker, drawn in red on top of the mesh.
"""

import json
import math
import sys

import vtk

VIEW_FRAMES = {
    "overview": ((1, -1, 1), (0, 0, 1)),
    "toprightiso": ((1, -1, 1), (0, 0, 1)),
    "isometric": ((1, 1, 1), (0, 0, 1)),
    "front": ((0, -1, 0), (0, 0, 1)),
    "back": ((0, 1, 0), (0, 0, 1)),
    "left": ((-1, 0, 0), (0, 0, 1)),
    "right": ((1, 0, 0), (0, 0, 1)),
    "top": ((0, 0, 1), (0, 1, 0)),
    "bottom": ((0, 0, -1), (0, 1, 0)),
}


def _coincident_offset(mapper):
    try:
        mapper.SetResolveCoincidentTopologyToPolygonOffset()
    except AttributeError:
        pass


def _selection_actors(selections):
    actors = []
    for item in selections:
        actor = vtk.vtkActor()
        mapper = vtk.vtkPolyDataMapper()
        polydata = vtk.vtkPolyData()
        property_ = actor.GetProperty()
        property_.SetColor(0.86, 0.08, 0.08)
        if item.get("kind") == "face":
            points = vtk.vtkPoints()
            for position in item.get("positions", []):
                points.InsertNextPoint(float(position[0]), float(position[1]), float(position[2]))
            indices = item.get("indices", [])
            polys = vtk.vtkCellArray()
            for start in range(0, len(indices) - len(indices) % 3, 3):
                ids = vtk.vtkIdList()
                for index in indices[start:start + 3]:
                    ids.InsertNextId(int(index))
                polys.InsertNextCell(ids)
            polydata.SetPoints(points)
            polydata.SetPolys(polys)
            property_.EdgeVisibilityOn()
            property_.SetEdgeColor(0.55, 0.02, 0.02)
        else:
            points = vtk.vtkPoints()
            for position in item.get("edge_positions", []):
                points.InsertNextPoint(float(position[0]), float(position[1]), float(position[2]))
            lines = vtk.vtkCellArray()
            for run in item.get("edge_runs", []):
                first, count = int(run.get("firstVertex", 0)), int(run.get("vertexCount", 0))
                if count < 2:
                    continue
                ids = vtk.vtkIdList()
                for index in range(first, first + count):
                    ids.InsertNextId(index)
                lines.InsertNextCell(ids)
            polydata.SetPoints(points)
            polydata.SetLines(lines)
            property_.SetLineWidth(3)
        _coincident_offset(mapper)
        try:
            mapper.SetRelativeCoincidentTopologyPolygonOffsetParameters(-1, -1)
        except AttributeError:
            pass
        mapper.SetInputData(polydata)
        actor.SetMapper(mapper)
        actors.append(actor)
    return actors


def main():
    if len(sys.argv) != 4:
        raise SystemExit("usage: ai_render_views.py <scene.stl> <spec.json> <output-prefix>")
    source, spec_path, prefix = sys.argv[1:]
    spec = json.loads(open(spec_path).read())
    views = spec.get("views") or ["toprightiso"]
    unknown = [view for view in views if view not in VIEW_FRAMES]
    if unknown:
        raise SystemExit("unknown render views: " + ",".join(unknown))

    reader = vtk.vtkSTLReader()
    reader.SetFileName(source)
    reader.Update()
    mapper = vtk.vtkPolyDataMapper()
    mapper.SetInputConnection(reader.GetOutputPort())
    _coincident_offset(mapper)
    bounds = reader.GetOutput().GetBounds()
    default_center = [(bounds[i * 2] + bounds[i * 2 + 1]) / 2 for i in range(3)]
    default_size = max(bounds[i * 2 + 1] - bounds[i * 2] for i in range(3)) or 1
    camera_spec = spec.get("camera") or {}
    center = [float(v) for v in camera_spec.get("center", default_center)]
    size = float(camera_spec.get("size", default_size)) or 1
    actors = _selection_actors(spec.get("highlights", []))

    for index, view in enumerate(views):
        direction, up = VIEW_FRAMES[view]
        # A bounding sphere fits every direction; leave ordinary world views
        # anchored, including views requested after overview in the same call.
        view_center, view_size = center, size
        if view == "overview":
            view_center = default_center
            view_size = 1.1 * (math.sqrt(sum((bounds[2*i+1] - bounds[2*i])**2 for i in range(3))) or 1)
        window = vtk.vtkRenderWindow()
        window.SetOffScreenRendering(1)
        window.SetSize(1000, 760)
        renderer = vtk.vtkRenderer()
        renderer.SetBackground(0.94, 0.95, 0.97)
        base = vtk.vtkActor()
        base.SetMapper(mapper)
        base.GetProperty().SetColor(0.75, 0.79, 0.84)
        base.GetProperty().EdgeVisibilityOn()
        base.GetProperty().SetEdgeColor(0.25, 0.28, 0.32)
        renderer.AddActor(base)
        for actor in actors:
            renderer.AddActor(actor)
        window.AddRenderer(renderer)
        camera = renderer.GetActiveCamera()
        camera.SetFocalPoint(*view_center)
        camera.SetPosition(*[view_center[i] + direction[i] * view_size * 3 for i in range(3)])
        camera.SetViewUp(*up)
        camera.ParallelProjectionOn()
        camera.SetParallelScale(view_size / 2)
        renderer.ResetCameraClippingRange()
        window.Render()
        capture = vtk.vtkWindowToImageFilter()
        capture.SetInput(window)
        capture.Update()
        writer = vtk.vtkPNGWriter()
        writer.SetFileName(f"{prefix}{index}.png")
        writer.SetInputConnection(capture.GetOutputPort())
        writer.Write()
        window.Finalize()


if __name__ == "__main__":
    main()
