"""给管理员的站内通知辅助。

定时任务没有登录用户，因此不能走 ``write_audit``（其 actor 是 users 外键），
统一用通知告知管理员，执行结果本身记录在 ``scheduled_task_runs``。
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Notification, User


ADMIN_ROLES = ("super_admin", "admin")


def active_admin_ids(db: Session) -> list[int]:
    return list(db.scalars(select(User.id).where(User.role.in_(ADMIN_ROLES), User.is_active.is_(True))).all())


def notify_admins(db: Session, title: str, content: str, category: str, *, marker: str | None = None) -> int:
    """给所有启用中的管理员发通知。

    ``marker`` 用于去重：同一管理员下已存在包含该 marker 的同分类通知时跳过，
    避免任务每轮重复提醒同一件事。
    """
    sent = 0
    for admin_id in active_admin_ids(db):
        if marker and db.scalar(
            select(Notification.id).where(
                Notification.user_id == admin_id,
                Notification.category == category,
                Notification.content.contains(marker),
            )
        ):
            continue
        db.add(Notification(user_id=admin_id, title=title, content=content, category=category))
        sent += 1
    return sent
