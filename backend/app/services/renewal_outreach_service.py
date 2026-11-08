"""客户到期触达：把"该催谁"变成一张能直接照着做的清单。

为什么需要这层：到期提醒原来只写站内信（``app/tasks/lines.py``），而客户基本不登录，
真正的催续费发生在微信里。所以系统要做的不是"再发一条通知"，而是两件更朴素的事：

1. 告诉销售今天该找哪些人——按紧急度排好，顺带算好续费金额、写好一段能直接复制的话；
2. 记下"找过了"——谁、什么时候、什么渠道、说了什么、什么结果。

第 2 点不写，就会出现两个销售同时催同一个客户，或者客户明明答应过又没人跟进。
文案存快照：模板会随产品和价格变，事后回看要知道当时到底发的是哪句话。
"""

import math
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Customer, CustomerLine, Node, Product, Region, RenewalOutreach, User
from app.models.base import utcnow
from app.services.customer_scope import scoped_customer_ids
from app.services.pricing_service import PricingError, quote

DEFAULT_WINDOW_DAYS = 30
DEFAULT_OVERDUE_GRACE_DAYS = 15
MAX_ITEMS = 300

CHANNELS = {"wechat": "微信", "phone": "电话", "email": "邮件", "other": "其他"}
OUTCOMES = {
    "sent": "已发送，等回复",
    "promised": "已答应续费",
    "declined": "明确不续",
    "unreachable": "联系不上",
}


class OutreachError(ValueError):
    """入参或状态不合法。带上状态码，路由层不必再靠字符串猜。"""

    def __init__(self, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def days_left_of(expires_at: datetime, now: datetime) -> int:
    """剩余天数，向上取整；已过期返回负数。

    用 ceil 而不是 floor：剩 20 小时算 1 天，剩 0 小时算 0 天，
    这样"今天到期"和"还有 1 天"不会混成一档。
    """
    return math.ceil((expires_at - now).total_seconds() / 86400)


def bucket_of(days_left: int, *, urgent_days: int = 7) -> str:
    if days_left <= 0:
        return "overdue"
    if days_left <= urgent_days:
        return "urgent"
    return "soon"


def build_message(
    *,
    customer_name: str,
    line_name: str,
    product_name: str,
    expires_at: datetime,
    days_left: int,
    price: float | None,
    billing_days: int,
) -> str:
    """生成一段可以直接发微信的续费提醒。

    刻意写短、写具体：客户看三行就要知道"哪条线、什么时候到期、多少钱"。
    取不到价格时不给数字——宁可少一句，也不要报错价。
    """
    who = customer_name.strip() or "您好"
    line = line_name.strip() or product_name.strip() or "您的线路"
    # 没有节点名时 line 会退回商品名，此时再套一层括号就成了"香港 IP（香港 IP）"。
    label = line if not product_name.strip() or line == product_name.strip() else f"{line}（{product_name}）"
    if days_left > 0:
        when = f"将于 {expires_at:%Y-%m-%d} 到期（还剩 {days_left} 天）"
    elif days_left == 0:
        when = f"今天（{expires_at:%Y-%m-%d}）到期"
    else:
        when = f"已于 {expires_at:%Y-%m-%d} 到期（已过期 {-days_left} 天）"
    if price is None:
        tail = "需要我这边安排续费吗？具体金额我确认后发您。"
    else:
        tail = f"续费 {billing_days} 天为 ¥{price:.2f}，需要我这边直接安排吗？"
    return f"{who}，您好。{label}{when}。{tail}"


def _renewal_price(db: Session, product: Product) -> float | None:
    """续费同周期价格。缺价目表时返回 None，不让整条队列因为一个商品算不出价而报错。"""
    try:
        unit, _ = quote(db, product, 1, product.billing_days)
    except PricingError:
        return None
    return float(unit)


def _last_touches(db: Session, line_ids: list[int], now: datetime) -> dict[int, dict]:
    """每条线路最近一次触达，一次查询取完（避免列表 N+1）。"""
    if not line_ids:
        return {}
    rows = db.execute(
        select(RenewalOutreach, User.display_name)
        .join(User, RenewalOutreach.sales_id == User.id)
        .where(RenewalOutreach.line_id.in_(line_ids))
        .order_by(RenewalOutreach.line_id, RenewalOutreach.created_at.desc(), RenewalOutreach.id.desc())
    ).all()
    latest: dict[int, dict] = {}
    for touch, sales_name in rows:
        if touch.line_id in latest:
            continue
        latest[touch.line_id] = {
            "id": touch.id,
            "channel": touch.channel,
            "channel_label": CHANNELS.get(touch.channel, touch.channel),
            "outcome": touch.outcome,
            "outcome_label": OUTCOMES.get(touch.outcome, touch.outcome),
            "message": touch.message,
            "note": touch.note,
            "sales_name": sales_name,
            "created_at": touch.created_at,
            "days_ago": max(0, (now - touch.created_at).days),
        }
    return latest


def outreach_queue(
    db: Session,
    *,
    user: User,
    days: int = DEFAULT_WINDOW_DAYS,
    overdue_grace_days: int = DEFAULT_OVERDUE_GRACE_DAYS,
    now: datetime | None = None,
    limit: int = MAX_ITEMS,
) -> list[dict]:
    """待触达清单。

    既包含即将到期，也包含**刚过期**的线路：现实里最该打电话的是"昨天断了"的那批，
    只盯未来会让它们从列表里消失。过期超过 ``overdue_grace_days`` 的不再出现，
    该走的是重新开单而不是续费。
    """
    now = now or utcnow()
    horizon = now + timedelta(days=max(1, days))
    floor = now - timedelta(days=max(0, overdue_grace_days))
    rows = db.execute(
        select(CustomerLine, Customer, Product, Node)
        .join(Customer, CustomerLine.customer_id == Customer.id)
        .join(Product, CustomerLine.product_id == Product.id)
        .outerjoin(Node, CustomerLine.node_id == Node.id)
        .where(
            CustomerLine.customer_id.in_(scoped_customer_ids(db, user)),
            CustomerLine.status == "active",
            CustomerLine.expires_at <= horizon,
            CustomerLine.expires_at >= floor,
        )
        .order_by(CustomerLine.expires_at.asc(), CustomerLine.id.asc())
        .limit(max(1, min(limit, MAX_ITEMS)))
    ).all()

    line_ids = [line.id for line, *_ in rows]
    lasts = _last_touches(db, line_ids, now)
    regions = {
        region_id: name
        for region_id, name in db.execute(
            select(Product.region_id, Region.name)
            .join(Region, Product.region_id == Region.id)
            .where(Product.region_id.in_({row[2].region_id for row in rows}))
        ).all()
    }

    items = []
    for line, customer, product, node in rows:
        left = days_left_of(line.expires_at, now)
        price = _renewal_price(db, product)
        line_name = node.name if node else ""
        message = build_message(
            customer_name=customer.name,
            line_name=line_name,
            product_name=product.name,
            expires_at=line.expires_at,
            days_left=left,
            price=price,
            billing_days=product.billing_days,
        )
        items.append({
            "line_id": line.id,
            "customer_id": customer.id,
            "customer_name": customer.name,
            "contact": customer.phone or customer.email or "",
            "line_name": line_name or product.name,
            "product_name": product.name,
            "product_type": product.product_type,
            "region": regions.get(product.region_id, ""),
            "ip": line.static_ip,
            "expires_at": line.expires_at,
            "days_left": left,
            "bucket": bucket_of(left),
            "renewal_days": product.billing_days,
            "renewal_price": price,
            "price_missing": price is None,
            "last_touch": lasts.get(line.id),
            "suggested_message": message,
        })

    # 已过期的排最前，其余按剩余天数；同一天内按客户名，方便一次看完一个客户。
    items.sort(key=lambda item: (item["days_left"], item["customer_name"]))
    return items


def summary_of(items: list[dict]) -> dict:
    touched = sum(1 for item in items if item["last_touch"])
    return {
        "total": len(items),
        "overdue": sum(1 for item in items if item["bucket"] == "overdue"),
        "urgent": sum(1 for item in items if item["bucket"] == "urgent"),
        "soon": sum(1 for item in items if item["bucket"] == "soon"),
        "touched": touched,
        "untouched": len(items) - touched,
        # 答应续费但还没开单的，是真正会漏掉的钱
        "promised": sum(1 for item in items if (item["last_touch"] or {}).get("outcome") == "promised"),
    }


def line_for_touch(db: Session, *, user: User, line_id: int) -> tuple[CustomerLine, Customer, Product, Node | None]:
    row = db.execute(
        select(CustomerLine, Customer, Product, Node)
        .join(Customer, CustomerLine.customer_id == Customer.id)
        .join(Product, CustomerLine.product_id == Product.id)
        .outerjoin(Node, CustomerLine.node_id == Node.id)
        .where(CustomerLine.id == line_id)
    ).first()
    if not row:
        raise OutreachError("线路不存在", 404)
    line, customer, product, node = row
    # 用范围子查询判断归属，而不是再写一遍角色判断：销售、管理员、超管的可见性本就不同。
    visible = db.scalar(
        select(func.count()).select_from(Customer).where(
            Customer.id == customer.id,
            Customer.id.in_(scoped_customer_ids(db, user)),
        )
    )
    if not visible:
        raise OutreachError("无权操作该客户的线路", 403)
    return line, customer, product, node


def record_touch(
    db: Session,
    *,
    user: User,
    line_id: int,
    channel: str,
    outcome: str,
    message: str | None = None,
    note: str | None = None,
    now: datetime | None = None,
) -> RenewalOutreach:
    """记一次触达。

    ``message`` 不传就用当前模板生成——绝大多数情况销售就是复制粘贴那条文案。
    传了就以传入的为准，因为实际发出的可能被改过，留痕要留真话。
    """
    if channel not in CHANNELS:
        raise OutreachError(f"未知触达渠道：{channel}")
    if outcome not in OUTCOMES:
        raise OutreachError(f"未知触达结果：{outcome}")
    now = now or utcnow()
    line, customer, product, node = line_for_touch(db, user=user, line_id=line_id)
    left = days_left_of(line.expires_at, now)
    text = (message or "").strip()
    if not text:
        text = build_message(
            customer_name=customer.name,
            line_name=node.name if node else "",
            product_name=product.name,
            expires_at=line.expires_at,
            days_left=left,
            price=_renewal_price(db, product),
            billing_days=product.billing_days,
        )
    touch = RenewalOutreach(
        line_id=line.id,
        customer_id=customer.id,
        sales_id=user.id,
        channel=channel,
        outcome=outcome,
        message=text[:2000],
        days_left=left,
        expires_at=line.expires_at,
        note=(note or "").strip()[:1000] or None,
        created_at=now,
        updated_at=now,
    )
    db.add(touch)
    db.flush()
    return touch


def touch_history(
    db: Session,
    *,
    user: User,
    line_id: int | None = None,
    limit: int = 50,
) -> list[dict]:
    query = (
        select(RenewalOutreach, User.display_name, Customer.name)
        .join(User, RenewalOutreach.sales_id == User.id)
        .join(Customer, RenewalOutreach.customer_id == Customer.id)
        .where(RenewalOutreach.customer_id.in_(scoped_customer_ids(db, user)))
        .order_by(RenewalOutreach.created_at.desc(), RenewalOutreach.id.desc())
        .limit(max(1, min(limit, 200)))
    )
    if line_id is not None:
        query = query.where(RenewalOutreach.line_id == line_id)
    return [
        {
            "id": touch.id,
            "line_id": touch.line_id,
            "customer_name": customer_name,
            "sales_name": sales_name,
            "channel": touch.channel,
            "channel_label": CHANNELS.get(touch.channel, touch.channel),
            "outcome": touch.outcome,
            "outcome_label": OUTCOMES.get(touch.outcome, touch.outcome),
            "message": touch.message,
            "note": touch.note,
            "days_left": touch.days_left,
            "expires_at": touch.expires_at,
            "created_at": touch.created_at,
        }
        for touch, sales_name, customer_name in db.execute(query).all()
    ]
