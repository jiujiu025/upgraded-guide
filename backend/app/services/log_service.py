"""system_logs：给运维看的运行日志。

三条约束：

1. **写日志不能影响主流程**。任何异常都在这里吞掉并降级到标准 logging，
   绝不能因为"记日志失败"让一笔已经扣钱的采购回滚。
2. **跟随当前事务**。调用方在关键失败点先 commit 业务状态，再写日志；
   这样业务回滚时不会留下"说了失败但没失败"的假日志，也不会丢真日志。
3. **不写敏感信息**。面板密码、上游账密、token 一律不落 detail。
"""
from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import SystemLog, User
from app.models.base import utcnow

logger = logging.getLogger(__name__)

LEVELS = ("info", "warning", "error")
MAX_DETAIL = 2000


def write_log(
    db: Session,
    level: str,
    category: str,
    action: str,
    message: str,
    *,
    detail: str | None = None,
    actor: User | None = None,
    order_id: int | None = None,
    line_id: int | None = None,
    vps_id: int | None = None,
) -> None:
    try:
        context = db.info.get("impersonation")
        actor_id = context.actor_id if context is not None else (actor.id if actor is not None else None)
        db.add(SystemLog(
            level=level if level in LEVELS else "info",
            category=(category or "system")[:30],
            action=(action or "unknown")[:60],
            message=(message or "")[:500],
            detail=(detail or "")[:MAX_DETAIL] or None,
            actor_id=actor_id,
            order_id=order_id,
            line_id=line_id,
            vps_id=vps_id,
        ))
        db.flush()
    except Exception:  # noqa: BLE001 - 记日志失败绝不能连累业务
        logger.exception("写系统日志失败：%s/%s %s", category, action, message)


def purge_old(db: Session, days: int = 30) -> int:
    """清理超过保留期的日志。由定时任务调用。"""
    from datetime import timedelta

    cutoff = utcnow() - timedelta(days=days)
    deleted = db.query(SystemLog).filter(SystemLog.created_at < cutoff).delete(synchronize_session=False)
    if deleted:
        db.commit()
    return deleted or 0


def counts(db: Session, days: int = 7) -> dict[str, int]:
    from datetime import timedelta

    since = utcnow() - timedelta(days=days)
    rows = db.execute(
        select(SystemLog.level, func.count()).where(SystemLog.created_at >= since).group_by(SystemLog.level)
    ).all()
    result = {level: 0 for level in LEVELS}
    for level, count in rows:
        result[level] = count
    return result
