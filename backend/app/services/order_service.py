from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import case, select, update
from sqlalchemy.orm import Session

from app.models import Customer, CustomerLine, Notification, Order, PaymentOrder, Product, User
from app.models.base import utcnow
from app.services.pricing_service import PricingError, quote as _quote

PAYMENT_METHODS = {"balance", "online", "contact_manager"}
TERMINAL_ORDER_STATUSES = {"completed", "cancelled", "refunded"}
# 尚未结清的支付单：`pending` 是通道处理中，`manual_review` 是钱可能已到但账没落。
# 这两种情况下取消订单，都会让"钱在平台、单在本地消失"，口径与
# ``tasks.orders.close_expired_orders`` 保持一致。
BLOCKING_PAYMENT_STATUSES = ("pending", "manual_review")
# 视为"钱已经到账"的收款状态：线上回调与余额支付写 `paid`，财务人工登记写
# `manually_confirmed`。两者都可以继续走确认/交付。
PAID_PAYMENT_STATUSES = {"paid", "manually_confirmed"}


def calculate_order_price(db: Session, product: Product, quantity: int, billing_days: int) -> tuple[float, float]:
    """返回 (该周期单价, 合计)。实现在 pricing_service，这里只做类型适配与 HTTP 映射。"""
    try:
        unit_price, total = _quote(db, product, quantity, billing_days)
    except PricingError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return float(unit_price), float(total)


def initial_order_status(payment_method: str, order_type: str = "purchase") -> str:
    if payment_method not in PAYMENT_METHODS:
        raise HTTPException(status_code=422, detail="不支持的支付方式")
    if payment_method == "contact_manager":
        # 续费的“联系经理”仍走人工完成；余额/在线则进入可支付状态，由支付接口推进。
        return "renewal" if order_type == "renewal" else "pending_confirm"
    return "pending_payment"


def is_payable_order(order: Order) -> bool:
    """订单当前是否可以被客户支付（普通订单 + 尚未收款的续费单）。"""
    if order.status == "pending_payment":
        return True
    return order.order_type == "renewal" and order.status == "renewal" and order.renewal_completed_at is None


def active_payment_order(db: Session, order_id: int) -> PaymentOrder | None:
    """订单上尚未结清的支付单，没有则返回 ``None``。"""
    return db.scalar(
        select(PaymentOrder)
        .where(
            PaymentOrder.order_id == order_id,
            PaymentOrder.status.in_(BLOCKING_PAYMENT_STATUSES),
        )
        .order_by(PaymentOrder.id.desc())
    )


def ensure_order_cancellable(db: Session, order: Order) -> None:
    """取消订单前的统一闸门。

    客户自助取消和管理员取消都必须走这里：支付中的订单一旦被取消，支付平台回调
    仍可能到达，本地只能把订单从"已取消"再改回"已付款"，账面会乱。宁可让调用方
    先等支付结果或先到财务核查，也不允许在钱没落定的时候关单。
    """
    payment = active_payment_order(db, order.id)
    if not payment:
        return
    if payment.status == "manual_review":
        raise HTTPException(
            status_code=409,
            detail="该订单有支付异常待财务核对，请先到财务页面处理后再取消",
        )
    raise HTTPException(
        status_code=409,
        detail="订单正在支付中，请等待支付结果；放弃支付后订单会超时自动关闭",
    )


def ensure_payment_settled(order: Order) -> None:
    """确认/交付前的收款闸门。

    线下转账与对公付款（``contact_manager``）现实里就是"先确认、后到账"，因此给它
    留了口子；余额和在线支付必须先收到钱，否则等于先发货后收钱，系统也不会提醒欠款。
    线下单是否已收款由审计文案里的"收款状态"留痕，方便财务事后对。
    """
    if order.payment_method == "contact_manager":
        return
    if order.payment_status in PAID_PAYMENT_STATUSES:
        return
    raise HTTPException(
        status_code=409,
        detail="订单尚未收款，不能确认或交付；请先完成支付，或由财务登记线下收款",
    )


def settle_renewal(db: Session, order: Order, payment_status: str) -> CustomerLine:
    """续费订单收款/人工确认后延长线路。

    幂等：``renewal_completed_at`` 已写入时直接返回，重复回调不会叠加天数。
    调用方必须已持有订单行锁（``with_for_update``）。
    """
    if order.renewal_completed_at is not None:
        return db.get(CustomerLine, order.renewal_line_id)
    if order.status in {"cancelled", "refunding", "refunded"}:
        # 迟到回调兜底：订单已取消/退款时不能再延长线路，否则会出现"单子取消了、
        # 线路却续上了"。调用方应把支付单转人工核查而不是重试。
        raise HTTPException(status_code=409, detail="订单已取消或退款，不能完成续费")
    if not order.renewal_line_id:
        raise HTTPException(status_code=409, detail="续费订单缺少原线路信息")
    line = db.scalar(select(CustomerLine).where(
        CustomerLine.id == order.renewal_line_id,
        CustomerLine.customer_id == order.customer_id,
    ).with_for_update())
    if not line:
        raise HTTPException(status_code=409, detail="续费线路不存在或与订单客户不匹配")
    if line.status == "deleted":
        raise HTTPException(status_code=409, detail="线路已删除，不能续费")
    now = utcnow()
    line.expires_at = max(line.expires_at, now) + timedelta(days=order.billing_days)
    if line.status == "expired":
        line.status = "active"
    order.status = "completed"
    order.payment_status = payment_status
    order.renewal_completed_at = now
    customer = db.get(Customer, order.customer_id)
    if customer and customer.user_id:
        add_notification(
            db, customer.user_id, "线路续费已完成",
            f"续费订单 {order.order_no} 已完成，线路到期时间延长 {order.billing_days} 天。", "renewal",
        )
    return line


def rollback_renewal_extension(db: Session, order: Order) -> CustomerLine | None:
    """续费退款后回退延长天数。

    只在该线路此后没有其它已完成续费时才回退；否则交人工核对，避免把后续续费的
    天数一起扣掉。返回被调整的线路，未调整时返回 ``None``。
    """
    if not order.renewal_line_id or not order.renewal_completed_at:
        return None
    newer = db.scalar(select(Order.id).where(
        Order.renewal_line_id == order.renewal_line_id,
        Order.order_type == "renewal",
        Order.id != order.id,
        Order.renewal_completed_at.is_not(None),
        Order.renewal_completed_at > order.renewal_completed_at,
    ))
    if newer:
        return None
    line = db.scalar(select(CustomerLine).where(CustomerLine.id == order.renewal_line_id).with_for_update())
    if not line or line.status == "deleted":
        return None
    line.expires_at = line.expires_at - timedelta(days=order.billing_days)
    if line.status == "active" and line.expires_at <= utcnow():
        line.status = "expired"
    return line


def add_notification(db: Session, user_id: int | None, title: str, content: str, category: str) -> None:
    if user_id:
        db.add(Notification(user_id=user_id, title=title, content=content, category=category))


def notify_order_parties(db: Session, customer: Customer, order: Order, title: str, content: str, category: str) -> None:
    add_notification(db, customer.user_id, title, content, category)
    add_notification(db, order.assigned_sales_id, title, f"客户 {customer.name}：{content}", category)


def ensure_sales_user(db: Session, sales_id: int | None) -> User | None:
    if sales_id is None:
        return None
    sales = db.get(User, sales_id)
    if not sales or sales.role != "sales" or not sales.is_active:
        raise HTTPException(status_code=400, detail="指定销售账号不存在或不可用")
    return sales


def claim_delivery_slot(db: Session, order_id: int, delivered_at) -> bool:
    """原子占用一个交付名额，避免重复或并发交付超过订单数量。"""
    next_quantity = Order.delivered_quantity + 1
    result = db.execute(
        update(Order)
        .where(
            Order.id == order_id,
            Order.order_type == "purchase",
            Order.status.in_({"pending_delivery", "partially_delivered"}),
            Order.delivered_quantity < Order.quantity,
        )
        .values(
            delivered_quantity=next_quantity,
            status=case((next_quantity >= Order.quantity, "delivered"), else_="partially_delivered"),
            delivered_at=case((next_quantity >= Order.quantity, delivered_at), else_=Order.delivered_at),
        )
        .execution_options(synchronize_session=False)
    )
    return result.rowcount == 1
