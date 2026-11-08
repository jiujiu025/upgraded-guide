"""线路到期提醒。

按 ``LINE_REMINDER_DAYS``（默认 7/3/1 天）给客户发站内通知。

设计要点：

- 按"档位"去重而不是按"天"去重：剩余 5 天的线路归入 7 天档，同一档只发一次，
  所以任务每天跑也不会变成每天一条骚扰；
- 通知内容沿用 ``线路 {id} 即将到期`` 前缀，与 ``GET /customer/renewals``
  里原有的惰性提醒共用同一套去重口径，两者不会重复发；
- 只提醒 ``status == "active"`` 且尚未到期的线路，已过期线路不在这里处理。
"""

import math
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Customer, CustomerLine, Notification, Product
from app.services.order_service import add_notification

MAX_BATCH = 500


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
