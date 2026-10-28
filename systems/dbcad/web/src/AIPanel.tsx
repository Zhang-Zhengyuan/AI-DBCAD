import { useEffect, useRef, useState } from "react";
import { api, ApiError, type AIJob, type AIStatus, type AIProvider, type ProviderModel } from "./api";
import { useViewer } from "./store";

export function AIPanel() {
  const project = useViewer(s => s.activeProject);
  const selected = useViewer(s => s.selected);
  const preview = useViewer(s => s.aiPreview);
  const [instruction, setInstruction] = useState("");
  const [continuation, setContinuation] = useState("");
  const [job, setJob] = useState<AIJob | null>(null);
  const [status, setStatus] = useState<AIStatus | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const [providers, setProviders] = useState<AIProvider[]>([]);
  const [activePid, setActivePid] = useState(() => localStorage.getItem("resumacad.pid") || "default");
  const [models, setModels] = useState<ProviderModel[] | null>(null);
  const modelsOwner = useRef(activePid);
  const [modelsLoading, setModelsLoading] = useState(false);
  const [modelsError, setModelsError] = useState("");
  const [modelRefresh, setModelRefresh] = useState(0);
  const [statusLoading, setStatusLoading] = useState(true);
  const [statusFor, setStatusFor] = useState("");
  const [statusError, setStatusError] = useState("");
  const [modelSel, setModelSel] = useState(() => localStorage.getItem("resumacad.model") || "");
  const [settingsBusy, setSettingsBusy] = useState(false);
  const [draft, setDraft] = useState({ label: "", base_url: "", api_key: "", limit: "100" });
  const [price, setPrice] = useState({ input: "", output: "" });
  const [images, setImages] = useState<string[]>([]);
  const [imagesLoading, setImagesLoading] = useState(false);
  const settingsRef = useRef<HTMLDetailsElement>(null);
  const [continueConversation, setContinueConversation] = useState(true);
  const [inspectOnly, setInspectOnly] = useState(false);
  useEffect(() => {
    let alive = true;
    void api.aiProviders().then(d => { if (alive) setProviders(d.providers); })
      .catch(e => { if (alive) setError(`读取接口配置失败：${String(e)}`); });
    return () => { alive = false; };
  }, [modelRefresh]);
  useEffect(() => {
    let alive = true;
    // File pickers restore window focus. Refresh in the background without
    // discarding a verified selection for the same provider.
    if (modelsOwner.current !== activePid) {
      setModels(null);
      modelsOwner.current = activePid;
    }
    setModelsLoading(true); setModelsError("");
    void api.aiModels(activePid).then(d => { if (alive) setModels(d.models); })
      .catch(e => { if (alive) setModelsError(String(e)); })
      .finally(() => { if (alive) setModelsLoading(false); });
    localStorage.setItem("resumacad.pid", activePid);
    return () => { alive = false; };
  }, [activePid, modelRefresh]);
  useEffect(() => {
    const refresh = () => setModelRefresh(value => value + 1);
    window.addEventListener("focus", refresh);
    return () => window.removeEventListener("focus", refresh);
  }, []);
  useEffect(() => { if (modelSel) localStorage.setItem("resumacad.model", modelSel); else localStorage.removeItem("resumacad.model"); }, [modelSel]);
  useEffect(() => {
    setJob(null); setError(""); setContinuation(""); setRestoring(false); setContinueConversation(true);
    useViewer.getState().setAIPreview(null);
    if (project) {
      const saved = sessionStorage.getItem(`inferracad.ai.${project.id}`);
      if (saved) {
        setRestoring(true);
        void api.aiJob(project.id, saved).then(fresh => {
          if (useViewer.getState().activeProject?.id !== project.id) return;
          setJob(fresh);
          if (fresh.instruction) setInstruction(fresh.instruction);
          if ((fresh.status === "ready" || fresh.status === "paused") && fresh.scene) useViewer.getState().setAIPreview(fresh.scene);
        }).catch(e => {
          if (e instanceof ApiError && e.status === 404) sessionStorage.removeItem(`inferracad.ai.${project.id}`);
          else if (useViewer.getState().activeProject?.id === project.id) setError("读取任务暂时失败，恢复记录已保留，请刷新重试。");
        }).finally(() => { if (useViewer.getState().activeProject?.id === project.id) setRestoring(false); });
      }
    }
  }, [project?.id]);
  useEffect(() => {
    if (!job || job.status !== "running") return;
    let alive = true;
    const timer = setInterval(() => {
      void api.aiJob(job.project_id, job.id).then(fresh => {
        if (!alive) return;
        setJob(fresh);
        if ((fresh.status === "ready" || fresh.status === "paused") && fresh.scene && useViewer.getState().activeProject?.id === fresh.project_id)
          useViewer.getState().setAIPreview(fresh.scene);
        if (fresh.status !== "running") setModelRefresh(value => value + 1);
      }).catch(e => { if (alive) setError(String(e)); });
    }, 1500);
    return () => { alive = false; clearInterval(timer); };
  }, [job?.id, job?.status]);
  const start = async () => {
    if (!project || startBlockReason) { setError(startBlockReason || "请先选择项目。"); return; }
    setBusy(true); setError("");
    try {
      const fresh = await api.aiStart(project.id, instruction, project.head_version, selected, {
        ...(continueConversation && job?.framework === "agenticcadedit-acis" &&
            ["applied", "completed", "failed", "cancelled"].includes(job.status) ? { parent_job_id: job.id } : {}),
        ...(inspectOnly ? { mode: "inspect" as const } : {}),
        ...(modelSel ? { provider_id: activePid, model: modelSel } : {}),
        ...(images.length ? { images } : {}),
      });
      sessionStorage.setItem(`inferracad.ai.${project.id}`, fresh.id);
      if (useViewer.getState().activeProject?.id === project.id) setJob(fresh);
    } catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  };
  const cancel = async () => {
    if (!job) return;
    setBusy(true);
    try { setJob(await api.aiCancel(job.project_id, job.id)); useViewer.getState().setAIPreview(null); }
    catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  };
  const apply = async () => {
    if (!job) return;
    setBusy(true); setError("");
    try {
      setJob(await api.aiApply(job.project_id, job.id));
      await useViewer.getState().openProject(job.project_id);
      await useViewer.getState().loadProjects();
    } catch (e) { setError(`应用失败，正式模型保持当前版本：${String(e)}`); }
    finally { setBusy(false); }
  };
  const resume = async (notes: string) => {
    if (!job) return;
    setBusy(true); setError("");
    try {
      const fresh = await api.aiResume(job.project_id, job.id, notes);
      if (useViewer.getState().activeProject?.id === fresh.project_id) {
        setJob(fresh); setContinuation("");
      }
    } catch (e) { setError(`继续失败，未完成预览仍保留：${String(e)}`); }
    finally { setBusy(false); }
  };
  const activeProvider = providers.find(p => p.id === activePid);
  const selectedModel = models?.find(m => m.id === modelSel) ?? null;
  const hasPrice = !!selectedModel?.pricing?.input_cny_per_million &&
    !!selectedModel?.pricing?.output_cny_per_million && !!selectedModel?.pricing?.context_tokens;
  const isDefaultModel = activePid === "default" && !!modelSel && modelSel === activeProvider?.default_model;
  const modelEligible = modelSel ? (selectedModel?.vlm === true && hasPrice) || isDefaultModel : activePid === "default";
  const statusRequestKey = modelEligible ? `${activePid}/${modelSel}` : "base-config";
  useEffect(() => {
    let alive = true;
    setStatusLoading(true); setStatusError("");
    void api.aiStatus(modelEligible ? activePid : undefined, modelEligible ? modelSel || undefined : undefined)
      .then(value => { if (alive) { setStatus(value); setStatusFor(statusRequestKey); } })
      .catch(e => { if (alive) { setStatus(null); setStatusFor(statusRequestKey); setStatusError(String(e)); } })
      .finally(() => { if (alive) setStatusLoading(false); });
    return () => { alive = false; };
  }, [activePid, modelSel, modelEligible, modelRefresh, statusRequestKey]);
  useEffect(() => {
    // A remembered model that no longer exists for this provider would keep the
    // start button dead with no visible reason; recover explicitly, not silently.
    if (models && modelSel && !selectedModel && !isDefaultModel) {
      setModelSel("");
      setError(activePid === "default"
        ? "之前选择的模型已不在该接口的模型列表中，已切回本机默认模型。"
        : "之前选择的模型已不在该接口的模型列表中，已清除失效选择；请重新选择模型或切回本机默认。");
    }
  }, [models, modelSel, isDefaultModel, activePid]);
  const saveProvider = async () => {
    setSettingsBusy(true); setError("");
    try {
      const saved = await api.aiSaveProvider({
        label: draft.label.trim() || "自定义接口",
        base_url: draft.base_url.trim().replace(/\/+$/, ""),
        api_key: draft.api_key.trim(),
        ...(status?.budget?.enforced === false ? {} : { budget_limit_cny: Number(draft.limit) || 100 }),
      });
      setProviders((await api.aiProviders()).providers);
      setActivePid(saved.id); setModelSel("");
      setDraft({ label: "", base_url: "", api_key: "", limit: "100" });
    } catch (e) { setError(String(e)); } finally { setSettingsBusy(false); }
  };
  const probe = async (model: string) => {
    setSettingsBusy(true); setError("");
    try {
      await api.aiProbe(activePid, model);
      setModelRefresh(value => value + 1);
    } catch (e) { setError(String(e)); } finally { setSettingsBusy(false); }
  };
  const savePrice = async () => {
    if (!modelSel) return;
    setSettingsBusy(true); setError("");
    try {
      await api.aiModelPricing(activePid, modelSel, {
        input_cny_per_million: Number(price.input), output_cny_per_million: Number(price.output),
      });
      setModelRefresh(value => value + 1);
    } catch (e) { setError(String(e)); } finally { setSettingsBusy(false); }
  };
  const readAsDataURL = (file: File) => new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(new Error(`读取 ${file.name} 失败`));
    reader.readAsDataURL(file);
  });
  const addImages = async (files: FileList | null) => {
    if (!files?.length) return;
    const picked = Array.from(files)
      .filter(f => f.type === "image/png" || f.type === "image/jpeg")
      .slice(0, 3 - images.length);
    for (const file of picked) if (file.size > 2_500_000) setError(`${file.name} 超过 2.5MB，已跳过`);
    const readable = picked.filter(f => f.size <= 2_500_000);
    if (readable.length) {
      setImagesLoading(true);
      try {
        const urls = await Promise.all(readable.map(readAsDataURL));
        setImages(prev => [...prev, ...urls].slice(0, 3));
      } catch (e) { setError(String(e)); }
      finally { setImagesLoading(false); }
    }
  };
  const running = job?.status === "running";
  const paused = job?.status === "paused";
  const needsReply = paused && job.pause_reason === "no_tool_call";
  const stale = !!job && !!project && project.head_version !== job.base_version;
  const modelBlockReason = modelEligible ? null : !modelSel ? "请先在接口与模型中选择模型。"
    : modelsLoading ? "正在读取所选模型的信息，请稍候。"
    : modelsError ? `读取模型状态失败，请刷新模型状态：${modelsError}`
    : !selectedModel ? "所选模型已不可用，请重新选择模型。"
    : !hasPrice ? "所选模型尚未配置单价，请先保存单价，再检测 VLM。"
    : "所选模型尚未通过 VLM 检测，请在接口与模型中完成检测。";
  const startBlockReason = !project ? "请先在左侧选择或创建项目。"
    : restoring ? "正在恢复上次任务，请稍候。"
    : running ? "AI 正在处理当前任务，请等待结果或停止任务。"
    : job?.status === "ready" ? "已有预览等待处理，请先应用到项目或放弃预览。"
    : paused ? "当前任务已暂停，请使用下方的继续或放弃预览。"
    : busy ? "正在处理当前操作，请稍候。"
    : imagesLoading ? "正在读取参考图片，请稍候。"
    : settingsBusy ? "正在保存或检测模型，请稍候。"
    : modelBlockReason
      ?? (statusFor !== statusRequestKey || (statusLoading && !status) ? "正在检查 AI 服务，请稍候。"
        : statusError ? `读取 AI 服务状态失败：${statusError}`
        : !status?.enabled ? "当前 AI 服务未就绪，请检查接口配置或刷新模型状态。"
        : instruction.trim().length < 2 ? "请输入至少两个字符的建模要求。" : null);
  return <section className="ai-panel" aria-label="AI CAD 编辑">
    <div className="ai-heading"><h2>AI CAD</h2><span>ResumaCAD</span></div>
    <p className="ai-model">
      {status?.enabled
        ? `${status.framework_label ? status.framework_label + " · " : ""}${modelSel ? `${modelSel}（已选）` : status.model}`
        : statusLoading ? "正在读取 AI 配置…" : "AI 尚未配置"}
    </p>
    <details className="ai-settings" ref={settingsRef}>
      <summary>接口与模型{modelSel ? `：${modelSel}` : "：跟随本机默认"}</summary>
      <div className="ai-settings-body">
        <label>Provider
          <select aria-label="Provider" value={activePid} onChange={e => { setActivePid(e.target.value); setModelSel(""); }}>
            {providers.map(p => (
              <option key={p.id} value={p.id}>{p.label || p.id}{p.id === "default" ? "（本机 env 默认）" : ""}</option>
            ))}
          </select>
        </label>
        {activeProvider && activeProvider.id !== "default" &&
          <p className="ai-hint">{activeProvider.base_url} · key {activeProvider.key_hint || "（未存）"} · {status?.budget?.enforced === false ? "不设金额上限，费用持续记录" : `provider 累计上限 ¥${activeProvider.budget_limit_cny}，同时受本轮总上限约束`}</p>}
        {models === null ? <p className="ai-hint">正在获取模型列表…</p> : <>
          <label>待配置或运行的模型
            <select aria-label="待配置或运行的模型" value={modelSel} onChange={e => setModelSel(e.target.value)}>
              <option value="">{activePid === "default" ? `（本机默认：${activeProvider?.default_model ?? status?.model ?? "未配置"}）` : "请选择该 provider 的模型"}</option>
              {models.map(m => (
                <option key={m.id} value={m.id}>
                  {m.id}{m.vlm === true ? (m.pricing ? " ✓" : "（缺单价）") : m.vlm === false ? "（不支持图片/工具）" : "（未检测）"}
                </option>
              ))}
            </select>
          </label>
          <div className="ai-settings-row">
            <button type="button" disabled={!modelSel || settingsBusy || (!hasPrice && !isDefaultModel)} onClick={() => void probe(modelSel)}>检测 VLM</button>
            <span className="ai-hint">先填写单价再检测图片与工具能力；检测费用计入账本，通过后可运行。</span>
          </div>
          {modelSel && !hasPrice && !isDefaultModel && (
            <div className="ai-settings-row">
              <span>单价（元/百万 token）：输入
                <input value={price.input} onChange={e => setPrice({ ...price, input: e.target.value })} size={6} inputMode="decimal" />
                输出
                <input value={price.output} onChange={e => setPrice({ ...price, output: e.target.value })} size={6} inputMode="decimal" />
              </span>
              <button type="button" disabled={settingsBusy || !(Number(price.input) > 0) || !(Number(price.output) > 0)} onClick={() => void savePrice()}>保存单价</button>
            </div>
          )}
          {selectedModel?.probe_error && <p className="ai-hint">探测信息：{selectedModel.probe_error.slice(0, 120)}</p>}
        </>}
        <details><summary>新增自定义 provider（base_url + API key）</summary>
          <div className="ai-settings-body">
            <label>名称 <input value={draft.label} onChange={e => setDraft({ ...draft, label: e.target.value })} placeholder="如 我的中转" /></label>
            <label>Base URL（必须 https） <input value={draft.base_url} onChange={e => setDraft({ ...draft, base_url: e.target.value })} placeholder="https://example.com/v1" /></label>
            <label>API Key（只写入本机 0600 文件，保存后不再回显） <input type="password" value={draft.api_key} onChange={e => setDraft({ ...draft, api_key: e.target.value })} /></label>
            {status?.budget?.enforced !== false && <label>Provider 累计子上限（元） <input value={draft.limit} onChange={e => setDraft({ ...draft, limit: e.target.value })} size={6} inputMode="decimal" /></label>}
            <button type="button" className="primary" disabled={settingsBusy || !draft.base_url.trim().startsWith("https://") || draft.api_key.trim().length < 8} onClick={() => void saveProvider()}>保存 provider</button>
          </div>
        </details>
      </div>
    </details>
    <p>用自然语言创建或修改模型；可附带图纸图片看图建模。</p>
    {job?.framework === "agenticcadedit-acis" && ["applied", "completed", "failed", "cancelled"].includes(job.status) && (
      <label>对话方式 <select aria-label="对话方式" value={continueConversation ? "continue" : "new"}
        onChange={e => setContinueConversation(e.target.value === "continue")}>
        <option value="continue">继续当前对话，保留之前要求</option>
        <option value="new">新建独立对话</option>
      </select></label>
    )}
    {status?.framework === "agenticcadedit-acis" && <label><input type="checkbox" checked={inspectOnly}
      disabled={running || busy || paused || job?.status === "ready"} onChange={e => setInspectOnly(e.target.checked)} />只读检查（禁止修改几何）</label>}
    <label htmlFor="ai-instruction">编辑要求</label>
    <textarea id="ai-instruction" value={instruction} onChange={e => setInstruction(e.target.value)} rows={5}
      placeholder="例如：创建一个 60×40×8 的底板，在中央打一个半径 6 的通孔。" disabled={running || busy || paused || job?.status === "ready"} />
    <p className="ai-hint">{selected.length ? `已选中 ${selected.length} 个组件` : "针对当前项目；可先选中需要修改的组件"}。结果先预览，再应用。</p>
    <div className="ai-images">
      {images.map((src, i) => (
        <span key={i} className="ai-image-chip">
          <img src={src} alt={`参考图片 ${i + 1}`} />
          <button type="button" aria-label="移除图片" disabled={running || busy}
            onClick={() => setImages(images.filter((_, j) => j !== i))}>×</button>
        </span>
      ))}
      {images.length < 3 && status?.framework === "agenticcadedit-acis" && (
        <label className="ai-image-add" title="PNG/JPEG，最多 3 张，单张 ≤2.5MB">＋ 图纸/照片
          <input type="file" accept="image/png,image/jpeg" multiple hidden disabled={running || busy}
            onChange={e => { void addImages(e.target.files); e.currentTarget.value = ""; }} />
        </label>
      )}
    </div>
    <div className="ai-actions">
      <button className="primary" title={startBlockReason || undefined} aria-describedby={startBlockReason ? "ai-start-block" : undefined} disabled={!!startBlockReason}
        onClick={() => void start()}>{running ? "AI 正在编辑…" : "生成预览"}</button>
      {running && <button disabled={busy || job.cancel_requested} onClick={() => void cancel()}>停止</button>}
    </div>
    {startBlockReason && <p id="ai-start-block" role="status" className="ai-hint">{startBlockReason}</p>}
    {!running && !busy && <div className="ai-actions">
      <button type="button" disabled={modelsLoading || settingsBusy} onClick={() => setModelRefresh(value => value + 1)}>刷新模型状态</button>
      {modelBlockReason && <>
        <button type="button" onClick={() => { if (settingsRef.current) settingsRef.current.open = true; }}>打开接口与模型</button>
        <button type="button" onClick={() => { setActivePid("default"); setModelSel(""); }}>使用本机默认模型</button>
      </>}
    </div>}
    {paused && <div className="ai-preview-note">
      <strong>{preview ? "正在查看未完成预览 · 已暂停" : "未完成预览已暂停"}</strong>
      <p>{job.summary}</p>
      <p>累计 {job.calls} 次模型调用 · 约 ¥{job.estimated_cny.toFixed(3)}。继续沿用原任务和累计费用，不会从头生成。</p>
      {stale && <p role="alert">项目版本已改变，请基于最新模型重新发起任务；旧预览不能直接继续。</p>}
      <form onSubmit={e => { e.preventDefault(); void resume(String(new FormData(e.currentTarget).get("instruction") ?? "")); }}>
      <label htmlFor="ai-continuation">{needsReply ? "回答或补充要求（必填）" : "续作补充要求（可选）"}</label>
      <textarea id="ai-continuation" name="instruction" value={continuation} onChange={e => setContinuation(e.target.value)} rows={3}
        placeholder={needsReply ? "请回答模型的问题，或纠正其说明后继续。" : "例如：保留主体，接着完成把手和出水口。"}
        required={needsReply} disabled={busy || stale || job.can_resume === false} maxLength={6000} />
      <div className="ai-actions">
        <button type="submit" className="primary" disabled={busy || stale || job.can_resume === false || !status?.enabled || (needsReply && !continuation.trim())}>{needsReply ? "回答并继续" : "继续生成"}</button>
        <button type="button" disabled={busy} onClick={() => void cancel()}>放弃预览</button>
        {!preview && job.scene && <button type="button" onClick={() => useViewer.getState().setAIPreview(job.scene)}>查看中间模型</button>}
      </div>
      </form>
    </div>}
    {job?.status === "ready" && <div className="ai-preview-note">
      <strong>{preview ? "正在查看 AI 预览 · 尚未应用" : "AI 预览已就绪"}</strong>
      <p>{job.summary}</p>
      <div className="ai-actions">
        <button className="primary" disabled={busy} onClick={() => void apply()}>应用到项目</button>
        <button disabled={busy} onClick={() => void cancel()}>放弃预览</button>
        {!preview && job.scene && <button onClick={() => useViewer.getState().setAIPreview(job.scene)}>查看预览</button>}
      </div>
    </div>}
    {job && job.status !== "ready" && !paused && <p role="status">{job.status === "running" ? (job.cancel_requested ? "正在停止，等待当前调用结束…" : `本段已调用 ${job.segment_calls ?? job.calls}/${job.segment_limit ?? 20} 次，累计 ${job.calls} 次，等待结果…`) : job.status === "applied" ? (job.base_version ? "已应用，可使用撤销恢复" : "已应用到项目") : job.summary}</p>}
    {running && preview && <p className="ai-hint">正在续作，当前画面为上一检查点的未完成预览。</p>}
    {job && <details className="ai-trace"><summary>查看过程 · {job.trace.length} 条记录 · 累计 {job.calls} 次模型调用 · 约 ¥{job.estimated_cny.toFixed(3)}</summary>
      <ol>{job.trace.map(step => <li key={step.step}><strong>{step.tool}</strong> {step.outcome === "error" ? "失败" : ""}<pre>{step.detail}</pre></li>)}</ol>
    </details>}
    {status?.budget && <p className="ai-hint">{status.budget.enforced === false
      ? `累计费用及未知费用预留 ¥${status.budget.charged_or_reserved_cny.toFixed(2)} · 不设金额上限`
      : `本轮总预算占用 ¥${status.budget.charged_or_reserved_cny.toFixed(2)} / ¥${status.budget.limit_cny?.toFixed(0) ?? "—"}（含实验、所有模型与检测）`}</p>}
    {error && <p role="alert" className="ai-error">{error}</p>}
  </section>;
}
