import { test, expect } from "@playwright/test";

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
