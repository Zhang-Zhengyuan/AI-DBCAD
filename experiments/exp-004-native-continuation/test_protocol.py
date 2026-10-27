"""Zero-paid-call checks of task denominators and real ACIS oracle geometry."""
import asyncio
import copy
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
import os
from unittest.mock import patch

from pilot import Runner
from scenes import EVENTS, POLICIES, evaluate, human_offset, initial_commands, spec, timeline
from app.component_store import ComponentStore, set_component_store
from app.v1_store import initialize_v1_store, shutdown_v1_store, get_v1_store
from app.worker_pool import get_worker_pool, set_worker_pool


class ProtocolTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('CAD_AGENT_UPSTREAM') and os.environ.get('CAD_AGENT_PYTHON'), 'Set fixed official environment for full zero-cost integration')
    def test_actual_official_loop_acis_stages_human_change_and_blocked_continuation(self):
        from app import ai_editor as editor
        from app.ai_budget import SharedBudget
        from app.config import settings
        async def check(folder):
            root=Path(folder)
            initialize_v1_store(memory=True)
            set_component_store(ComponentStore(root/'assets'))
            terms={'model':'fixed-test','context_tokens':262144,'input_cny_per_million':3,'output_cny_per_million':18}
            config={'framework':'agenticcadedit-acis','agentic_upstream':os.environ['CAD_AGENT_UPSTREAM'],
                    'render_python':os.environ['CAD_AGENT_PYTHON'],'trace_dir':str(root/'traces'),
                    'budget':SharedBudget(root/'ledger.json',300,terms),'pricing':terms,'max_calls':8}
            counts={}
            async def model(job,config,messages,tools):
                count=counts.get(job.id,0)
                counts[job.id]=count+1
                if count==0:
                    if job.mode=='inspect' or 'blocked' in job.instruction:
                        name,args='inspect',{}
                    else:
                        chosen='auxiliary' if any(f'阶段{i}：' in job.instruction for i in (7,9)) else 'movable'
                        current=next(c for c in job.manifest if c.name==chosen)
                        target=-14 if 'home' in job.instruction or any(f'阶段{i}：' in job.instruction for i in (3,6)) else -12 if '阶段2：' in job.instruction else 0
                        offset=target-(current.bbox_min[0]+current.bbox_max[0])/2
                        vector=[0,2 if '阶段7：' in job.instruction else -2,0] if chosen=='auxiliary' else [offset,0,0]
                        name,args='run_step',{'code':f"part=cad.component('{chosen}'); part=cad.translate(part,{vector})"}
                else:
                    name,args='finish',{'summary':'目标受阻，保持当前模型' if 'blocked' in job.instruction else '已检查或编辑完成',
                                        'success':'blocked' not in job.instruction}
                job.calls+=1
                import json
                return {'model':'fixed-test','usage':{'prompt_tokens':1,'completion_tokens':1},'choices':[{'message':{
                    'role':'assistant','content':None,'tool_calls':[{'id':f'{job.id}-{count}','type':'function','function':{'name':name,'arguments':json.dumps(args)}}]}}]}
            old_config=editor.configuration
            with patch.object(editor,'official_model',model),patch.object(editor,'configuration',lambda:config):
                runner=Runner(root,{'maximum_segments':1})
                await runner.install_observers()
                runner.current_spec=spec('pin')
                try:
                    project=get_v1_store().create_project('zero-cost integration').id
                    for name,params in initial_commands(spec('pin')):
                        await runner.host(project,{'action':'component.create','params':params},name)
                    parent,_=await runner.stage(project,None,'inspect','inspect initial',root/'s1')
                    self.assertEqual(parent.status,'completed')
                    parent,_=await runner.stage(project,parent,'edit','move target',root/'s2')
                    self.assertEqual(parent.status,'applied')
                    parent,_=await runner.stage(project,parent,'edit','move home',root/'s3')
                    obstacle=next(c for c in get_v1_store().latest(project).components if c.name=='obstacle')
                    await runner.host(project,{'action':'component.translate','params':{'component':obstacle.hash_sat,'offset':[-7,0,0]}})
                    runner.reference=list(get_v1_store().latest(project).components)
                    parent,record=await runner.stage(project,parent,'edit','blocked',root/'s4')
                    self.assertEqual(parent.status,'failed')
                    self.assertTrue(record['geometry']['task_success'])
                    parent,_=await runner.stage(project,parent,'inspect','inspect after refusal',root/'s5')
                    self.assertEqual(parent.status,'completed')
                    self.assertGreater(len(parent.messages),20)
                    prefix=await runner.prefix({'label':'fixed-zero-cost'},'pin')
                    self.assertTrue(prefix['eligible'])
                    await runner.branch({'label':'fixed-zero-cost'},'pin','E3','N',prefix)
                    import json
                    unsafe=json.loads((root/'fixed-zero-cost/pin/E3-N/record.json').read_text())
                    self.assertTrue(unsafe['ever_visible_unsafe'])
                    self.assertFalse(unsafe['first']['task_success'])
                    await runner.branch({'label':'fixed-zero-cost'},'pin','E2','A',prefix)
                    legal=json.loads((root/'fixed-zero-cost/pin/E2-A/record.json').read_text())
                    self.assertFalse(legal['ever_visible_unsafe'])
                    self.assertTrue(legal['first']['task_success'])
                    self.assertEqual(config['budget'].summary()['calls'],0)
                finally:
                    runner.bridge.AgenticBridge.dispatch=runner.original_dispatch
                    await get_worker_pool().shutdown()
                    set_worker_pool(None)
                    set_component_store(None)
                    shutdown_v1_store()
                    editor.jobs.clear()
        with tempfile.TemporaryDirectory(prefix='exp004-loop-') as folder:
            asyncio.run(check(folder))

    def test_allocated_denominators_and_true_user_stages(self):
        self.assertEqual(2*len(EVENTS)*len(POLICIES),30)
        for family in ("pin","slider"):
            stages = timeline(spec(family))
            self.assertEqual(len(stages),10)
            self.assertGreaterEqual(sum(mode=="edit" for mode,_ in stages),5)
            self.assertIn("3 mm",stages[0][1])
            self.assertIn("不得改",stages[0][1])

    def test_real_acis_analytic_clearance_and_collision_all_ten_conditions(self):
        async def check(folder):
            initialize_v1_store(memory=True)
            set_component_store(ComponentStore(Path(folder)/"assets"))
            runner = Runner(Path(folder),{})
            try:
                for family in ("pin","slider"):
                    s = spec(family)
                    for event in EVENTS:
                        s=spec(family)
                        if event=='E2': s['threshold']=2
                        project = get_v1_store().create_project(family+event).id
                        for name,params in initial_commands(s):
                            await runner.host(project,{"action":"component.create","params":params},name)
                        change = human_offset(s,event)
                        if change:
                            name,offset = change
                            component=next(c for c in get_v1_store().latest(project).components if c.name==name)
                            await runner.host(project,{"action":"component.translate","params":{"component":component.hash_sat,"offset":offset}})
                        before=list(get_v1_store().latest(project).components)
                        moving=next(c for c in before if c.name=="movable")
                        await runner.host(project,{"action":"component.translate","params":{"component":moving.hash_sat,"offset":[s['target']-s['home'],0,0]}})
                        actual=get_v1_store().latest(project).components
                        score=evaluate(actual,before,s)
                        measurement=await runner.exact(actual)
                        expected={"E0":5,"E1":5,"E2":2.5,"E3":-2 if family=="pin" else -1,"E4":2.5}[event]
                        self.assertAlmostEqual(score['candidate_signed_gap'],expected)
                        self.assertAlmostEqual(measurement['distance']['distance'],max(0,expected),places=6)
                        self.assertEqual(score['unsafe'],event in {"E3","E4"})
                        self.assertEqual(measurement['interference']['intersection_volume']>1e-6,event=="E3")
                        self.assertEqual(score['geometry_success'],event in {"E0","E1","E2"})
                        if event in {"E3","E4"}:
                            self.assertTrue(evaluate(before,before,s)['geometry_success'])
            finally:
                await get_worker_pool().shutdown()
                set_worker_pool(None)
                set_component_store(None)
                shutdown_v1_store()
        with tempfile.TemporaryDirectory(prefix='exp004-offline-') as folder:
            asyncio.run(check(folder))

    def test_protected_component_or_missing_body_cannot_pass_by_same_bbox(self):
        from app.v1_store import SceneComponent
        def part(name,low,high):
            return SceneComponent(name,name,name=name,bbox_min=low,bbox_max=high)
        original=[part("movable",[-2,-2,2],[2,2,6]),part("obstacle",[7,-4,1],[11,4,7]),
                  part("fixture",[-20,-15,-4],[20,15,-2]),part("auxiliary",[-16,-13,0],[-14,-11,2])]
        self.assertTrue(evaluate(original,original,spec('pin'))['geometry_success'])
        changed=copy.deepcopy(original)
        changed[2]=replace(changed[2],hash_sat='changed-but-same-box')
        self.assertFalse(evaluate(changed,original,spec('pin'))['geometry_success'])
        self.assertFalse(evaluate(changed[:-1],original,spec('pin'))['geometry_success'])


if __name__=='__main__':
    unittest.main()
