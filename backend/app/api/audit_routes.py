"""全局操作审计：谁在什么时候改了什么。

此前只有「客户 360」里能按客户查，管理员看不到全局视图——开关切换、自动采购、
交付、改价这些动作写了审计但没人能看到，出事时无法回溯。

这里只提供只读查询，不提供删除/修改：审计日志一旦可改就失去意义。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.db import get_db
from app.models import AuditLog, User

router = APIRouter(prefix="/admin/audit-logs", tags=["audit"])

MAX_PAGE_SIZE = 200


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _like(value: str) -> str:
    return f"%{value.strip()}%"


@router.get("")
def list_audit_logs(
    actor: str | None = Query(default=None, max_length=80, description="操作人用户名或姓名，模糊匹配"),
    action: str | None = Query(default=None, max_length=60, description="动作，精确匹配"),
    resource_type: str | None = Query(default=None, max_length=40, description="资源类型，精确匹配"),
    keyword: str | None = Query(default=None, max_length=120, description="在详情里搜关键词"),
    start: datetime | None = Query(default=None, description="起始时间（含）"),
    end: datetime | None = Query(default=None, description="结束时间（含）"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=MAX_PAGE_SIZE),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("super_admin", "admin")),
):
    """分页查询审计日志。

    用 outer join 取操作人：系统动作（定时任务、支付回调、自动采购）没有 actor_id，
    inner join 会把它们全部丢掉——而这些恰恰是排查时最想看的。
    """
    base = select(AuditLog, User.display_name, User.username).outerjoin(
        User, AuditLog.actor_id == User.id,
    )
    count_stmt = select(func.count()).select_from(AuditLog).outerjoin(
        User, AuditLog.actor_id == User.id,
    )

    conditions = []
    if actor and actor.strip():
        conditions.append(or_(
            User.username.ilike(_like(actor)),
            User.display_name.ilike(_like(actor)),
        ))
    if action:
        conditions.append(AuditLog.action == action)
    if resource_type:
        conditions.append(AuditLog.resource_type == resource_type)
    if keyword and keyword.strip():
        conditions.append(AuditLog.detail.ilike(_like(keyword)))
    if start is not None:
        conditions.append(AuditLog.created_at >= start.replace(tzinfo=None))
    if end is not None:
        conditions.append(AuditLog.created_at <= end.replace(tzinfo=None))
    if conditions:
        base = base.where(*conditions)
        count_stmt = count_stmt.where(*conditions)

    total = db.scalar(count_stmt) or 0
    rows = db.execute(
        base.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()

    items = [{
        "id": item.id,
        "created_at": item.created_at,
        "action": item.action,
        "resource_type": item.resource_type,
        "resource_id": item.resource_id,
        "detail": item.detail,
        # 没有操作人就是系统动作：定时任务、支付回调、自动采购
        "actor": display_name or username or "系统",
        "actor_username": username,
    } for item, display_name, username in rows]

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, (total + page_size - 1) // page_size),
        "facets": _facets(db),
    }


def _facets(db: Session) -> dict:
    """筛选项：动作与资源类型的去重值，供前端下拉。

    distinct 全表扫在小表上没问题；审计表会长大，这里限制 100 个，够用且不会拖慢页面。
    """
    actions = [row for row in db.scalars(select(AuditLog.action).distinct().order_by(AuditLog.action).limit(100))]
    types = [row for row in db.scalars(select(AuditLog.resource_type).distinct().order_by(AuditLog.resource_type).limit(100))]
    return {"actions": actions, "resource_types": types}


@router.get("/summary")
def audit_summary(
    days: int = Query(default=7, ge=1, le=90),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("super_admin", "admin")),
):
    """最近 N 天按动作统计，用来看"今天都发生了什么"。"""
    since = _utcnow() - timedelta(days=days)
    rows = db.execute(
        select(AuditLog.action, func.count())
        .where(AuditLog.created_at >= since)
        .group_by(AuditLog.action)
        .order_by(func.count().desc())
        .limit(20)
    ).all()
    return {"days": days, "items": [{"action": action, "count": count} for action, count in rows]}
