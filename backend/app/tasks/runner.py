"""任务执行器：抢锁、执行、落流水。

失败不外抛（调度进程要能继续跑下一轮），但会写 ``scheduled_task_runs`` 并在日志
里留完整堆栈。
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from time import perf_counter

from app.core.config import get_settings
from app.db import SessionLocal
from app.models import ScheduledTaskRun
from app.models.base import utcnow
from app.tasks.locking import task_lock
from app.tasks.registry import TASKS

logger = logging.getLogger(__name__)


@dataclass
class TaskOutcome:
    name: str
    status: str
    summary: str
    counts: dict = field(default_factory=dict)
    error: str | None = None


def _summarize(counts: dict) -> str:
    if not counts:
        return "无统计"
    return json.dumps(counts, ensure_ascii=False, sort_keys=True)


def _record_run(
    name: str,
    *,
    status: str,
    triggered_by: str,
    started_at: datetime,
    duration_ms: int,
    summary: str,
    error: str | None,
) -> None:
    """用独立会话落流水，避免任务事务回滚把日志一起回滚掉。"""
    try:
        with SessionLocal() as db:
            db.add(
                ScheduledTaskRun(
                    task_name=name,
                    status=status,
                    triggered_by=triggered_by,
                    started_at=started_at,
                    finished_at=utcnow(),
                    duration_ms=duration_ms,
                    summary=summary[:4000],
                    error=error[:4000] if error else None,
                )
            )
            db.commit()
    except Exception:
        logger.exception("定时任务流水写入失败 task=%s", name)


def execute(name: str, *, triggered_by: str = "scheduler", now: datetime | None = None) -> TaskOutcome:
    spec = TASKS.get(name)
    if spec is None:
        return TaskOutcome(name, "failed", "", {}, f"未知任务: {name}")

    started_at = utcnow()
    task_now = now or started_at
    clock = perf_counter()

    with task_lock(name, get_settings().task_lock_ttl_seconds) as acquired:
        if not acquired:
            logger.info("定时任务跳过（未抢到锁） task=%s", name)
            return TaskOutcome(name, "skipped", "未抢到执行锁，本轮跳过")

        status = "success"
        counts: dict = {}
        error: str | None = None
        db = SessionLocal()
        try:
            counts = spec.handler(db, task_now) or {}
            db.commit()
        except Exception as exc:
            db.rollback()
            status = "failed"
            error = f"{type(exc).__name__}: {exc}"
            logger.exception("定时任务执行失败 task=%s", name)
        finally:
            db.close()

    duration_ms = int((perf_counter() - clock) * 1000)
    summary = _summarize(counts)
    _record_run(
        name,
        status=status,
        triggered_by=triggered_by,
        started_at=started_at,
        duration_ms=duration_ms,
        summary=summary,
        error=error,
    )
    if status == "success":
        logger.info("定时任务完成 task=%s duration_ms=%s %s", name, duration_ms, summary)
    return TaskOutcome(name, status, summary, counts, error)
