import { useEffect, useState } from "react";
import { api, ApiError, type AIJob, type AIStatus } from "./api";
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
  useEffect(() => { void api.aiStatus().then(setStatus).catch(() => setStatus(null)); }, []);
  useEffect(() => {
    setJob(null); setError(""); setContinuation(""); setRestoring(false);
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
        if (fresh.status !== "running") void api.aiStatus().then(setStatus);
      }).catch(e => { if (alive) setError(String(e)); });
    }, 1500);
    return () => { alive = false; clearInterval(timer); };
  }, [job?.id, job?.status]);
  const start = async () => {
    if (!project) return;
    setBusy(true); setError("");
    try {
      const fresh = await api.aiStart(project.id, instruction, project.head_version, selected);
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
  const running = job?.status === "running";
  const paused = job?.status === "paused";
  const stale = !!job && !!project && project.head_version !== job.base_version;
  return <section className="ai-panel" aria-label="AI CAD 编辑">
    <div className="ai-heading"><h2>AI CAD</h2><span>ResumaCAD</span></div>
    <p className="ai-model">{status?.enabled ? status.model : "AI 尚未配置"}</p>
    <p>用自然语言创建或修改模型。支持图元、移动、缩放和布尔运算。</p>
    <label htmlFor="ai-instruction">编辑要求</label>
    <textarea id="ai-instruction" value={instruction} onChange={e => setInstruction(e.target.value)} rows={5}
      placeholder="例如：创建一个 60×40×8 的底板，在中央打一个半径 6 的通孔。" disabled={running || busy || paused || job?.status === "ready"} />
    <p className="ai-hint">{selected.length ? `已选中 ${selected.length} 个组件` : "针对当前项目；可先选中需要修改的组件"}。结果先预览，再应用。</p>
    <div className="ai-actions">
      <button className="primary" disabled={!project || !status?.enabled || instruction.trim().length < 2 || running || busy || restoring || job?.status === "ready" || paused}
        onClick={() => void start()}>{running ? "AI 正在编辑…" : "生成预览"}</button>
      {running && <button disabled={busy || job.cancel_requested} onClick={() => void cancel()}>停止</button>}
    </div>
    {paused && <div className="ai-preview-note">
      <strong>{preview ? "正在查看未完成预览 · 已暂停" : "未完成预览已暂停"}</strong>
      <p>{job.summary}</p>
      <p>累计 {job.calls} 次模型调用 · 约 ¥{job.estimated_cny.toFixed(3)}。继续沿用原任务和累计费用，不会从头生成。</p>
      {stale && <p role="alert">项目版本已改变，请基于最新模型重新发起任务；旧预览不能直接继续。</p>}
      <form onSubmit={e => { e.preventDefault(); void resume(String(new FormData(e.currentTarget).get("instruction") ?? "")); }}>
      <label htmlFor="ai-continuation">续作补充要求（可选）</label>
      <textarea id="ai-continuation" name="instruction" value={continuation} onChange={e => setContinuation(e.target.value)} rows={3}
        placeholder="例如：保留主体，接着完成把手和出水口。" disabled={busy || stale || job.can_resume === false} maxLength={6000} />
      <div className="ai-actions">
        <button type="submit" className="primary" disabled={busy || stale || job.can_resume === false || !status?.enabled}>继续生成</button>
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
    {status?.budget && <p className="ai-hint">本轮预算占用 ¥{status.budget.charged_or_reserved_cny.toFixed(2)} / ¥{status.budget.limit_cny.toFixed(0)}（含实验）</p>}
    {error && <p role="alert" className="ai-error">{error}</p>}
  </section>;
}
