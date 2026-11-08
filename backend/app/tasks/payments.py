"""支付对账（本地）。

支付平台只提供下单与异步通知，本地缺少"查单"能力，因此这里做三件不依赖外部
接口的事：

1. 把长期停在 ``pending`` 且已过期的支付单标记为 ``expired``，让挂单有终态；
2. 找出"标记已付但账没落"的差异：充值单没有对应钱包流水、订单支付单没有把订单
   推进到已付款；
3. 把积压超过一天的 ``manual_review`` 单量报给管理员，避免人工核查队列悄悄变长。

真正的远程查单（``act=order``）需要先拿服务商沙箱验证响应字段，未实现前
不做任何自动入账——宁可让钱挂在人工核查，也不能凭猜测改账。
"""

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Order, PaymentOrder, WalletTransaction
from app.tasks.notify import notify_admins

PAID_OR_LATER = ("paid", "processing", "completed", "refunded")
MANUAL_REVIEW_ALERT_HOURS = 24
MAX_BATCH = 500


def reconcile_payments(db: Session, *, now: datetime, grace_minutes: int, limit: int = MAX_BATCH) -> dict:
    cutoff = now - timedelta(minutes=grace_minutes)
    stale = db.scalars(
        select(PaymentOrder)
        .where(
            PaymentOrder.status == "pending",
            PaymentOrder.expires_at.is_not(None),
            PaymentOrder.expires_at < cutoff,
        )
        .order_by(PaymentOrder.id)
        .limit(limit)
    ).all()
    for item in stale:
        item.status = "expired"

    missing_wallet_credit = db.scalar(
        select(func.count())
        .select_from(PaymentOrder)
        .where(
            PaymentOrder.status == "paid",
            PaymentOrder.purpose == "wallet_recharge",
            ~select(1).where(WalletTransaction.payment_order_id == PaymentOrder.id).exists(),
        )
    ) or 0

    order_not_advanced = db.scalar(
        select(func.count())
        .select_from(PaymentOrder)
        .join(Order, PaymentOrder.order_id == Order.id)
        .where(
            PaymentOrder.status == "paid",
            PaymentOrder.purpose == "order_payment",
            Order.status.not_in(PAID_OR_LATER),
        )
    ) or 0

    review_cutoff = now - timedelta(hours=MANUAL_REVIEW_ALERT_HOURS)
    manual_review_backlog = db.scalar(
        select(func.count())
        .select_from(PaymentOrder)
        .where(PaymentOrder.status == "manual_review", PaymentOrder.created_at < review_cutoff)
    ) or 0

    differences = int(missing_wallet_credit) + int(order_not_advanced)
    notified = 0
    if differences or manual_review_backlog:
        # 按自然日去重：同一管理员当天只收到一条，避免每轮任务重复刷屏。
        day_marker = f"对账日期 {now:%Y-%m-%d}"
        notified = notify_admins(
            db,
            "支付对账发现差异",
            (
                f"充值单已付款但无钱包流水：{missing_wallet_credit} 笔；"
                f"订单支付单已付款但订单未推进：{order_not_advanced} 笔；"
                f"积压超过 {MANUAL_REVIEW_ALERT_HOURS} 小时的人工核查单：{manual_review_backlog} 笔。"
                f"请到财务页面逐笔核对。（{day_marker}）"
            ),
            "finance",
            marker=day_marker,
        )

    return {
        "expired_payment_orders": len(stale),
        "missing_wallet_credit": int(missing_wallet_credit),
        "order_not_advanced": int(order_not_advanced),
        "manual_review_backlog": int(manual_review_backlog),
        "admins_notified": notified,
    }
