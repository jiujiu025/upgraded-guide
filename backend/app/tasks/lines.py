"""线路到期提醒与到期标记。

按 ``LINE_REMINDER_DAYS``（默认 7/3/1 天）给客户发站内通知；到期后由 ``expire_lines``
把线路落成 ``expired``，让"正常线路"的口径能真正随时间变化。

设计要点：

- 按"档位"去重而不是按"天"去重：剩余 5 天的线路归入 7 天档，同一档只发一次，
  所以任务每天跑也不会变成每天一条骚扰；
- 通知内容沿用 ``线路 {id} 即将到期`` 前缀，与 ``GET /customer/renewals``
  里原有的惰性提醒共用同一套去重口径，两者不会重复发；
- 只提醒 ``status == "active"`` 且尚未到期的线路，已过期线路不在这里处理。
- ``expire_lines`` 只翻 ``active``：``disabled``（人工停用）与 ``deleted`` 不动，
  否则管理员停用的线路会被时间任务改写；续费成功时由 ``settle_renewal`` 拉回 ``active``。
"""

import math
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Customer, CustomerLine, Notification, Product
from app.services.order_service import add_notification

MAX_BATCH = 500


def expire_lines(db: Session, *, now: datetime, grace_hours: int = 0, limit: int = MAX_BATCH) -> dict:
    """把已到期但仍标 ``active`` 的线路落成 ``expired``。

    只处理 ``active``：人工停用（``disabled``）与已删除（``deleted``）不在这里改写。
    重复执行是幂等的——线路一旦变成 ``expired`` 就不再进入候选集。
    """
    cutoff = now - timedelta(hours=max(0, int(grace_hours)))
    rows = db.execute(
        select(CustomerLine, Customer)
        .join(Customer, CustomerLine.customer_id == Customer.id)
        .where(
            CustomerLine.status == "active",
            CustomerLine.expires_at <= cutoff,
        )
        .order_by(CustomerLine.expires_at)
        .limit(limit)
    ).all()

    expired = 0
    notified = 0
    for line, customer in rows:
        line.status = "expired"
        expired += 1
        if not customer.user_id:
            continue
        # 按"线路 + 到期日"去重：同一档到期只发一次，续费后再次到期可以再提醒一次。
        marker = f"线路 {line.id} 已于 {line.expires_at:%Y-%m-%d} 到期"
        exists = db.scalar(
            select(Notification.id).where(
                Notification.user_id == customer.user_id,
                Notification.category == "expiration",
                Notification.content.contains(marker),
            )
        )
        if exists:
            continue
        add_notification(
            db,
            customer.user_id,
            "线路已到期",
            f"{marker}，服务已停止。续费后到期时间会从当前时间顺延，如需继续使用请及时办理。",
            "expiration",
        )
        notified += 1

    return {
        "scanned": len(rows),
        "expired": expired,
        "notified": notified,
        "grace_hours": max(0, int(grace_hours)),
        "truncated": len(rows) >= limit,
    }


def line_expiry_reminders(db: Session, *, now: datetime, reminder_days: tuple[int, ...], limit: int = MAX_BATCH) -> dict:
    thresholds = sorted({int(day) for day in reminder_days})
    if not thresholds:
        return {"scanned": 0, "sent": 0, "already_sent": 0, "thresholds": []}
    horizon = now + timedelta(days=thresholds[-1])
    rows = db.execute(
        select(CustomerLine, Customer, Product)
        .join(Customer, CustomerLine.customer_id == Customer.id)
        .join(Product, CustomerLine.product_id == Product.id)
        .where(
            CustomerLine.status == "active",
            CustomerLine.expires_at > now,
            CustomerLine.expires_at <= horizon,
        )
        .order_by(CustomerLine.expires_at)
        .limit(limit)
    ).all()

    sent = 0
    already_sent = 0
    for line, customer, product in rows:
        days_left = math.ceil((line.expires_at - now).total_seconds() / 86400)
        bucket = next((day for day in thresholds if days_left <= day), None)
        if bucket is None:
            continue
        if not customer.user_id:
            continue
        base_marker = f"线路 {line.id} 即将到期"
        bucket_marker = f"{base_marker}（剩余 {bucket} 天提醒）"
        existing = db.scalars(
            select(Notification.content).where(
                Notification.user_id == customer.user_id,
                Notification.category == "expiration",
                Notification.content.contains(base_marker),
            )
        ).all()
        if any(bucket_marker in content for content in existing):
            already_sent += 1
            continue
        # 旧的惰性提醒只覆盖 7 天档，且不带档位后缀；视同最大档已发过。
        if bucket == thresholds[-1] and any("（剩余" not in content for content in existing):
            already_sent += 1
            continue
        add_notification(
            db,
            customer.user_id,
            "线路即将到期",
            f"{bucket_marker}，线路 {line.id}（{product.name}）将于 {line.expires_at:%Y-%m-%d} 到期，请及时续费。",
            "expiration",
        )
        sent += 1

    return {
        "scanned": len(rows),
        "sent": sent,
        "already_sent": already_sent,
        "thresholds": thresholds,
    }
