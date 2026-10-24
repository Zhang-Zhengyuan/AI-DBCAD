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
