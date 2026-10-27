"""Actual official-loop/ACIS long-prefix pilot; raw outputs stay under runs/."""
from __future__ import annotations
import argparse
import asyncio
import copy
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import itertools
import json
import math
from pathlib import Path
import random
import re
import subprocess
import sys
import time
import traceback
from uuid import uuid4

from scenes import EVENTS, POLICIES, contract, evaluate, human_offset, initial_commands, instruction, serialize, spec, timeline

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BACKEND = ROOT / "systems/dbcad/CAD_DB/backend"
sys.path.insert(0, str(BACKEND))


def dump(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str))
    temporary.replace(path)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_hashes():
    files = list(HERE.glob("*.py")) + [HERE / "PROTOCOL.md"]
    files += [BACKEND / "app" / name for name in (
        "ai_editor.py", "ai_agentic.py", "ai_agentic_runner.py", "ai_agentic_mcp.py",
        "ai_cad_facade.py", "ai_messages.py", "cad_contract.py", "ai_budget.py",
        "ai_render_views.py", "worker_pool.py", "worker_client.py")]
    files += [BACKEND / "uv.lock"]
    return {str(p.relative_to(ROOT)): sha(p) for p in files}


def register(path, config_path):
    from app import ai_editor as editor
    from app.config import settings
    config = json.loads(Path(config_path).read_text())
    settings.ai_config = config["ai_config"]
    runtime = editor.configuration()
    if runtime.get("framework") != "agenticcadedit-acis":
        raise ValueError("Official native framework is required")
    if runtime["budget"].limit > 300:
        raise ValueError("This authorization cannot exceed CNY300")
    if path.exists():
        raise FileExistsError("Registration never overwrites a run")
    path.mkdir(parents=True)
    models = config.get("models") or [{"label": runtime["pricing"]["model"], "pricing": runtime["pricing"]}]
    for model in models:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*",model.get("label", "")):
            raise ValueError("Model label must be a local directory name")
    patch=subprocess.check_output(["git","diff","--binary","HEAD"],cwd=ROOT)
    (path / "working-tree.patch").write_bytes(patch)
    for relative in source_hashes():
        snapshot=path / "frozen-source" / relative
        snapshot.parent.mkdir(parents=True,exist_ok=True)
        snapshot.write_bytes((ROOT / relative).read_bytes())
    plan = dict(experiment="EXP-004", protocol_version=1, created_at=datetime.now(timezone.utc).isoformat(),
                git_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT,text=True).strip(),
                source_hashes=source_hashes(), upstream_revision="c084034cdcf462025a60ee70c5ba94577428e5ca",
                working_tree_dirty=bool(subprocess.check_output(["git","status","--porcelain"],cwd=ROOT,text=True).strip()),
                working_tree_patch_sha256=sha(path / "working-tree.patch"),
                worker_sha256=sha(str(settings.worker_exe) + ".bin"),
                config=config, models=models, policies=list(POLICIES), families=["pin", "slider"],
                events=list(EVENTS), prefix_stages=10, continuation_stages=2,
                allocated_branches_per_model=30, seed=20261028, segment_calls=8, maximum_segments=2,
                output_tokens=8192, temperature=.6, top_p=.95, budget_before=runtime["budget"].summary(),
                claim="Controlled long-prefix paired branches; failures are not assumed")
    dump(path / "run.json", plan)
    dump(path / "freeze.json", {"run_sha256": sha(path / "run.json"), "source_hashes": plan["source_hashes"]})
    print(json.dumps({"registered": str(path), "models": [m["label"] for m in models], "branches_per_model": 30},ensure_ascii=False),flush=True)


def verify(path):
    plan = json.loads((path / "run.json").read_text())
    frozen = json.loads((path / "freeze.json").read_text())
    if sha(path / "run.json") != frozen["run_sha256"] or source_hashes() != frozen["source_hashes"]:
        raise ValueError("Frozen source/protocol changed; register a NEW run, never rewrite this one")
    return plan


class Runner:
    def __init__(self, root, plan):
        from app import ai_editor, ai_agentic
        self.root, self.plan = root, plan
        self.editor, self.bridge = ai_editor, ai_agentic
        self.model_folder = None
        self.current_stage = None
        self.events = []
        self.reference = None
        self.current_spec = None
        self.original_model = ai_editor.official_model
        self.original_configuration = ai_editor.configuration
        self.original_dispatch = ai_agentic.AgenticBridge.dispatch

    async def query(self, manifest, request):
        from app.worker_pool import get_worker_pool
        pool = get_worker_pool()
        return await pool.query("exp004-audit", len(self.events), manifest, request)

    async def host(self, project, command, name=None):
        from app.worker_pool import get_worker_pool
        from app.command_result import manifest_from_result
        from app.v1_store import get_v1_store
        store = get_v1_store()
        head = store.latest(project)
        before = head.components if head else []
        result = await get_worker_pool().execute(project, head.number if head else 0, before, command)
        manifest, _, added, _ = manifest_from_result(before, result)
        if name:
            digest = added[0].hash_sat
            manifest = [replace(c, name=name) if c.hash_sat == digest else c for c in manifest]
        version = store.commit_version(project, message="human/setup: " + command["action"], author="EXP004 host",
                                       components=manifest, base_version=head.number if head else None)
        return version

    async def exact(self, manifest):
        parts = {c.name: c for c in manifest}
        if not {"movable", "obstacle"} <= set(parts):
            return {"missing": True}
        args = {"a": parts["movable"].hash_sat, "b": parts["obstacle"].hash_sat}
        return {"distance": await self.query(manifest, {"kind": "distance", **args}),
                "interference": await self.query(manifest, {"kind": "interference", **args}),
                "properties": await self.query(manifest,{"kind":"inspect"})}

    async def install_observers(self):
        runner = self

        async def model(job, config, messages, tools):
            folder = runner.current_stage / "api"
            folder.mkdir(exist_ok=True)
            index = len(list(folder.glob("*-request.json"))) + 1
            dump(folder / f"{index:03d}-request.json", {"model": config["pricing"]["model"], "messages": messages, "tools": tools})
            started = time.monotonic()
            try:
                response = await runner.original_model(job, config, messages, tools)
                dump(folder / f"{index:03d}-response.json", response)
                return response
            except BaseException as error:
                dump(folder / f"{index:03d}-error.json", {"type":type(error).__name__,"detail":str(error)[:1000]})
                raise
            finally:
                dump(folder / f"{index:03d}-timing.json", {"elapsed_s":time.monotonic()-started})

        async def dispatch(bridge, operation, payload):
            started = time.monotonic()
            result = await runner.original_dispatch(bridge, operation, payload)
            if operation == "tool":
                event = {"tool":payload.get("name"), "arguments":payload.get("arguments",{}),
                         "elapsed_s":time.monotonic()-started,
                         "result":{k:v for k,v in result.items() if k != "images"} if isinstance(result,dict) else result}
                if event["tool"] in {"run_step", "undo", "reset"}:
                    event["manifest"] = serialize(bridge.job.manifest)
                    if runner.reference is not None:
                        event["oracle"] = evaluate(bridge.job.manifest,runner.reference,runner.current_spec)
                        event["exact_hidden"] = await runner.exact(bridge.job.manifest)
                runner.events.append(event)
                dump(runner.current_stage / "tool-events.json",runner.events)
            return result

        self.editor.official_model = model
        self.bridge.AgenticBridge.dispatch = dispatch

    async def stage(self, project, parent, mode, text, folder):
        from app.v1_store import get_v1_store
        folder.mkdir(parents=True, exist_ok=False)
        self.current_stage, self.events = folder, []
        dump(folder / "input.json",{"instruction":text,"mode":mode,"parent":parent.id if parent else None,
                                     "manifest":serialize(get_v1_store().latest(project).components)})
        started = time.monotonic()
        job = await self.editor.start(project, get_v1_store().latest(project).number,text,[],
                                      parent.id if parent else None,mode=mode)
        for segment in range(self.plan["maximum_segments"]):
            await job.task
            if segment+1 < self.plan["maximum_segments"] and job.status=="paused" and job.pause_reason=="round_limit":
                await self.editor.resume(job)
            else:
                break
        # Reviewer policy is fixed before outcomes: apply every ready proposal.
        if job.status == "ready":
            self.editor.apply(job,"EXP004 isolated automatic reviewer")
        current = get_v1_store().latest(project).components
        entries = self.editor.configuration()["budget"].update(lambda d: [c for c in d["calls"] if c["label"]=="native-ai/"+job.id])
        finished = any(t.get("tool")=="official_segment" and json.loads(t["detail"]).get("finished") for t in job.trace)
        record = {"job":job.public(),"manifest":serialize(current),"preview":serialize(job.manifest),
                  "messages":job.messages,"elapsed_s":time.monotonic()-started,
                  "finished":finished,"ledger_entries":entries,
                  "charged_or_reserved_cny":sum(c["charged_or_reserved_cny"] for c in entries),
                  "geometry":evaluate(current,self.reference,self.current_spec) if self.reference is not None else None}
        if record["geometry"] is not None:
            blocked = any(word in job.summary.lower() for word in ("阻", "不可行", "不足", "小于", "blocked", "infeasible", "cannot"))
            record["geometry"]["task_success"] = (record["geometry"].get("geometry_success",False) and finished
                and (record["geometry"].get("feasible",False) or blocked))
            record["exact_hidden"] = await self.exact(current)
            properties=record["exact_hidden"].get("properties",{}).get("components",[])
            moving=next((c for c in current if c.name=="movable"),None)
            measured=next((p for p in properties if moving and p.get("id")==moving.hash_sat),{})
            expected_volume=16*math.pi if self.current_spec["family"]=="pin" else 96
            geometry_valid=(bool(properties) and all(p.get("valid") for p in properties)
                            and measured.get("solids")==1
                            and math.isclose(measured.get("volume",-1),expected_volume,rel_tol=1e-7,abs_tol=1e-6))
            record["geometry"]["solid_valid_and_expected_volume"]=geometry_valid
            record["geometry"]["task_success"] &= geometry_valid
        dump(folder / "result.json",record)
        print(json.dumps({"stage":str(folder.relative_to(self.root)),"status":job.status,"calls":job.calls,
                          "cost":round(job.cost,6),"geometry":record["geometry"]},ensure_ascii=False),flush=True)
        return job, record

    async def prefix(self, model, family):
        from app.v1_store import get_v1_store
        folder = self.root / model["label"] / family / "prefix"
        if folder.exists():
            raise FileExistsError("Paid prefix already exists; never rerun it silently")
        folder.mkdir(parents=True)
        s = spec(family)
        project = get_v1_store().create_project("EXP004 prefix " + family).id
        for name, params in initial_commands(s):
            await self.host(project,{"action":"component.create","params":params},name)
        initial = list(get_v1_store().latest(project).components)
        parent = None
        self.reference, self.current_spec = None, s
        for i,(mode,text) in enumerate(timeline(s),1):
            parent, _ = await self.stage(project,parent,mode,text,folder / f"s{i:02d}")
            if not parent.messages:
                raise RuntimeError("Prefix lost its actual conversation; do not fabricate history")
        current = get_v1_store().latest(project).components
        check = evaluate(current,initial,s)
        auxiliary_before = next(c for c in initial if c.name=="auxiliary")
        auxiliary_after = next((c for c in current if c.name=="auxiliary"),None)
        auxiliary_restored = auxiliary_after is not None and all(abs(a-b)<1e-6 for a,b in zip(
            auxiliary_before.bbox_min+auxiliary_before.bbox_max, auxiliary_after.bbox_min+auxiliary_after.bbox_max))
        eligible = (check.get("at_home") and check.get("valid_structure") and auxiliary_restored
                    and not set(check.get("protected_changed",[]))-{"auxiliary"})
        result = {"eligible":bool(eligible),"oracle":check,"manifest":serialize(current),
                  "parent_id":parent.id,"parent_messages":parent.messages,"camera":parent.camera,
                  "version":get_v1_store().latest(project).number,
                  "versions":[{"number":v.number,"message":v.message,"components":serialize(v.components)}
                              for v in get_v1_store().list_versions(project)]}
        dump(folder / "checkpoint.json",result)
        return result

    async def refresh(self, manifest, s):
        started = time.monotonic()
        info = await self.query(manifest,{"kind":"inspect"})
        catalog = {}
        for component in manifest:
            catalog[component.name] = {kind:await self.query(manifest,{"kind":kind,"component":component.hash_sat,"limit":512,"offset":0})
                                       for kind in ("faces","edges")}
        pairs = []
        for a,b in itertools.combinations(manifest,2):
            args = {"a":a.hash_sat,"b":b.hash_sat}
            pairs.append({"a":a.name,"b":b.name,"distance":await self.query(manifest,{"kind":"distance",**args}),
                          "interference":await self.query(manifest,{"kind":"interference",**args})})
        by_name = {c.name:c for c in manifest}
        movable = by_name["movable"]
        half = (movable.bbox_max[0]-movable.bbox_min[0])/2
        public_gap = by_name["obstacle"].bbox_min[0]-(s["target"]+half)
        return {"inspect":info,"topology":catalog,"pairs":pairs,
                "candidate_signed_gap_from_current_bbox":public_gap,"elapsed_s":time.monotonic()-started}

    async def branch(self, model, family, event, policy, prefix):
        from app.v1_store import get_v1_store, SceneComponent
        folder = self.root / model["label"] / family / f"{event}-{policy}"
        folder.mkdir(parents=True,exist_ok=False)
        if not prefix["eligible"]:
            dump(folder / "record.json",{"status":"prefix_failed","allocated":True})
            return
        s = spec(family)
        if event == "E2":
            s["threshold"] = 2.0
        project = get_v1_store().create_project(f"EXP004 {family}-{event}-{policy}").id
        parts = [SceneComponent(**c) for c in prefix["manifest"]]
        for version in sorted(prefix["versions"],key=lambda v:v["number"]):
            get_v1_store().commit_version(project,message=version["message"],author="EXP004 exact prefix fork",
                                         components=[SceneComponent(**c) for c in version["components"]],
                                         base_version=version["number"]-1 or None)
        base=prefix["version"]
        parent = self.editor.Job(uuid4().hex,project,base,"Fork of actual ten-stage prefix",[],parts,list(parts),
                                 status="completed",version=base,framework="agenticcadedit-acis",mode="inspect",
                                 messages=copy.deepcopy(prefix["parent_messages"]),camera=copy.deepcopy(prefix["camera"]))
        parent.conversation_id = parent.id
        if policy == "F":
            parent.messages = [{"role":"user","content":text} for _,text in timeline(s)]
            parent.messages.append({"role":"user","content":"旧工具观测与模型推断已清除，所有用户要求仍保留。请按需查询当前模型。"})
        self.editor.jobs[parent.id] = parent
        change = human_offset(s,event)
        if change:
            name,offset = change
            component = next(c for c in parts if c.name==name)
            await self.host(project,{"action":"component.translate","params":{"component":component.hash_sat,"offset":offset}})
        self.reference = list(get_v1_store().latest(project).components)
        self.current_spec = s
        dump(folder / "before.json",{"event":event,"policy":policy,"source_prefix":prefix["parent_id"],
                                     "manifest":serialize(self.reference),"truth":evaluate(self.reference,self.reference,s)})
        records = []
        for stage in (11,12):
            if policy == "A":
                current = get_v1_store().latest(project).components
                refreshed = await self.refresh(current,s)
                dump(folder / f"refresh-s{stage}.json",refreshed)
                parent.messages.append({"role":"user","content":"宿主完整任务相关查询刷新（非目标答案）："+json.dumps(refreshed,ensure_ascii=False)})
            text = instruction(s,stage)
            if event == "E2" and stage == 11:
                text += "\n合法需求更新：从现在起（包括后续阶段），最低间隙永久更新为 2 mm，明确替代先前的 3 mm 条件，直到用户再次修改；其他约束与目标保持不变。"
            parent,record = await self.stage(project,parent,"edit",text,folder / f"s{stage}")
            records.append(record)
        unsafe = []
        for stage in (11,12):
            events_path = folder / f"s{stage}" / "tool-events.json"
            events = json.loads(events_path.read_text()) if events_path.exists() else []
            unsafe.extend(e for e in events if e.get("oracle",{}).get("unsafe"))
        dump(folder / "record.json",{"status":"completed","allocated":True,
                                     "first":records[0]["geometry"],"final":records[-1]["geometry"],
                                     "ever_visible_unsafe":bool(unsafe),"unsafe_boundaries":len(unsafe),
                                     "cost":sum(r["charged_or_reserved_cny"] for r in records),
                                     "calls":sum(len(r["ledger_entries"]) for r in records),
                                     "attribution":"pending_trace_review"})

    async def run(self):
        from app.config import settings
        from app.component_store import ComponentStore, set_component_store
        from app.v1_store import initialize_v1_store, shutdown_v1_store
        from app.worker_pool import get_worker_pool
        from app.ai_budget import SharedBudget
        settings.ai_config = self.plan["config"]["ai_config"]
        base = self.editor.configuration()
        settings.component_root = str(self.root / "assets")
        set_component_store(ComponentStore(self.root / "assets"))
        initialize_v1_store(memory=True)
        await self.install_observers()
        try:
            for model in self.plan["models"]:
                runtime = dict(base,pricing=model["pricing"],max_calls=self.plan["segment_calls"],max_tokens=8192,
                               job_soft_limit_cny=20,trace_dir=str(self.root / "traces"))
                runtime["budget"] = SharedBudget(base["budget"].path,base["budget"].limit,model["pricing"])
                self.editor.configuration = lambda runtime=runtime: runtime
                prefixes = {family:await self.prefix(model,family) for family in self.plan["families"]}
                queue = list(itertools.product(self.plan["families"],EVENTS,POLICIES))
                random.Random(self.plan["seed"]).shuffle(queue)
                dump(self.root / model["label"] / "queue.json",queue)
                for family,event,policy in queue:
                    try:
                        await self.branch(model,family,event,policy,prefixes[family])
                    except BaseException as error:
                        folder = self.root / model["label"] / family / f"{event}-{policy}"
                        dump(folder / "exception.json",{"type":type(error).__name__,"detail":str(error),"traceback":traceback.format_exc()})
                        if isinstance(error,(KeyboardInterrupt,asyncio.CancelledError)):
                            raise
                dump(self.root / model["label"] / "budget-after.json",runtime["budget"].summary())
        finally:
            self.editor.official_model = self.original_model
            self.editor.configuration = self.original_configuration
            self.bridge.AgenticBridge.dispatch = self.original_dispatch
            dump(self.root / "budget-after.json",base["budget"].summary())
            await get_worker_pool().shutdown()
            shutdown_v1_store()
            set_component_store(None)


def summarize(root):
    plan = json.loads((root / "run.json").read_text())
    rows = []
    for model in plan["models"]:
        for policy in POLICIES:
            records = []
            for family,event in itertools.product(plan["families"],EVENTS):
                p = root / model["label"] / family / f"{event}-{policy}" / "record.json"
                records.append(json.loads(p.read_text()) if p.exists() else {"status":"not_executed"})
            rows.append({"model":model["label"],"policy":policy,"allocated":len(records),
                         "completed":sum(r["status"]=="completed" for r in records),
                         "first_success":sum(bool(r.get("first",{}).get("task_success")) for r in records),
                         "final_success":sum(bool(r.get("final",{}).get("task_success")) for r in records),
                         "ever_visible_unsafe":sum(r.get("ever_visible_unsafe",False) for r in records),
                         "cost":sum(r.get("cost",0) for r in records),"calls":sum(r.get("calls",0) for r in records)})
    dump(root / "summary.json",rows)
    print(json.dumps(rows,ensure_ascii=False,indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command",choices=["register","run","summarize"])
    parser.add_argument("--run",type=Path,required=True)
    parser.add_argument("--config",type=Path)
    parser.add_argument("--execute",action="store_true")
    args = parser.parse_args()
    root = args.run.resolve()
    if args.command=="register": register(root,args.config)
    elif args.command=="summarize": summarize(root)
    elif not args.execute: print("Dry plan only. Paid inference requires --execute.")
    else: asyncio.run(Runner(root,verify(root)).run())


if __name__ == "__main__":
    main()
