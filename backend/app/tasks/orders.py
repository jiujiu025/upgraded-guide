"""超时未支付订单自动关闭。

只处理 ``pending_payment``（在线支付渠道下单后未付款）的订单：

- ``pending_confirm`` 是"联系客服/线下转账"订单，等的是人工确认，**不自动关闭**，
  否则会把正在走银行转账的客户订单误杀；
- 已存在 ``pending`` / ``manual_review`` 支付单的订单跳过，避免关闭正在付款中的订单；
- 关闭只改本地状态，不调用任何外部接口，也不涉及资金。
"""

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Customer, Order, PaymentOrder
from app.services.order_service import add_notification

MAX_BATCH = 200


def close_expired_orders(db: Session, *, now: datetime, timeout_minutes: int, limit: int = MAX_BATCH) -> dict:
    cutoff = now - timedelta(minutes=timeout_minutes)
    candidates = db.scalars(
        select(Order)
        .where(
            Order.status == "pending_payment",
            Order.payment_status == "unpaid",
            Order.created_at < cutoff,
        )
        .order_by(Order.id)
        .limit(limit)
    ).all()

    closed = 0
    skipped_active_payment = 0
    for order in candidates:
        has_active_payment = db.scalar(
            select(PaymentOrder.id).where(
                PaymentOrder.order_id == order.id,
                PaymentOrder.status.in_(("pending", "manual_review")),
            )
        )
        if has_active_payment:
            skipped_active_payment += 1
            continue
        order.status = "cancelled"
        order.cancelled_at = now
        customer = db.get(Customer, order.customer_id)
        hours = max(1, timeout_minutes // 60)
        add_notification(
            db,
            customer.user_id if customer else None,
            "订单已自动关闭",
            f"订单 {order.order_no} 超过 {hours} 小时未支付，已自动关闭。如仍需购买请重新下单。",
            "order",
        )
        add_notification(
            db,
            order.assigned_sales_id,
            "订单已自动关闭",
            f"客户 {customer.name if customer else order.customer_id}：订单 {order.order_no} 超时未支付，已自动关闭。",
            "order",
        )
        closed += 1

    return {
        "scanned": len(candidates),
        "closed": closed,
        "skipped_active_payment": skipped_active_payment,
        "timeout_minutes": timeout_minutes,
    }
