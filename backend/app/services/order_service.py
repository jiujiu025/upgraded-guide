from fastapi import HTTPException
from sqlalchemy import case, update
from sqlalchemy.orm import Session

from app.models import Customer, Notification, Order, Product, User
from app.services.pricing_service import PricingError, quote as _quote

PAYMENT_METHODS = {"balance", "online", "contact_manager"}
TERMINAL_ORDER_STATUSES = {"completed", "cancelled", "refunded"}


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
    if order_type == "renewal":
        return "renewal"
    return "pending_confirm" if payment_method == "contact_manager" else "pending_payment"


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
