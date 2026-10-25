"""Optional offscreen renderer subprocess; the API process imports no VTK/ACIS."""

import sys
import vtk

reader = vtk.vtkSTLReader()
reader.SetFileName(sys.argv[1])
reader.Update()
mapper = vtk.vtkPolyDataMapper()
mapper.SetInputConnection(reader.GetOutputPort())
bounds = reader.GetOutput().GetBounds()
center = [(bounds[i * 2] + bounds[i * 2 + 1]) / 2 for i in range(3)]
size = max(bounds[i * 2 + 1] - bounds[i * 2] for i in range(3)) or 1
window = vtk.vtkRenderWindow()
window.SetOffScreenRendering(1)
window.SetSize(1200, 440)
for index, (direction, up) in enumerate(
    [((1, -1, 1), (0, 0, 1)), ((0, -1, 0), (0, 0, 1)), ((0, 0, 1), (0, 1, 0))]
):
    renderer = vtk.vtkRenderer()
    renderer.SetViewport(index / 3, 0, (index + 1) / 3, 1)
    renderer.SetBackground(0.94, 0.95, 0.97)
    actor = vtk.vtkActor()
    actor.SetMapper(mapper)
    actor.GetProperty().SetColor(0.75, 0.79, 0.84)
    actor.GetProperty().EdgeVisibilityOn()
    actor.GetProperty().SetEdgeColor(0.25, 0.28, 0.32)
    renderer.AddActor(actor)
    window.AddRenderer(renderer)
    camera = renderer.GetActiveCamera()
    camera.SetFocalPoint(*center)
    camera.SetPosition(*[center[i] + direction[i] * size * 3 for i in range(3)])
    camera.SetViewUp(*up)
    camera.ParallelProjectionOn()
    renderer.ResetCamera()
window.Render()
capture = vtk.vtkWindowToImageFilter()
capture.SetInput(window)
capture.Update()
writer = vtk.vtkPNGWriter()
writer.SetFileName(sys.argv[2])
writer.SetInputConnection(capture.GetOutputPort())
writer.Write()
window.Finalize()
