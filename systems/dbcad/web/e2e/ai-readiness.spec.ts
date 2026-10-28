import { test, expect, type Page } from "@playwright/test";

const png = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aT9sAAAAASUVORK5CYII=", "base64");
const readyModel = (id = "Qwen3.8-Max") => ({ id, vlm: true, probe_error: null,
  pricing: { input_cny_per_million: 12, output_cny_per_million: 36, context_tokens: 262144 } });
const status = (model = "configured-default", enabled = true) => ({ enabled, model,
  framework: "agenticcadedit-acis", framework_label: "AgenticCADedit · ACIS", budget: null });
const providers = [{ id: "default", label: "本机默认", default_model: "configured-default" },
  { id: "other", label: "另一个接口", default_model: null }];

async function project(page: Page) {
  return (await page.request.post("/api/v1/projects", { headers: { "X-API-Password": "dbcad-e2e-access" },
    data: { name: `readiness-${Date.now()}` } })).json();
}

async function seed(page: Page, model = "Qwen3.8-Max") {
  await page.addInitScript(model => {
    sessionStorage.setItem("dbcad.password", "dbcad-e2e-access");
    localStorage.setItem("resumacad.pid", "default");
    localStorage.setItem("resumacad.model", model);
  }, model);
}

async function attachReference(page: Page) {
  await page.locator('input[type="file"][accept="image/png,image/jpeg"]').setInputFiles({
    name: "reference.png", mimeType: "image/png", buffer: png,
  });
  await expect(page.getByAltText("参考图片 1")).toBeVisible();
}

test("真实图片附件与已检测模型可提交；未选项目明确提示并保留输入", async ({ page }) => {
  const p = await project(page);
  const starts: Record<string, unknown>[] = [];
  await page.route("**/api/v1/ai/status**", route => route.fulfill({ json: status() }));
  await page.route("**/api/v1/ai/providers**", route => route.fulfill({ json:
    route.request().url().endsWith("/models") ? { models: [readyModel()] } : { providers } }));
  await page.route(`**/api/v1/projects/${p.id}/ai/jobs`, async route => {
    starts.push(route.request().postDataJSON());
    await route.fulfill({ json: { id: "image-test", project_id: p.id, base_version: null,
      status: "completed", framework: "agenticcadedit-acis", summary: "测试接口已接收图片请求",
      calls: 0, estimated_cny: 0, trace: [], scene: null } });
  });
  await seed(page);
  await page.goto("/web/");
  await page.getByLabel("编辑要求", { exact: true }).fill("参考图片建立模型");
  await attachReference(page);
  await expect(page.getByRole("button", { name: "生成预览", exact: true })).toBeDisabled();
  await expect(page.getByText("请先在左侧选择或创建项目。", { exact: true })).toBeVisible();
  await page.getByRole("listitem").filter({ hasText: p.name }).click();
  await expect(page.getByRole("button", { name: "生成预览", exact: true })).toBeEnabled();
  expect(starts).toHaveLength(0);
  await page.getByRole("button", { name: "生成预览", exact: true }).click();
  await expect.poll(() => starts.length).toBe(1);
  expect(starts[0]).toMatchObject({ instruction: "参考图片建立模型", model: "Qwen3.8-Max", provider_id: "default" });
  expect(starts[0].images).toEqual([`data:image/png;base64,${png.toString("base64")}`]);
});

test("刷新模型资格可解除禁用，文字与图片不必重新填写", async ({ page }) => {
  const p = await project(page);
  let ready = false;
  await page.route("**/api/v1/ai/status**", route => route.fulfill({ json: status() }));
  await page.route("**/api/v1/ai/providers**", route => route.fulfill({ json:
    route.request().url().endsWith("/models") ? { models: [{ ...readyModel(), vlm: ready }] } : { providers } }));
  await seed(page);
  await page.goto("/web/");
  await page.getByRole("listitem").filter({ hasText: p.name }).click();
  await page.getByLabel("编辑要求", { exact: true }).fill("参考图片建立模型");
  await attachReference(page);
  await expect(page.getByRole("button", { name: "生成预览", exact: true })).toBeDisabled();
  ready = true; // Another tab/operator completed the probe; no paid probe in this test.
  await page.getByRole("button", { name: "刷新模型状态", exact: true }).click();
  await expect(page.getByRole("button", { name: "生成预览", exact: true })).toBeEnabled();
  await expect(page.getByLabel("编辑要求", { exact: true })).toHaveValue("参考图片建立模型");
  await expect(page.getByAltText("参考图片 1")).toBeVisible();
});

test("旧模型的延迟状态响应不能把新选择的可用模型禁用", async ({ page }) => {
  const p = await project(page);
  let release!: () => void;
  let requested!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  const seen = new Promise<void>(resolve => { requested = resolve; });
  await page.route("**/api/v1/ai/status**", async route => {
    if (!new URL(route.request().url()).searchParams.has("model")) {
      requested(); await pending;
      await route.fulfill({ json: status("configured-default", false) });
    } else await route.fulfill({ json: status("Qwen3.8-Max") });
  });
  await page.route("**/api/v1/ai/providers**", route => route.fulfill({ json:
    route.request().url().endsWith("/models") ? { models: [readyModel()] } : { providers } }));
  await seed(page, "");
  await page.goto("/web/");
  await seen;
  await page.getByRole("listitem").filter({ hasText: p.name }).click();
  await page.locator(".ai-settings > summary").click();
  await page.getByLabel("待配置或运行的模型").selectOption("Qwen3.8-Max");
  await page.getByLabel("编辑要求", { exact: true }).fill("建立一个立方体");
  await expect(page.getByRole("button", { name: "生成预览", exact: true })).toBeEnabled();
  release();
  await page.waitForLoadState("networkidle");
  await expect(page.getByRole("button", { name: "生成预览", exact: true })).toBeEnabled();
});

test("切换接口后旧列表晚返回也不清除新模型选择", async ({ page }) => {
  const p = await project(page);
  let release!: () => void;
  let requested!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  const seen = new Promise<void>(resolve => { requested = resolve; });
  await page.route("**/api/v1/ai/status**", route => route.fulfill({ json: status() }));
  await page.route("**/api/v1/ai/providers**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/default/models")) {
      requested(); await pending;
      await route.fulfill({ json: { models: [readyModel()] } });
    } else await route.fulfill({ json: path.endsWith("/other/models")
      ? { models: [readyModel("other-ready")] } : { providers } });
  });
  await seed(page);
  await page.goto("/web/");
  await seen;
  await page.getByRole("listitem").filter({ hasText: p.name }).click();
  await page.locator(".ai-settings > summary").click();
  await page.getByLabel("Provider", { exact: true }).selectOption("other");
  await page.getByLabel("待配置或运行的模型").selectOption("other-ready");
  await page.getByLabel("编辑要求", { exact: true }).fill("建立一个立方体");
  await expect(page.getByRole("button", { name: "生成预览", exact: true })).toBeEnabled();
  release();
  await page.waitForLoadState("networkidle");
  await expect(page.getByLabel("待配置或运行的模型")).toHaveValue("other-ready");
  await expect(page.getByRole("button", { name: "生成预览", exact: true })).toBeEnabled();
});
