from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request

from app.jobs import PING_QUEUE, JobAccepted, JobState, PingJob, job_key
from app.resources import Resources

router = APIRouter(prefix="/jobs", tags=["jobs"])


def _resources(request: Request) -> Resources:
    return request.app.state.resources


@router.post("/ping", status_code=202)
async def enqueue_ping(request: Request) -> JobAccepted:
    """Ставит проверочную задачу в очередь; воркер отметит её выполненной в Redis."""
    res = _resources(request)
    job = PingJob(job_id=uuid4().hex)
    state = JobState(job_id=job.job_id, status="queued")
    await res.redis.set(job_key(job.job_id), state.model_dump_json(), ex=res.settings.job_ttl_s)
    try:
        broker = await res.ensure_broker()
        await broker.publish(job, queue=PING_QUEUE)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"очередь недоступна: {exc}") from exc
    return JobAccepted(job_id=job.job_id, status_url=f"/api/jobs/{job.job_id}")


@router.get("/{job_id}")
async def get_job(job_id: str, request: Request) -> JobState:
    raw = await _resources(request).redis.get(job_key(job_id))
    if raw is None:
        raise HTTPException(status_code=404, detail="задача не найдена или истёк срок хранения")
    return JobState.model_validate_json(raw)
