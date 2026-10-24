"""Pinned AgenticCADedit reproduction; offline verification and paired API runs."""
from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
from datetime import datetime
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
LOCAL = HERE / '.local'
UPSTREAM = LOCAL / 'upstream'
COMMIT = 'c084034cdcf462025a60ee70c5ba94577428e5ca'
DATA = HERE.parent / 'neuralcad_edit/.local/data/edit_192_external'
COLUMNS = ['request', 'request_type', 'file_name', 'brep_start_path',
           'request_video_30fps_720p_audio', 'request_transcript', 'request_events', 'request_text', 'views']
sys.path.insert(0, str(UPSTREAM))


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str) + '\n')


def new_run(prefix):
    path = LOCAL / 'runs' / (prefix + '-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    path.mkdir(parents=True)
    return path


def doctor():
    import cadquery, mcp, open3d, probreg, decord
    from src.harnesses.cadquery_mcp.cad_session import CadSession
    from src.vlms.vllm_openai import VLM
    head = subprocess.check_output(['git', '-C', str(UPSTREAM), 'rev-parse', 'HEAD'], text=True).strip()
    assert head == COMMIT, f'Unexpected upstream revision: {head}'
    assert not subprocess.check_output(['git', '-C', str(UPSTREAM), 'status', '--porcelain'], text=True)
    report = {'python': sys.version.split()[0], 'architecture': platform.machine(), 'upstream': head,
              'dataset_exists': (DATA / 'parquets/val_edit_all.parquet').is_file(),
              'packages': {p: importlib.metadata.version(p) for p in
                           ['cadquery', 'cadquery-ocp', 'mcp', 'openai', 'open3d', 'probreg']}}
    assert report['dataset_exists'], 'First run ../neuralcad_edit/setup.sh to prepare verified data'
    write_json(LOCAL / 'doctor.json', report)
    print(json.dumps(report, indent=2))


def tasks(requests=None):
    """Only the released request-side fields can enter a model context."""
    import pandas as pd
    frame = pd.read_parquet(DATA / 'parquets/val_edit_all.parquet', columns=COLUMNS)
    if requests:
        frame = frame.set_index('request', drop=False).loc[requests].reset_index(drop=True)
    from src.scripts_benchmark_inference.run_harness import add_root_dir_to_files, format_task_dict
    rows = []
    for _, row in frame.iterrows():
        item = json.loads(row.to_json())
        add_root_dir_to_files(item, str(DATA))
        rows.append(format_task_dict(item))
    return rows


def image_checks(path, require_red=False):
    import numpy as np
    from PIL import Image
    array = np.asarray(Image.open(path).convert('RGB'))
    assert array.std() > 5, f'Blank image: {path}'
    red = int(((array[:, :, 0] > 160) & (array[:, :, 1] < 100) & (array[:, :, 2] < 100)).sum())
    if require_red:
        assert red > 20, f'Highlight absent: {path}'
    return {'size': list(array.shape[:2]), 'red_pixels': red}


async def tools_smoke(output, task):
    """Exercise the actual official server over JSON-RPC, with no model calls."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    params = StdioServerParameters(command=sys.executable,
                                   args=[str(UPSTREAM / 'src/harnesses/cadquery_mcp/server.py')])
    trace = []
    with (output / 'server.log').open('w') as server_log:
        async with stdio_client(params, errlog=server_log) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                names = [t.name for t in (await session.list_tools()).tools]
                assert {'run_step', 'inspect', 'list_faces', 'list_edges', 'render_views',
                        'undo', 'reset', 'export_step'} <= set(names)

                async def call(name, args=None, ok=True):
                    result = await asyncio.wait_for(session.call_tool(name, args or {}), timeout=120)
                    assert not result.isError, f'{name}: {result.content}'
                    texts = [b.text for b in result.content if b.type == 'text']
                    value = json.loads(texts[0]) if texts else {}
                    if 'ok' in value:
                        assert value['ok'] is ok, f'{name}: {value}'
                    trace.append({'tool': name, 'arguments': args or {}, 'result': value})
                    return result, value

                step = task['brep_start_path_step']
                await call('run_step', {'code': f'result = cq.importers.importStep({step!r})'})
                _, before = await call('inspect')
                await call('run_step', {'code': 'result = result.translate((3, 0, 0))'})
                _, moved = await call('inspect')
                assert abs(moved['center']['x'] - before['center']['x'] - 3) < .002
                await call('run_step', {'code': "result = result.translate((100, 0, 0)); raise RuntimeError('rollback probe')"}, ok=False)
                _, recovered = await call('inspect')
                assert recovered == moved
                _, faces = await call('list_faces', {'limit': 5})
                await call('list_edges', {'limit': 5})
                center = faces['faces'][0]['center']
                if isinstance(center, dict):
                    center = [center[k] for k in ('x', 'y', 'z')]
                rendered, _ = await call('render_views', {'views': ['toprightiso', 'top'], 'highlight_faces': [center]})
                images = [b for b in rendered.content if b.type == 'image']
                assert len(images) == 2
                checks = []
                for i, block in enumerate(images):
                    path = output / f'highlight-{i}.png'
                    path.write_bytes(base64.b64decode(block.data))
                    checks.append(image_checks(path))
                assert sum(c['red_pixels'] for c in checks) > 20
                await call('undo')
                _, restored = await call('inspect')
                assert restored == before
                await call('export_step', {'output_dir': str(output)})
                await call('reset')
                _, empty = await call('inspect')
                assert 'no ' in empty['result']
    write_json(output / 'trace.json', trace)
    return {'tools': names, 'before': before, 'translated': moved, 'highlight': checks,
            'failure_recovered': True, 'undo_restored': True, 'api_calls': 0}


def smoke():
    output = new_run('tools-smoke')
    task = next(t for t in tasks() if t.get('request_text'))
    report = asyncio.run(tools_smoke(output, task))
    import cadquery as cq
    shape = cq.importers.importStep(str(output / 'tmp.step'))
    assert shape.val().isValid()
    report.update({'request': task['request'], 'output': str(output),
                   'exported_solids': len(shape.solids().vals())})
    write_json(output / 'summary.json', report)
    write_json(LOCAL / 'smoke.json', report)
    print(json.dumps(report, indent=2))


def diagnostics():
    """Record an upstream transaction limitation, without changing the method."""
    from src.harnesses.cadquery_mcp.cad_session import CadSession
    session = CadSession()
    session.run_step("result = cq.Assembly(); result.add(cq.Workplane('XY').box(1,1,1), name='a')")
    before = session.inspect()
    failed = session.run_step("result.add(cq.Workplane('XY').box(1,1,1).translate((3,0,0)), name='b'); raise RuntimeError('probe')")
    after = session.inspect()
    report = {'kind': 'in-place assembly mutation followed by error', 'before': before, 'after': after,
              'step_reported_ok': failed['ok'], 'rollback_restored_geometry': before == after,
              'explanation': 'Official history copies namespace bindings shallowly; Assembly.add mutates the bound object',
              'upstream_modified': False, 'api_calls': 0}
    write_json(LOCAL / 'diagnostics.json', report)
    print(json.dumps(report, indent=2))


def published():
    import pandas as pd
    folder = UPSTREAM / 'consolidated_data'
    edits = pd.read_csv(folder / 'edits_v2_df_edits_2026_07_22.csv')
    ratings = pd.read_csv(folder / 'edits_v2_df_ratings_2026_07_22.csv')
    report = {'edit_rows': len(edits), 'rating_rows': len(ratings),
              'edit_users': edits['user'].value_counts().to_dict(), 'rating_columns': list(ratings.columns),
              'note': 'Released CSV inventory, not new inference or a verified reproduction of paper tables.'}
    groups = []
    for user, group in edits.loc[edits['user'].str.startswith('qwen3.6-27b_')].groupby('user'):
        valid = group['dino-v2_similarity'].notna() & group['failed_run'].eq(False)
        scored = group.loc[valid]
        groups.append({'user': user, 'tasks': len(group), 'released_valid_view_count': int(valid.sum()),
                       'released_valid_view_percent': float(valid.mean() * 100),
                       'median_chamfer_on_valid_views': float((1 / (scored['chamfer_similarity'] + 1e-8)).median()),
                       'median_iou_on_valid_views': float(scored['iou'].median()),
                       'median_dino_on_valid_views': float(scored['dino-v2_similarity'].median())})
    qwen_ratings = ratings.loc[ratings['edit_user'].str.startswith('qwen3.6-27b_')]
    report.update({'qwen_geometry': groups, 'qwen_released_raters': sorted(qwen_ratings['rater_id'].unique()),
                   'acceptance_reproduced': False,
                   'limitations': ['Qwen release includes geometric scores but no Qwen VLM/human ratings',
                                   'CSV vlm_acceptance is all zero for Qwen; it is not the paper acceptance result',
                                   'Gemma/GPT-5.6-Luna model outputs are not included in these CSVs',
                                   'View availability matches paper validity counts; this is not B-Rep.isValid']})
    write_json(LOCAL / 'published-inventory.json', report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


def paired(args):
    # stdio_client binds errlog=sys.stderr at import time. Import before redirecting
    # per-task logs, otherwise the second session inherits the first closed file.
    import mcp.client.stdio
    from provider import Budget, BudgetExceeded, Transport, is_budget_exceeded, load_credentials
    from src.vlms.base_vlm import BaseVLM
    from src.vlms.vllm_openai import VLM
    pricing = json.loads((LOCAL / 'pricing.json').read_text())
    if args.max_iters < 1 or args.max_tokens < 1 or args.budget <= 0:
        raise ValueError('Iteration, output-token and budget limits must be positive')
    selected = tasks(args.request)
    config = json.loads((UPSTREAM / 'src/config/edit_192_external.json').read_text())
    common = config['benchmark_models']['qwen3.6-27b_cadquery-script'].copy()
    common.update(model=pricing['model'], max_tokens=args.max_tokens)
    # The same transport/settings wrap both official harnesses; no edits to their loops.
    common['1m_token_cost_input'] = pricing['input_cny_per_million']
    common['1m_token_cost_output'] = pricing['output_cny_per_million']
    plan = {'requests': [t['request'] for t in selected], 'upstream': COMMIT, 'model_config': common,
            'max_iters': args.max_iters, 'budget_cny': args.budget, 'execute': args.execute,
            'arms': ['script', 'mcp'], 'pricing': pricing,
            'deviations': ['API-hosted model instead of author vLLM weights',
                           'Pilot iteration/output-token limits; not full paper reproduction']}
    print(json.dumps(plan, ensure_ascii=False, indent=2), flush=True)
    if not args.execute:
        write_json(LOCAL / 'paired-plan.json', plan)
        return
    output = args.resume.resolve() if args.resume else new_run('paired')
    if args.resume:
        original_plan = json.loads((output / 'plan.json').read_text())
        for key in ('model_config', 'max_iters', 'budget_cny', 'pricing'):
            if plan[key] != original_plan[key]:
                raise ValueError(f'Resume changed {key}')
        if not set(plan['requests']) <= set(original_plan['requests']):
            raise ValueError('Resume cannot add tasks to an existing run')
    else:
        write_json(output / 'plan.json', plan)
    budget = Budget(LOCAL / 'budget-pilot.json', args.budget, pricing)
    credentials = load_credentials(LOCAL / 'provider.env')
    records = json.loads((output / 'results.json').read_text()) if args.resume else []
    for index, task in enumerate(selected):
        # Alternate arm order across tasks rather than always favoring a warm service.
        for arm in (['script', 'mcp'] if index % 2 == 0 else ['mcp', 'script']):
            if args.arm and args.arm != arm:
                continue
            folder = output / task['request'] / arm
            existing = next((r for r in records if r['request'] == task['request'] and r['arm'] == arm), None)
            if any((folder / 'api').glob('*-request.json')) or (folder / 'tmp.step').exists():
                raise ValueError('Refusing to overwrite model requests or STEP outputs')
            if existing:
                if existing.get('api_calls', 0) or (folder / 'tmp.step').exists():
                    raise ValueError('Refusing to overwrite an attempted model experiment; choose a new run')
                write_json(folder / 'infrastructure-failure.json', existing)
                if (folder / 'error.log').exists():
                    (folder / 'error.log').rename(folder / 'infrastructure-error.log')
                records.remove(existing)
            folder.mkdir(parents=True, exist_ok=True)
            client = Transport(credentials, budget, folder / 'api', task['request'] + '/' + arm)

            class BoundedVLM(VLM):
                def __init__(self):
                    BaseVLM.__init__(self, common, cache=False)
                    self.config = common.copy()

                def _chat_create(self, messages, tools=None):
                    kwargs = {'model': self.config['model'], 'messages': messages,
                              'max_tokens': self.config['max_tokens'],
                              'temperature': self.config['temperature'], 'top_p': self.config['top_p'],
                              'extra_body': {'chat_template_kwargs': self.config['chat_template_kwargs']}}
                    if tools is not None:
                        kwargs.update(tools=tools, tool_choice='auto')
                    return client.create(**kwargs)

            record = {'request': task['request'], 'arm': arm, 'output': str(folder), 'model': pricing['model']}
            started = time.monotonic()
            print(f"Running {task['request']} / {arm}", flush=True)
            try:
                model = BoundedVLM()
                harness = UPSTREAM / ('src/harnesses/cadquery_script.py' if arm == 'script'
                                      else 'src/harnesses/cadquery_mcp/server.py')
                method = model.cadquery_script if arm == 'script' else model.cadquery_mcp
                with (folder / 'harness.log').open('w') as log, contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                    result = method(instruction_text='Edit the existing model according to the provided instructions.',
                                    task_info_dict=task.copy(), harness_script_file=str(harness),
                                    output_dir=str(folder), max_iters=args.max_iters)
                record.update(result)
                record['status'] = 'completed' if (folder / 'tmp.step').is_file() else 'missing_step'
            except Exception as error:
                # Keep the full local traceback for diagnosis without exposing credentials.
                import traceback
                (folder / 'error.log').write_text(traceback.format_exc().replace(credentials['CAD_API_KEY'], '[REDACTED]'))
                record.update(status='error', error_type=type(error).__name__)
                record['budget_exceeded'] = is_budget_exceeded(error)
            record['seconds'] = time.monotonic() - started
            record['budget'] = budget.summary()
            record['api_calls'] = client.counter
            write_json(folder / 'settings.json', {'edit_request_id': task['request'],
                       'userId': f"paratera-qwen3.6-27b_cadquery-{arm}_pilot", 'isHuman': False, **record})
            records.append(record)
            write_json(output / 'results.json', records)
            print(json.dumps(record, ensure_ascii=False), flush=True)
            if record.get('budget_exceeded'):
                write_json(LOCAL / 'latest-paired.json', {'output': str(output), 'records': records, 'budget': budget.summary()})
                raise BudgetExceeded('Batch stopped at the budget limit; partial results preserved')
    write_json(LOCAL / 'latest-paired.json', {'output': str(output), 'records': records, 'budget': budget.summary()})
    print(f'Paired output: {output}', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('doctor', 'smoke', 'published', 'diagnostics'):
        commands.add_parser(name)
    command = commands.add_parser('paired')
    command.add_argument('--request', action='append', required=True)
    command.add_argument('--max-iters', type=int, default=12)
    command.add_argument('--max-tokens', type=int, default=8192)
    command.add_argument('--budget', type=float, default=30)
    command.add_argument('--execute', action='store_true')
    command.add_argument('--resume', type=Path)
    command.add_argument('--arm', choices=['script', 'mcp'])
    command = commands.add_parser('evaluate')
    command.add_argument('--run', type=Path)
    args = parser.parse_args()
    if args.command == 'paired':
        paired(args)
    elif args.command == 'evaluate':
        from evaluate import evaluate
        run = args.run or Path(json.loads((LOCAL / 'latest-paired.json').read_text())['output'])
        evaluate(run.resolve())
    else:
        globals()[args.command]()


if __name__ == '__main__':
    main()
