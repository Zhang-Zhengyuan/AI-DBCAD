"""Evaluate saved outputs only; references are never passed into inference."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
from types import SimpleNamespace

from study import DATA, HERE, LOCAL, UPSTREAM, image_checks, tasks, write_json


def reference_paths(request_id):
    from src.utils.db import DatabaseManager
    config = json.loads((HERE.parent / 'neuralcad_edit/.local/config.local.json').read_text())
    db = DatabaseManager(config)
    try:
        request = db.requests.find_one({'_id': request_id})
        edit = db.edits.find_one({'request': request_id, 'user': request['user']})
        brep = db.breps.find_one({'_id': edit['brep_end']})
        return {'step': DATA / brep['step'][0], 'stl': DATA / brep['stl'][0]}
    finally:
        db.close_connection()


def render_export(path, folder):
    import cadquery as cq
    from src.utils.cadquery_rendering import VIEW_PROJECTIONS, render_to_png
    folder.mkdir(parents=True, exist_ok=True)
    model = cq.importers.importStep(str(path))
    shape = cq.Compound.makeCompound(model.vals())
    # This independent validation measures the actual B-Rep, not image existence.
    bb = shape.BoundingBox()
    report = {'brep_valid': shape.isValid(), 'solids': len(shape.Solids()),
              'faces': len(shape.Faces()), 'edges': len(shape.Edges()),
              'volume': sum(s.Volume() for s in shape.Solids()),
              'bbox_size': [bb.xlen, bb.ylen, bb.zlen]}
    cq.exporters.export(shape, str(folder / 'mesh.stl'))
    for name, projection in VIEW_PROJECTIONS.items():
        target = folder / f'{name}.png'
        render_to_png(shape, str(target), projection, 640, 640)
        image_checks(target)
    return report


def api_summary(folder, pricing):
    calls = []
    for path in sorted((folder / 'api').glob('*-response.json')):
        response = json.loads(path.read_text())
        usage = response.get('usage') or {}
        message = response['choices'][0]['message']
        calls.append({'model': response['model'], 'usage': usage,
                      'finish_reason': response['choices'][0]['finish_reason'],
                      'tools': [c['function']['name'] for c in (message.get('tool_calls') or [])]})
    prompt = sum(c['usage'].get('prompt_tokens', 0) for c in calls)
    completion = sum(c['usage'].get('completion_tokens', 0) for c in calls)
    return {'calls': len(calls), 'prompt_tokens': prompt, 'completion_tokens': completion,
            'estimated_cny': (prompt * pricing['input_cny_per_million'] +
                              completion * pricing['output_cny_per_million']) / 1e6,
            'tool_counts': dict(Counter(name for c in calls for name in c['tools'])),
            'finish_reasons': dict(Counter(c['finish_reason'] for c in calls))}


def contact_sheet(folder, task):
    from PIL import Image, ImageDraw, ImageOps, ImageFont
    import matplotlib
    font_path = Path(matplotlib.get_data_path()) / 'fonts/ttf/DejaVuSans.ttf'
    font = ImageFont.truetype(str(font_path), 18)
    small = ImageFont.truetype(str(font_path), 15)
    bold = ImageFont.truetype(str(font_path.with_name('DejaVuSans-Bold.ttf')), 21)
    sheet = Image.new('RGB', (1600, 980), 'white')
    draw = ImageDraw.Draw(sheet)
    draw.text((12, 8), task['request'], font=small, fill='black')
    draw.text((12, 31), task.get('request_text') or 'See original multimodal request', font=font, fill='black')
    draw.text((12, 57), 'Same renderer, independent FitAll. Image similarity does not verify a closed solid or a correct edit.', font=small, fill='black')
    for col, name in enumerate(['input', 'script', 'mcp', 'reference']):
        draw.text((col * 400 + 12, 83), name.upper(), font=bold, fill='black')
        for index, view in enumerate(['toprightiso', 'front']):
            y = 116 + index * 430
            path = folder / 'evaluation' / name / f'{view}.png'
            draw.text((col * 400 + 12, y), view, font=small, fill='black')
            if path.is_file():
                sheet.paste(ImageOps.contain(Image.open(path).convert('RGB'), (400, 400)), (col * 400, y + 20))
            else:
                draw.text((col * 400 + 40, y + 180), 'NO STEP OUTPUT', font=font, fill='black')
    sheet.save(folder / 'comparison.png')


def evaluate(run):
    import open3d as o3d
    from src.utils.evals_feature_geometric import compute_chamfer_distance, load_stl_as_point_cloud, _iou_impl
    from transformers import pipeline
    from huggingface_hub import snapshot_download
    from src.utils.evals_feature_geometric import pair_cosine_similarity
    weights = snapshot_download('facebook/dinov2-base',
                                revision='f9e44c814b77203eaa57a6bdbbd535f21ede1415', local_files_only=True)
    extractor = pipeline(task='image-feature-extraction', model=weights, device='cpu')
    records = json.loads((run / 'results.json').read_text())
    plan = json.loads((run / 'plan.json').read_text())
    report = []
    for task in tasks(plan['requests']):
        request_id = task['request']
        folder = run / request_id
        ref = reference_paths(request_id)
        row = {'request': request_id, 'instruction': task.get('request_text'), 'variants': {}}
        sources = {'input': Path(task['brep_start_path_step']), 'reference': ref['step'],
                   'script': folder / 'script/tmp.step', 'mcp': folder / 'mcp/tmp.step'}
        o3d.utility.random.seed(42)
        target_points = load_stl_as_point_cloud(str(ref['stl']), num_samples=10000)
        for name, source in sources.items():
            summary = {'step_exists': source.is_file()}
            if name in ('script', 'mcp'):
                summary['api'] = api_summary(folder / name, plan['pricing'])
                execution = next(r for r in records if r['request'] == request_id and r['arm'] == name)
                summary.update(run_status=execution['status'], wall_seconds=execution['seconds'])
            if source.is_file():
                exported = folder / 'evaluation' / name
                summary.update(render_export(source, exported))
                o3d.utility.random.seed(42)
                points = load_stl_as_point_cloud(str(exported / 'mesh.stl'), num_samples=10000)
                summary['chamfer_distance_to_reference'] = float(compute_chamfer_distance(points, target_points)[0])
                summary['upstream_surface_voxel_iou_to_reference'] = float(_iou_impl(
                    str(exported / 'mesh.stl'), str(ref['stl']), SimpleNamespace(root_dir='')))
            row['variants'][name] = summary
        # Same flattened token features as upstream extract_dino, on our common renders.
        names = [name for name, value in row['variants'].items() if value['step_exists']]
        features = extractor([str(folder / 'evaluation' / name / 'toprightiso.png') for name in names],
                             batch_size=1, return_tensors=True)
        feature_map = {name: feature.squeeze().numpy().flatten() for name, feature in zip(names, features)}
        for name in names:
            row['variants'][name]['dino_similarity_common_render_to_reference'] = float(
                pair_cosine_similarity(feature_map[name], feature_map['reference']))
        contact_sheet(folder, task)
        report.append(row)
        write_json(run / 'evaluation.json', report)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    write_json(LOCAL / 'latest-evaluation.json', {'run': str(run), 'tasks': report,
               'protocol': {'points': 10000, 'random_seed': 42, 'alignment': False,
                            'cd': 'upstream bidirectional Euclidean surface distance, model units',
                            'iou': 'upstream surface voxel index IoU; not filled solid volume IoU',
                            'dino': 'fixed DINOv2 CPU flattened token features; common CadQuery renders',
                            'acceptance': 'not rated; no paid VLM/human scores'}})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path)
    args = parser.parse_args()
    run = args.run or Path(json.loads((LOCAL / 'latest-paired.json').read_text())['output'])
    evaluate(run.resolve())
