from decimal import Decimal, ROUND_HALF_UP

from fastapi import HTTPException
from sqlalchemy import case, update
from sqlalchemy.orm import Session

from app.models import Customer, Notification, Order, Product, User

PAYMENT_METHODS = {"balance", "online", "contact_manager"}
TERMINAL_ORDER_STATUSES = {"completed", "cancelled", "refunded"}
PERIOD_FACTORS = {1: Decimal("1"), 6: Decimal("5.5"), 12: Decimal("10")}


def calculate_order_price(product: Product, quantity: int, billing_days: int) -> tuple[float, float]:
    """只使用数据库产品价格，按基础周期、半年和年度周期计算。"""
    if product.billing_days <= 0 or billing_days % product.billing_days:
        raise HTTPException(status_code=422, detail="使用周期不合法")
    multiplier = billing_days // product.billing_days
    factor = PERIOD_FACTORS.get(multiplier)
    if factor is None:
        raise HTTPException(status_code=422, detail="仅支持基础周期、6 个周期或 12 个周期")
    unit_price = Decimal(str(product.price))
    total = (unit_price * quantity * factor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    discount = (factor / Decimal(multiplier)).quantize(Decimal("0.0001"))
    return float(total), float(discount)


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
