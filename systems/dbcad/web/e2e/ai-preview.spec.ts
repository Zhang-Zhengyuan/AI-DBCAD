import { test, expect } from "@playwright/test";

test("模型澄清问题可显示，人工填写回答后沿用同一任务", async ({ page }) => {
  const headers = {"X-API-Password":"dbcad-e2e-access"};
  const project = await (await page.request.post('/api/v1/projects', {headers,data:{name:`ai-question-${Date.now()}`}})).json();
  const scene = await (await page.request.post(`/api/v1/projects/${project.id}/commands`,{headers,
    data:{action:'component.create',params:{kind:'cube',width:8,height:8,depth:8}}})).json();
  const id = 'c'.repeat(32);
  let resumed = false;
  await page.route('**/api/v1/ai/status**', route => route.fulfill({json:{enabled:true,model:'default-model',framework:'agenticcadedit-acis',budget:null}}));
  await page.route('**/api/v1/ai/providers**', route => route.fulfill({json:{providers:[],models:[]}}));
  await page.route(`**/api/v1/projects/${project.id}/ai/jobs**`, async route => {
    if (route.request().method()==='POST') {
      expect(route.request().url()).toContain('/resume');
      expect(route.request().postDataJSON()).toEqual({instruction:'直径 12 mm'});
      resumed = true;
    }
    await route.fulfill({json:{id,project_id:project.id,base_version:1,status:resumed?'completed':'paused',
      pause_reason:resumed?null:'no_tool_call',can_resume:!resumed,framework:'agenticcadedit-acis',
      summary:resumed?'已记录尺寸':'出水口直径需要多少毫米？',calls:1,estimated_cny:.01,trace:[],
      scene:{project_id:project.id,version:1,components:scene.components}}});
  });
  await page.addInitScript(({pid,id}) => {sessionStorage.setItem('dbcad.password','dbcad-e2e-access');sessionStorage.setItem(`inferracad.ai.${pid}`,id);}, {pid:project.id,id});
  await page.goto('/web/');
  await page.getByRole('listitem').filter({hasText:project.name}).click();
  await expect(page.getByText('出水口直径需要多少毫米？')).toBeVisible();
  await expect(page.getByRole('button',{name:'回答并继续',exact:true})).toBeDisabled();
  await page.getByLabel('回答或补充要求（必填）').fill('直径 12 mm');
  await page.getByRole('button',{name:'回答并继续',exact:true}).click();
  await expect.poll(() => resumed).toBe(true);
  expect((await (await page.request.get(`/api/v1/projects/${project.id}`,{headers})).json()).head_version).toBe(1);
});

test("新模型能先填价检测，再启用运行", async ({ page }) => {
  const headers = {"X-API-Password":"dbcad-e2e-access"};
  const project = await (await page.request.post('/api/v1/projects', {headers,data:{name:`ai-provider-${Date.now()}`}})).json();
  let pricing: object | null = null;
  let vlm: boolean | null = null;
  await page.route('**/api/v1/ai/status**', route => route.fulfill({json:{enabled:true,model:'default-model',framework:'agenticcadedit-acis',budget:{charged_or_reserved_cny:0,limit_cny:300}}}));
  await page.route('**/api/v1/ai/providers**', async route => {
    const url = route.request().url();
    if (url.endsWith('/probe')) { expect(pricing).not.toBeNull(); vlm = true; await route.fulfill({json:{vlm:true}}); }
    else if (route.request().method() === 'PUT') { pricing = {context_tokens:262144,...route.request().postDataJSON()}; await route.fulfill({json:{pricing}}); }
    else if (url.endsWith('/models')) await route.fulfill({json:{models:[{id:'new-vision',vlm,pricing}]}});
    else await route.fulfill({json:{providers:[{id:'default',label:'默认',default_model:'default-model'}, {id:'custom',label:'实验接口',base_url:'https://unit.invalid',budget_limit_cny:10}]}});
  });
  await page.addInitScript(() => sessionStorage.setItem('dbcad.password','dbcad-e2e-access'));
  await page.goto('/web/');
  await page.getByRole('listitem').filter({hasText:project.name}).click();
  await page.locator('.ai-settings > summary').click();
  await page.getByLabel('Provider',{exact:true}).selectOption('custom');
  await page.getByLabel('待配置或运行的模型').selectOption('new-vision');
  await page.getByLabel('编辑要求',{exact:true}).fill('建立一个立方体');
  await expect(page.getByRole('button',{name:'生成预览',exact:true})).toBeDisabled();
  await expect(page.getByRole('button',{name:'检测 VLM'})).toBeDisabled();
  await page.locator('.ai-settings-row input').nth(0).fill('3');
  await page.locator('.ai-settings-row input').nth(1).fill('18');
  await page.getByRole('button',{name:'保存单价'}).click();
  await expect(page.getByRole('button',{name:'检测 VLM'})).toBeEnabled();
  await page.getByRole('button',{name:'检测 VLM'}).click();
  await expect(page.getByRole('button',{name:'生成预览',exact:true})).toBeEnabled();
});

test("人工改版本后网页新阶段继承父对话，也可显式新建", async ({ page }) => {
  const headers = {"X-API-Password":"dbcad-e2e-access"};
  const project = await (await page.request.post('/api/v1/projects', {headers,data:{name:`ai-parent-${Date.now()}`}})).json();
  for (let i=0;i<2;i++) await page.request.post(`/api/v1/projects/${project.id}/commands`,{headers,data:{action:'component.create',params:{kind:'cube',width:8,height:8,depth:8,position:[20*i,0,0]}}});
  const parent = 'a'.repeat(32);
  let state = 'applied';
  const observed: Record<string,unknown>[] = [];
  await page.route('**/api/v1/ai/status**', route => route.fulfill({json:{enabled:true,model:'default-model',framework:'agenticcadedit-acis',budget:null}}));
  await page.route('**/api/v1/ai/providers**', route => route.fulfill({json:{providers:[],models:[]}}));
  await page.route(`**/api/v1/projects/${project.id}/ai/jobs**`, async route => {
    if (route.request().url().endsWith('/cancel')) state='cancelled';
    else if (route.request().method()==='POST') { observed.push(route.request().postDataJSON()); state='failed'; }
    await route.fulfill({json:{id:parent,project_id:project.id,base_version:1,status:state,framework:'agenticcadedit-acis',summary:'已完成',calls:1,estimated_cny:.01,trace:[],scene:null}});
  });
  await page.addInitScript(({pid,id}) => {sessionStorage.setItem('dbcad.password','dbcad-e2e-access');sessionStorage.setItem(`inferracad.ai.${pid}`,id);}, {pid:project.id,id:parent});
  await page.goto('/web/');
  await page.getByRole('listitem').filter({hasText:project.name}).click();
  await expect(page.getByLabel('对话方式')).toHaveValue('continue');
  await page.getByLabel('编辑要求',{exact:true}).fill('保持先前要求，继续修改');
  await page.getByRole('button',{name:'生成预览',exact:true}).click();
  await expect.poll(() => observed.length).toBe(1);
  expect(observed[0].parent_job_id).toBe(parent);
  expect(observed[0].base_version).toBe(2);
  state='applied';
  await page.reload();
  await page.getByRole('listitem').filter({hasText:project.name}).click();
  await page.getByLabel('对话方式').selectOption('new');
  await page.getByLabel('编辑要求',{exact:true}).fill('开始独立任务');
  await page.getByRole('button',{name:'生成预览',exact:true}).click();
  await expect.poll(() => observed.length).toBe(2);
  expect(observed[1].parent_job_id).toBeUndefined();
});

// Provider responses are fixed here; geometry and version APIs use the real ACIS
// worker. Paid provider/native-loop validation is recorded separately.
test("AI 预览面板：显示候选、放弃恢复原模型，凭据留在服务端", async ({ page }) => {
  const headers = {"X-API-Password":"dbcad-e2e-access"};
  const created = await page.request.post('/api/v1/projects', {headers,data:{name:`ai-panel-${Date.now()}`}});
  const project = await created.json();
  const version = await page.request.post(`/api/v1/projects/${project.id}/commands`,{headers,data:{action:'component.create',params:{kind:'cube',width:12,height:12,depth:12}}});
  const data = await version.json();
  let state = 'running';
  const job = () => ({id:'ui-test',project_id:project.id,base_version:1,status:state,summary:'已生成修改预览',calls:2,estimated_cny:.02,trace:[],scene:state==='ready'?{project_id:project.id,version:1,components:data.components}:null});
  await page.route('**/api/v1/ai/status', route => route.fulfill({json:{enabled:true,model:'test-provider',budget:{charged_or_reserved_cny:.02,limit_cny:100}}}));
  await page.route(`**/api/v1/projects/${project.id}/ai/jobs**`, async route => {
    const body = route.request().postDataJSON();
    if (route.request().url().endsWith('/cancel')) state='cancelled';
    else if (route.request().method()==='POST') {
      expect(Object.keys(body).sort()).toEqual(['base_version','instruction','selected']);
      expect(body.base_version).toBe(1);
    } else state='ready';
    await route.fulfill({json:job()});
  });
  await page.addInitScript(() => sessionStorage.setItem('dbcad.password','dbcad-e2e-access'));
  await page.goto('/web/');
  await page.getByRole('listitem').filter({hasText:project.name}).click();
  await page.getByLabel('编辑要求',{exact:true}).fill('把底板沿 X 轴移动 10');
  await page.getByRole('button',{name:'生成预览'}).click();
  await expect(page.getByText('正在查看 AI 预览 · 尚未应用')).toBeVisible();
  await expect(page.getByRole('button',{name:'应用到项目'})).toBeVisible();
  const head = await page.request.get(`/api/v1/projects/${project.id}`,{headers});
  expect((await head.json()).head_version).toBe(1);
  await page.getByRole('button',{name:'放弃预览'}).click();
  await expect(page.getByText('正在查看 AI 预览 · 尚未应用')).toHaveCount(0);
  await expect(page.locator('canvas')).toBeVisible();
});

test("调用触顶保留中间模型，刷新后同任务继续并保留累计费用", async ({ page }) => {
  const headers = {"X-API-Password":"dbcad-e2e-access"};
  const project = await (await page.request.post('/api/v1/projects', {headers,data:{name:`ai-resume-${Date.now()}`}})).json();
  const data = await (await page.request.post(`/api/v1/projects/${project.id}/commands`, {headers,
    data:{action:'component.create',params:{kind:'cube',width:8,height:8,depth:8}}})).json();
  let state = 'running';
  let resumed = false;
  let failNextGet = false;
  let starts = 0;
  const job = () => ({id:'resume-test',project_id:project.id,base_version:1,status:state,
    instruction:'建立有孔底板',summary:state==='paused'?'本段调用已用完，中间预览已保留':'已完成',
    calls:resumed?23:20,segment_calls:resumed?3:20,segment_limit:20,
    pause_reason:state==='paused'?'round_limit':null,can_resume:state==='paused',
    estimated_cny:resumed?.15:.12,trace:[],scene:['paused','ready'].includes(state)?{project_id:project.id,version:1,components:data.components}:null});
  await page.route('**/api/v1/ai/status', route => route.fulfill({json:{enabled:true,model:'test-provider',budget:{charged_or_reserved_cny:.15,limit_cny:100}}}));
  await page.route(`**/api/v1/projects/${project.id}/ai/jobs**`, async route => {
    if (route.request().method()==='GET' && failNextGet) {
      failNextGet = false;
      await route.fulfill({status:503,json:{detail:'temporarily unavailable'}});
      return;
    }
    if (route.request().url().endsWith('/resume')) {
      expect(route.request().postDataJSON()).toEqual({instruction:'保留主体，继续打孔'});
      resumed = true; state = 'running';
    } else if (route.request().url().endsWith('/cancel')) state='cancelled';
    else if (route.request().method()==='POST') { starts++; state='running'; }
    else state = resumed?'ready':'paused';
    await route.fulfill({json:job()});
  });
  await page.addInitScript(() => sessionStorage.setItem('dbcad.password','dbcad-e2e-access'));
  await page.goto('/web/');
  await page.getByRole('listitem').filter({hasText:project.name}).click();
  await page.getByLabel('编辑要求',{exact:true}).fill('建立有孔底板');
  await page.getByRole('button',{name:'生成预览',exact:true}).click();
  await expect(page.getByText('正在查看未完成预览 · 已暂停')).toBeVisible();
  await expect(page.getByRole('button',{name:'应用到项目'})).toHaveCount(0);
  await expect(page.getByRole('button',{name:'生成预览',exact:true})).toBeDisabled();
  failNextGet = true;
  await page.reload();
  await page.getByRole('listitem').filter({hasText:project.name}).click();
  await expect(page.getByText('读取任务暂时失败，恢复记录已保留，请刷新重试。')).toBeVisible();
  expect(await page.evaluate(id => sessionStorage.getItem(`inferracad.ai.${id}`), project.id)).toBe('resume-test');
  await page.reload();
  await page.getByRole('listitem').filter({hasText:project.name}).click();
  await expect(page.getByText('正在查看未完成预览 · 已暂停')).toBeVisible();
  await expect(page.getByLabel('编辑要求',{exact:true})).toHaveValue('建立有孔底板');
  await page.getByLabel('续作补充要求（可选）').fill('保留主体，继续打孔');
  await expect(page.getByLabel('续作补充要求（可选）')).toHaveValue('保留主体，继续打孔');
  await page.getByRole('button',{name:'继续生成',exact:true}).click();
  await expect(page.getByText('正在查看 AI 预览 · 尚未应用')).toBeVisible();
  await expect(page.getByRole('button',{name:'应用到项目'})).toBeVisible();
  expect(starts).toBe(1); // Continue does not silently create another paid job.
  expect((await (await page.request.get(`/api/v1/projects/${project.id}`,{headers})).json()).head_version).toBe(1);
  await page.getByRole('button',{name:'放弃预览'}).click();
  await expect(page.getByText('正在查看 AI 预览 · 尚未应用')).toHaveCount(0);
});

test("项目版本变化后不允许直接继续旧的未完成预览", async ({ page }) => {
  const headers = {"X-API-Password":"dbcad-e2e-access"};
  const project = await (await page.request.post('/api/v1/projects', {headers,data:{name:`ai-stale-${Date.now()}`}})).json();
  const endpoint = `/api/v1/projects/${project.id}/commands`;
  const first = await (await page.request.post(endpoint,{headers,data:{action:'component.create',params:{kind:'cube',width:8,height:8,depth:8}}})).json();
  await page.request.post(endpoint,{headers,data:{action:'component.create',params:{kind:'sphere',radius:1,position:[10,0,0]}}});
  await page.route('**/api/v1/ai/status', route => route.fulfill({json:{enabled:true,model:'test',budget:null}}));
  await page.route(`**/api/v1/projects/${project.id}/ai/jobs/stale-test`, route => route.fulfill({json:{
    id:'stale-test',project_id:project.id,base_version:1,status:'paused',can_resume:true,pause_reason:'round_limit',
    summary:'已暂停',calls:20,estimated_cny:.1,trace:[],scene:{project_id:project.id,version:1,components:first.components}
  }}));
  await page.addInitScript(({id}) => {
    sessionStorage.setItem('dbcad.password','dbcad-e2e-access');
    sessionStorage.setItem(`inferracad.ai.${id}`,'stale-test');
  }, {id:project.id});
  await page.goto('/web/');
  await page.getByRole('listitem').filter({hasText:project.name}).click();
  await expect(page.getByText('项目版本已改变，请基于最新模型重新发起任务；旧预览不能直接继续。')).toBeVisible();
  await expect(page.getByRole('button',{name:'继续生成',exact:true})).toBeDisabled();
  await expect(page.getByRole('button',{name:'应用到项目'})).toHaveCount(0);
});
