import { useEffect, useState } from "react";
import { api, type AIJob, type AIStatus } from "./api";
import { useViewer } from "./store";

export function AIPanel() {
  const project = useViewer(s => s.activeProject);
  const selected = useViewer(s => s.selected);
  const preview = useViewer(s => s.aiPreview);
  const [instruction, setInstruction] = useState("");
  const [job, setJob] = useState<AIJob | null>(null);
  const [status, setStatus] = useState<AIStatus | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { void api.aiStatus().then(setStatus).catch(() => setStatus(null)); }, []);
  useEffect(() => {
    setJob(null); setError("");
    useViewer.getState().setAIPreview(null);
    if (project) {
      const saved = sessionStorage.getItem(`inferracad.ai.${project.id}`);
      if (saved) void api.aiJob(project.id, saved).then(fresh => {
        if (useViewer.getState().activeProject?.id !== project.id) return;
        setJob(fresh);
        if (fresh.status === "ready" && fresh.scene) useViewer.getState().setAIPreview(fresh.scene);
      }).catch(() => sessionStorage.removeItem(`inferracad.ai.${project.id}`));
    }
  }, [project?.id]);
  useEffect(() => {
    if (!job || job.status !== "running") return;
    let alive = true;
    const timer = setInterval(() => {
      void api.aiJob(job.project_id, job.id).then(fresh => {
        if (!alive) return;
        setJob(fresh);
        if (fresh.status === "ready" && fresh.scene && useViewer.getState().activeProject?.id === fresh.project_id)
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
  const running = job?.status === "running";
  return <section className="ai-panel" aria-label="AI CAD 编辑">
    <div className="ai-heading"><h2>AI CAD</h2><span>InferraCAD</span></div>
    <p className="ai-model">{status?.enabled ? status.model : "AI 尚未配置"}</p>
    <p>用自然语言创建或修改模型。支持图元、移动、缩放和布尔运算。</p>
    <label htmlFor="ai-instruction">编辑要求</label>
    <textarea id="ai-instruction" value={instruction} onChange={e => setInstruction(e.target.value)} rows={5}
      placeholder="例如：创建一个 60×40×8 的底板，在中央打一个半径 6 的通孔。" disabled={running || busy} />
    <p className="ai-hint">{selected.length ? `已选中 ${selected.length} 个组件` : "针对当前项目；可先选中需要修改的组件"}。结果先预览，再应用。</p>
    <div className="ai-actions">
      <button className="primary" disabled={!project || !status?.enabled || instruction.trim().length < 2 || running || busy || job?.status === "ready"}
        onClick={() => void start()}>{running ? "AI 正在编辑…" : "生成预览"}</button>
      {running && <button disabled={busy || job.cancel_requested} onClick={() => void cancel()}>停止</button>}
    </div>
    {job?.status === "ready" && <div className="ai-preview-note">
      <strong>{preview ? "正在查看 AI 预览 · 尚未应用" : "AI 预览已就绪"}</strong>
      <p>{job.summary}</p>
      <div className="ai-actions">
        <button className="primary" disabled={busy} onClick={() => void apply()}>应用到项目</button>
        <button disabled={busy} onClick={() => void cancel()}>放弃预览</button>
        {!preview && job.scene && <button onClick={() => useViewer.getState().setAIPreview(job.scene)}>查看预览</button>}
      </div>
    </div>}
    {job && job.status !== "ready" && <p role="status">{job.status === "running" ? (job.cancel_requested ? "正在停止，等待当前调用结束…" : `已调用 ${job.calls} 轮，等待结果…`) : job.status === "applied" ? (job.base_version ? "已应用，可使用撤销恢复" : "已应用到项目") : job.summary}</p>}
    {job && <details className="ai-trace"><summary>查看过程 · {job.trace.length} 步 · 约 ¥{job.estimated_cny.toFixed(3)}</summary>
      <ol>{job.trace.map(step => <li key={step.step}><strong>{step.tool}</strong> {step.outcome === "error" ? "失败" : ""}<pre>{step.detail}</pre></li>)}</ol>
    </details>}
    {status?.budget && <p className="ai-hint">本轮预算占用 ¥{status.budget.charged_or_reserved_cny.toFixed(2)} / ¥{status.budget.limit_cny.toFixed(0)}（含实验）</p>}
    {error && <p role="alert" className="ai-error">{error}</p>}
  </section>;
}
