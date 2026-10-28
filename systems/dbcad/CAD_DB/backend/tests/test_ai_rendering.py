"""Run the optional renderer in its own VTK environment; never import VTK in API."""
import os
from pathlib import Path
import subprocess

import pytest


@pytest.mark.integration
def test_overview_recovers_distant_geometry_without_changing_anchored_views(tmp_path):
    python = os.environ.get("CAD_AGENT_PYTHON")
    if not python:
        pytest.skip("Set CAD_AGENT_PYTHON to the renderer environment")
    renderer = Path(__file__).parents[1] / "app/ai_render_views.py"
    script = r'''
import json, pathlib, subprocess, sys
import numpy as np
import vtk
from vtk.util.numpy_support import vtk_to_numpy
folder, renderer = pathlib.Path(sys.argv[1]), sys.argv[2]
append = vtk.vtkAppendPolyData()
for x in (-50, 50):
    cube = vtk.vtkCubeSource()
    cube.SetCenter(x, 0, 0)
    cube.SetXLength(8); cube.SetYLength(8); cube.SetZLength(8)
    cube.Update()
    append.AddInputData(cube.GetOutput())
triangles = vtk.vtkTriangleFilter(); triangles.SetInputConnection(append.GetOutputPort())
writer = vtk.vtkSTLWriter(); writer.SetInputConnection(triangles.GetOutputPort())
writer.SetFileName(str(folder / 'scene.stl')); writer.Write()
(folder / 'spec.json').write_text(json.dumps({'views':['front','overview','front'],
    'camera':{'center':[-50,0,0],'size':12}}))
subprocess.run([sys.executable, renderer, str(folder/'scene.stl'), str(folder/'spec.json'), str(folder/'view')], check=True)
arrays=[]
for i in range(3):
    reader=vtk.vtkPNGReader(); reader.SetFileName(str(folder/f'view{i}.png')); reader.Update()
    pixels=vtk_to_numpy(reader.GetOutput().GetPointData().GetScalars()).reshape(760,1000,-1)
    arrays.append(pixels)
assert np.array_equal(arrays[0], arrays[2]), 'Overview changed later anchored view'
def groups(pixels):
    cols=np.where(np.any(pixels[:,:,0] < 230, axis=0))[0]
    return np.split(cols,np.where(np.diff(cols)>1)[0]+1)
fixed, overview = groups(arrays[0]), groups(arrays[1])
assert len(fixed)==1 and len(fixed[0])>100, 'Anchored view fixture is not valid'
assert len(overview)==2 and all(len(g)>10 for g in overview), 'Overview omitted one distant part'
assert overview[0][0]>0 and overview[-1][-1]<999, 'Overview clips model at image edge'
print('Both distant parts visible; anchored views identical')
'''
    result = subprocess.run([python, "-c", script, str(tmp_path), str(renderer)],
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
