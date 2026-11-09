"""定时任务运维接口：查看执行流水、手动触发一次。"""

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.db import get_db
from app.models import ScheduledTaskRun, User
from app.tasks.registry import TASKS
from app.tasks.runner import execute

router = APIRouter()


def _run_view(item: ScheduledTaskRun) -> dict:
    return {
        "id": item.id,
        "task_name": item.task_name,
        "status": item.status,
        "triggered_by": item.triggered_by,
        "started_at": item.started_at,
        "finished_at": item.finished_at,
        "duration_ms": item.duration_ms,
        "summary": item.summary,
        "error": item.error,
    }


@router.get("/admin/task-runs")
def list_task_runs(
    limit: int = Query(50, ge=1, le=200),
    task_name: str | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("super_admin", "admin")),
):
    stmt = select(ScheduledTaskRun).order_by(ScheduledTaskRun.id.desc()).limit(limit)
    if task_name:
        stmt = stmt.where(ScheduledTaskRun.task_name == task_name)
    return [_run_view(item) for item in db.scalars(stmt).all()]


@router.get("/admin/task-runs/catalog")
def task_catalog(_: User = Depends(require_roles("super_admin", "admin"))):
    return [
        {"name": spec.name, "title": spec.title, "interval_minutes": spec.interval_minutes}
        for spec in TASKS.values()
    ]


@router.post("/admin/task-runs/{task_name}/run", status_code=202)
def run_task_now(
    task_name: str,
    background: BackgroundTasks,
    _: User = Depends(require_roles("super_admin", "admin")),
):
    """手动触发一次任务：立即返回 202，实际执行放到后台。

    供应商目录/报价刷新要打外部接口，可能几十秒。以前是同步执行，浏览器会一直挂到
    超时或任务结束；现在先回 202，前端轮询执行流水看结果，请求本身不再被拖住。
    """
    if task_name not in TASKS:
        raise HTTPException(status_code=404, detail="任务不存在")
    background.add_task(execute, task_name, triggered_by="manual_api")
    return {"name": task_name, "status": "queued"}
