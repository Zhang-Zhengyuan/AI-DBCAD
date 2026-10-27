"""Authenticated asynchronous AI previews and explicit version publication."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from ...dependencies import verify_api_password
from ... import ai_editor
from .projects import _project_or_404

router = APIRouter(dependencies=[Depends(verify_api_password)])


class EditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instruction: str = Field(min_length=2, max_length=6000)
    base_version: StrictInt | None = Field(..., ge=1)
    selected: list[str] = Field(default_factory=list, max_length=128)
    parent_job_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")


class ApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    author: str = Field(default="AI editor", min_length=1, max_length=120)


class ResumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instruction: str = Field(default="", max_length=6000)


@router.get("/ai/status")
def status():
    return ai_editor.availability()


@router.post("/projects/{project_id}/ai/jobs", status_code=202)
async def create(project_id: str, payload: EditRequest):
    _project_or_404(project_id)
    return (
        await ai_editor.start(
            project_id, payload.base_version, payload.instruction, payload.selected, payload.parent_job_id
        )
    ).public()


@router.get("/projects/{project_id}/ai/jobs/{job_id}")
def get(project_id: str, job_id: str):
    _project_or_404(project_id)
    return ai_editor.find_job(project_id, job_id).public()


@router.post("/projects/{project_id}/ai/jobs/{job_id}/cancel")
def cancel(project_id: str, job_id: str):
    _project_or_404(project_id)
    job = ai_editor.find_job(project_id, job_id)
    job.cancelled = True
    if job.status in {"ready", "paused"}:
        job.status = "cancelled"
        job.summary = "已放弃预览，正式模型未修改"
    ai_editor.persist(job)
    return job.public()


@router.post("/projects/{project_id}/ai/jobs/{job_id}/resume", status_code=202)
async def resume(project_id: str, job_id: str, payload: ResumeRequest):
    _project_or_404(project_id)
    return (await ai_editor.resume(ai_editor.find_job(project_id, job_id), payload.instruction)).public()


@router.post("/projects/{project_id}/ai/jobs/{job_id}/apply")
async def apply(project_id: str, job_id: str, payload: ApplyRequest):
    _project_or_404(project_id)
    return ai_editor.apply(ai_editor.find_job(project_id, job_id), payload.author)
