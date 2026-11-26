"""管理员系统日志：排障用。

审计页回答"谁改了什么"，这里回答"系统在哪一步失败了"——上游扣款后中转失败、
面板连不上、密钥解不开这类。只读，不能删改（保留期由定时任务统一清理）。
"""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.api.pagination import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    like_pattern,
    page_payload,
    paginate,
)
from app.db import get_db
from app.models import SystemLog, User
from app.models.base import utcnow
from app.services.log_service import LEVELS, counts

router = APIRouter(prefix="/admin/logs", tags=["系统日志"])


@router.get("")
def list_logs(
    level: str | None = Query(default=None, max_length=10),
    category: str | None = Query(default=None, max_length=30),
    keyword: str | None = Query(default=None, max_length=64),
    days: int = Query(default=7, ge=0, le=90),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("super_admin", "admin")),
):
    query = select(SystemLog)
    if days:
        query = query.where(SystemLog.created_at >= utcnow() - timedelta(days=days))
    if level:
        query = query.where(SystemLog.level == level)
    if category:
        query = query.where(SystemLog.category == category)
    if keyword and keyword.strip():
        pattern = like_pattern(keyword.strip())
        query = query.where(or_(
            SystemLog.message.ilike(pattern, escape="\\"),
            SystemLog.action.ilike(pattern, escape="\\"),
            SystemLog.detail.ilike(pattern, escape="\\"),
        ))

    # 按发生时间倒序；同一秒内的按 id 兜底，导入历史数据时也不会乱
    items, total = paginate(db, query.order_by(SystemLog.created_at.desc(), SystemLog.id.desc()),
                            page, page_size)
    # 分类下拉不能跟着筛选收缩，否则选中某个分类后就只剩它自己
    categories = [
        row[0] for row in db.execute(
            select(SystemLog.category).distinct().order_by(SystemLog.category)
        ).all()
    ]
    return page_payload(
        items, total, page, page_size,
        counts=counts(db, days=7),
        options={"levels": list(LEVELS), "categories": categories},
    )


@router.get("/summary")
def log_summary(days: int = Query(default=7, ge=1, le=90),
                db: Session = Depends(get_db),
                user: User = Depends(require_roles("super_admin", "admin"))):
    since = utcnow() - timedelta(days=days)
    rows = db.execute(
        select(SystemLog.category, SystemLog.level, func.count())
        .where(SystemLog.created_at >= since)
        .group_by(SystemLog.category, SystemLog.level)
    ).all()
    by_category: dict[str, dict[str, int]] = {}
    for category, level, count in rows:
        by_category.setdefault(category, {})[level] = count
    return {"days": days, "by_level": counts(db, days=days), "by_category": by_category}
