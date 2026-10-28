// v1 API 客户端（V3_PLAN.md §2 契约）。同源部署走反向代理；独立部署时
// 设置 VITE_API_BASE。
const BASE = import.meta.env.VITE_API_BASE ?? "";

export function setApiPassword(password: string) {
  sessionStorage.setItem("dbcad.password", password);
}

export function authHeaders(): Record<string, string> {
  const password = sessionStorage.getItem("dbcad.password");
  return password ? { "X-API-Password": password } : {};
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: unknown,
  ) {
    super(`API ${status}: ${JSON.stringify(detail)}`);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      ...authHeaders(),
      ...init?.headers,
      ...(init?.body && !(init.body instanceof FormData)
        ? { "Content-Type": "application/json" }
        : {}),
    },
  });
  if (!response.ok) {
    let detail: unknown = response.statusText;
    try {
      detail = (await response.json())?.detail ?? detail;
    } catch {
      /* 非 JSON 错误体 */
    }
    throw new ApiError(response.status, detail);
  }
  return response.status === 204 ? (undefined as T) : ((await response.json()) as T);
}

export interface ProjectInfo {
  id: string;
  name: string;
  created_at: string;
  updated_at: string;
  head_version: number | null;
}

export interface SceneComponent {
  component_id: string;
  name: string;
  sat: string;
  glb: string;
  color: number[];
  bbox: { min: number[]; max: number[] };
  triangles: number;
  vertices: number;
}

export interface Scene {
  project_id: string;
  version: number;
  components: SceneComponent[];
}

export interface VersionInfo {
  version: number;
  message: string;
  author: string;
  created_at: string;
  base_version: number | null;
  component_count: number;
}

export interface VersionList {
  versions: VersionInfo[];
  tags: { name: string; version: number }[];
}

export interface CommandSchemas {
  schema: string;
  commands: Record<string, { description: string; params: Record<string, { type: string; required?: boolean; enum?: string[] }> }>;
}

// 本地身份：sessionStorage 按标签页隔离——同浏览器多开两个标签即是两个
// 协作者（双端回归场景的最小演示形态）。
export const userName =
  sessionStorage.getItem("dbcad.user") ??
  (() => {
    const name = `user-${Math.random().toString(36).slice(2, 6)}`;
    sessionStorage.setItem("dbcad.user", name);
    return name;
  })();

export interface AIStatus {
  enabled: boolean; model: string | null;
  framework?: string; framework_label?: string;
  budget: { charged_or_reserved_cny: number; limit_cny: number } | null;
}
export interface ProviderModelEntry {
  vlm?: boolean | null; probe_error?: string | null; probed_at?: number;
  input_cny_per_million?: number; output_cny_per_million?: number; context_tokens?: number;
}
export interface AIProvider {
  id: string; label: string; base_url: string; created: number | null;
  budget_limit_cny: number | null; key_hint: string; editable: boolean;
  models: Record<string, ProviderModelEntry>; default_model?: string | null;
}
export interface ProviderModel {
  id: string; vlm: boolean | null; probe_error: string | null;
  pricing: { input_cny_per_million: number; output_cny_per_million: number; context_tokens: number } | null;
}
export interface AIStartExtra {
  parent_job_id?: string; images?: string[]; provider_id?: string; model?: string; mode?: "edit" | "inspect";
}
export interface AIJob {
  id: string; project_id: string; base_version: number | null;
  status: "running" | "paused" | "ready" | "applied" | "completed" | "failed" | "cancelled";
  framework?: string; parent_job_id?: string | null; conversation_id?: string; mode?: "edit" | "inspect";
  summary: string; calls: number; cancel_requested?: boolean; estimated_cny: number; scene: Scene | null;
  segment_calls?: number; segment_limit?: number; pause_reason?: string | null; can_resume?: boolean;
  instruction?: string;
  trace: { step: number; tool: string; outcome?: string; detail: string }[];
}

export const api = {
  aiStatus: (provider?: string, model?: string) => request<AIStatus>("/api/v1/ai/status" +
    (model ? `?provider_id=${encodeURIComponent(provider ?? "default")}&model=${encodeURIComponent(model)}` : "")),
  aiProviders: () => request<{ providers: AIProvider[] }>("/api/v1/ai/providers"),
  aiSaveProvider: (payload: { id?: string; label?: string; base_url: string; api_key?: string; budget_limit_cny?: number }) =>
    request<AIProvider>("/api/v1/ai/providers", { method: "PUT", body: JSON.stringify(payload) }),
  aiModels: (pid: string) => request<{ models: ProviderModel[] }>(`/api/v1/ai/providers/${pid}/models`),
  aiProbe: (pid: string, model: string) =>
    request<{ model: string; vlm: boolean; probe_error?: string | null }>(`/api/v1/ai/providers/${pid}/models/${encodeURIComponent(model)}/probe`, { method: "POST" }),
  aiModelPricing: (pid: string, model: string, pricing: { input_cny_per_million: number; output_cny_per_million: number; context_tokens?: number }) =>
    request(`/api/v1/ai/providers/${pid}/models/${encodeURIComponent(model)}`, { method: "PUT", body: JSON.stringify(pricing) }),
  aiStart: (id: string, instruction: string, base_version: number | null, selected: string[], extra?: AIStartExtra) =>
    request<AIJob>(`/api/v1/projects/${id}/ai/jobs`, { method: "POST", body: JSON.stringify({ instruction, base_version, selected, ...extra }) }),
  aiJob: (id: string, job: string) => request<AIJob>(`/api/v1/projects/${id}/ai/jobs/${job}`),
  aiCancel: (id: string, job: string) => request<AIJob>(`/api/v1/projects/${id}/ai/jobs/${job}/cancel`, {method: "POST"}),
  aiResume: (id: string, job: string, instruction: string) =>
    request<AIJob>(`/api/v1/projects/${id}/ai/jobs/${job}/resume`, {method: "POST", body: JSON.stringify({instruction})}),
  aiApply: (id: string, job: string) => request<AIJob>(`/api/v1/projects/${id}/ai/jobs/${job}/apply`, {method: "POST", body: JSON.stringify({author: userName})}),
  projects: () => request<ProjectInfo[]>("/api/v1/projects"),
  createProject: (name: string) => request<ProjectInfo>("/api/v1/projects", {
    method: "POST", body: JSON.stringify({ name }),
  }),
  openProject: (id: string) => request<ProjectInfo>(`/api/v1/projects/${id}`),
  versions: (id: string) => request<VersionList>(`/api/v1/projects/${id}/versions`),
  scene: (id: string, version?: number) =>
    request<Scene>(`/api/v1/projects/${id}/scene${version ? `?v=${version}` : ""}`),
  assetUrl: (hash: string, kind: "sat" | "glb") => `${BASE}/api/v1/assets/${hash}.${kind}`,
  schemas: () => request<CommandSchemas>("/api/v1/schema/commands"),
  command: (id: string, payload: Record<string, unknown>) =>
    request<{ version: number; components: SceneComponent[] }>(`/api/v1/projects/${id}/commands`, {
      method: "POST",
      body: JSON.stringify({ author: userName, ...payload }),
    }),
  undo: (id: string) => request<Record<string, unknown>>(`/api/v1/projects/${id}/undo`, { method: "POST" }),
  redo: (id: string) => request<Record<string, unknown>>(`/api/v1/projects/${id}/redo`, { method: "POST" }),
  importSat: async (projectId: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    form.append("project_id", projectId);
    form.append("author", userName);
    const response = await fetch(`${BASE}/api/v1/files/import`, { method: "POST", body: form, headers: authHeaders() });
    if (!response.ok) throw new ApiError(response.status, (await response.json())?.detail);
    return (await response.json()) as { project_id: string; version: number };
  },
  exportUrl: (projectId: string, fmt: "sat" | "glb" | "stl", version?: number) =>
    `${BASE}/api/v1/projects/${projectId}/files/export?fmt=${fmt}${version ? `&v=${version}` : ""}`,
  download: async (projectId: string, fmt: "sat" | "glb" | "stl", version?: number) => {
    const response = await fetch(api.exportUrl(projectId, fmt, version), { headers: authHeaders() });
    if (!response.ok) throw new ApiError(response.status, (await response.json())?.detail);
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement("a");
    link.href = url;
    link.download = `${projectId}${version ? `-v${version}` : ""}.${fmt}`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  },
  eventsUrl: (projectIds: string[]) =>
    `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${BASE}/api/v1/events?projects=${projectIds.join(",")}&user=${encodeURIComponent(userName)}`,
};
