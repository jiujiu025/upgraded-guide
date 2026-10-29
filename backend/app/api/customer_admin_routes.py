from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import BigInteger, String, case, cast, func, literal, or_, select
from sqlalchemy.orm import Session, aliased

from app.api.deps import require_roles
from app.db import get_db
from app.models import (
    AuditLog, Customer, CustomerLine, Order, PaymentOrder, PaymentRefund,
    Product, Region, User, WalletAccount, WalletTransaction, Node,
)
from app.services.audit_service import write_audit


router = APIRouter(prefix="/admin/customers", tags=["管理员客户管理"])


class CustomerUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    email: str | None = Field(default=None, min_length=3, max_length=160)
    phone: str | None = Field(default=None, max_length=40)
    status: str | None = Field(default=None, min_length=1, max_length=30)
    level: str | None = Field(default=None, min_length=1, max_length=20)
    sales_id: int | None = Field(default=None, gt=0)


def _customer_or_404(db: Session, customer_id: int) -> Customer:
    customer = db.scalar(select(Customer).where(
        Customer.id == customer_id,
        Customer.is_deleted.is_(False),
    ))
    if not customer:
        raise HTTPException(status_code=404, detail="客户不存在")
    return customer


def _customer_audit_filter(customer: Customer, account: User | None):
    filters = [
        (AuditLog.resource_type == "customer") & (AuditLog.resource_id == str(customer.id)),
        (AuditLog.resource_type == "order") & AuditLog.resource_id.in_(
            select(cast(Order.id, String)).where(Order.customer_id == customer.id)
        ),
        (AuditLog.resource_type == "customer_line") & AuditLog.resource_id.in_(
            select(cast(CustomerLine.id, String)).where(CustomerLine.customer_id == customer.id)
        ),
    ]
    if account:
        filters.extend([
            (AuditLog.resource_type == "user") & (AuditLog.resource_id == str(account.id)),
            (AuditLog.resource_type == "wallet") & (AuditLog.resource_id == str(account.id)),
        ])
    return or_(*filters)


@router.get("")
def list_admin_customers(
    keyword: str | None = Query(default=None, max_length=120),
    sales_id: int | None = Query(default=None, gt=0),
    status: str | None = Query(default=None, max_length=30),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("super_admin", "admin")),
):
    sales_user = aliased(User)
    account_user = aliased(User)
    query = (
        select(
            Customer, sales_user.display_name, sales_user.username,
            account_user.username, WalletAccount.balance_cents, account_user.is_active,
        )
        .outerjoin(sales_user, Customer.sales_id == sales_user.id)
        .outerjoin(account_user, Customer.user_id == account_user.id)
        .outerjoin(WalletAccount, WalletAccount.user_id == Customer.user_id)
        .where(Customer.is_deleted.is_(False))
    )
    filters = []
    if keyword and keyword.strip():
        pattern = f"%{keyword.strip()}%"
        filters.append(or_(
            Customer.name.ilike(pattern),
            Customer.email.ilike(pattern),
            Customer.phone.ilike(pattern),
            account_user.username.ilike(pattern),
            account_user.display_name.ilike(pattern),
            account_user.phone.ilike(pattern),
        ))
    if sales_id is not None:
        filters.append(Customer.sales_id == sales_id)
    if status:
        filters.append(Customer.status == status)
    if filters:
        query = query.where(*filters)

    count_query = select(func.count()).select_from(Customer).where(Customer.is_deleted.is_(False))
    if filters:
        count_query = count_query.outerjoin(account_user, Customer.user_id == account_user.id).where(*filters)
    total = db.scalar(count_query) or 0
    rows = db.execute(
        query.order_by(Customer.created_at.desc(), Customer.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    customer_ids = [row[0].id for row in rows]
    line_counts = {}
    if customer_ids:
        line_counts = dict(db.execute(
            select(CustomerLine.customer_id, func.count(CustomerLine.id))
            .where(CustomerLine.customer_id.in_(customer_ids), CustomerLine.status != "deleted")
            .group_by(CustomerLine.customer_id)
        ).all())

    items = []
    for customer, sales_name, sales_username, account_username, balance_cents, account_active in rows:
        items.append({
            "id": customer.id,
            "name": customer.name,
            "email": customer.email,
            "phone": customer.phone,
            "status": customer.status,
            "level": customer.level,
            "sales_id": customer.sales_id,
            "sales_name": sales_name,
            "sales_username": sales_username,
            "user_id": customer.user_id,
            "username": account_username,
            "account_active": account_active,
            "balance_cents": int(balance_cents if balance_cents is not None else round(customer.balance * 100)),
            "line_count": line_counts.get(customer.id, 0),
            "created_at": customer.created_at,
        })
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/sales-options")
def list_customer_sales_options(
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("super_admin", "admin")),
):
    sales = db.scalars(select(User).where(User.role == "sales").order_by(User.display_name, User.id)).all()
    return [{"id": item.id, "display_name": item.display_name, "username": item.username, "is_active": item.is_active} for item in sales]


@router.get("/{customer_id}")
def get_admin_customer(
    customer_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("super_admin", "admin")),
):
    customer = _customer_or_404(db, customer_id)
    account = db.scalar(select(User).where(User.id == customer.user_id)) if customer.user_id else None
    sales = db.get(User, customer.sales_id) if customer.sales_id else None
    wallet = db.scalar(select(WalletAccount).where(WalletAccount.user_id == customer.user_id)) if customer.user_id else None

    audit_filter = _customer_audit_filter(customer, account)

    wallet_count = db.scalar(
        select(func.count()).select_from(WalletTransaction)
        .join(WalletAccount, WalletTransaction.wallet_account_id == WalletAccount.id)
        .where(WalletAccount.user_id == customer.user_id)
    ) if customer.user_id else 0
    payment_count = db.scalar(select(func.count()).select_from(PaymentOrder).where(
        PaymentOrder.user_id == customer.user_id,
        ~select(1).where(WalletTransaction.payment_order_id == PaymentOrder.id).exists(),
    )) if customer.user_id else 0
    refund_count = db.scalar(
        select(func.count()).select_from(PaymentRefund)
        .join(PaymentOrder, PaymentRefund.payment_order_id == PaymentOrder.id)
        .where(
            PaymentOrder.user_id == customer.user_id,
            ~select(1).where(
                WalletTransaction.payment_order_id == PaymentRefund.payment_order_id,
                WalletTransaction.transaction_type == "order_refund",
            ).exists(),
        )
    ) if customer.user_id else 0
    audit_total = db.scalar(select(func.count()).select_from(AuditLog).where(audit_filter)) or 0
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    line_total = db.scalar(select(func.count()).select_from(CustomerLine).where(
        CustomerLine.customer_id == customer.id, CustomerLine.status != "deleted",
    )) or 0
    active_line_total = db.scalar(select(func.count()).select_from(CustomerLine).where(
        CustomerLine.customer_id == customer.id,
        CustomerLine.status == "active",
        CustomerLine.expires_at > now,
    )) or 0

    return {
        "customer": {
            "id": customer.id,
            "name": customer.name,
            "email": customer.email,
            "phone": customer.phone,
            "status": customer.status,
            "level": customer.level,
            "sales_id": customer.sales_id,
            "sales_name": sales.display_name if sales else None,
            "user_id": customer.user_id,
            "username": account.username if account else None,
            "account_active": account.is_active if account else None,
            "balance_cents": int(wallet.balance_cents if wallet else round(customer.balance * 100)),
            "created_at": customer.created_at,
            "updated_at": customer.updated_at,
        },
        "lines": [],
        "transactions": [],
        "audit_logs": [],
        "record_totals": {
            "lines": line_total,
            "active_lines": active_line_total,
            "transactions": int(wallet_count or 0) + int(payment_count or 0) + int(refund_count or 0),
            "audit_logs": audit_total,
        },
    }


@router.get("/{customer_id}/records")
def get_admin_customer_records(
    customer_id: int,
    kind: str = Query(pattern="^(lines|transactions|audit_logs)$"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    expiry: str = Query(default="all", pattern="^(all|expired|7d|30d|90d)$"),
    keyword: str | None = Query(default=None, max_length=120),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("super_admin", "admin")),
):
    customer = _customer_or_404(db, customer_id)
    if kind == "lines":
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        line_query = (
            select(CustomerLine, Product, Region, Node)
            .join(Product, CustomerLine.product_id == Product.id)
            .join(Region, Product.region_id == Region.id)
            .join(Node, CustomerLine.node_id == Node.id)
            .where(CustomerLine.customer_id == customer.id, CustomerLine.status != "deleted")
        )
        if keyword and keyword.strip():
            pattern = f"%{keyword.strip()}%"
            line_query = line_query.where(or_(
                Product.name.ilike(pattern), Product.product_type.ilike(pattern),
                Region.name.ilike(pattern), Node.name.ilike(pattern),
                CustomerLine.static_ip.ilike(pattern),
            ))
        if expiry == "expired":
            line_query = line_query.where(or_(
                CustomerLine.status == "expired",
                (CustomerLine.status == "active") & (CustomerLine.expires_at <= now),
            ))
        elif expiry != "all":
            days = int(expiry[:-1])
            line_query = line_query.where(
                CustomerLine.status == "active",
                CustomerLine.expires_at > now,
                CustomerLine.expires_at <= now + timedelta(days=days),
            )
        total = db.scalar(select(func.count()).select_from(line_query.subquery())) or 0
        rows = db.execute(line_query.order_by(CustomerLine.expires_at.asc(), CustomerLine.id.asc()).offset(offset).limit(limit)).all()
        items = [{
            "id": line.id, "product_name": product.name, "product_type": product.product_type,
            "region": region.name, "node_name": node.name, "static_ip": line.static_ip,
            "status": line.status, "started_at": line.started_at, "expires_at": line.expires_at,
            "customer_note": line.customer_note, "order_id": line.order_id,
        } for line, product, region, node in rows]
    elif kind == "audit_logs":
        account = db.get(User, customer.user_id) if customer.user_id else None
        audit_filter = _customer_audit_filter(customer, account)
        total = db.scalar(select(func.count()).select_from(AuditLog).where(audit_filter)) or 0
        rows = db.execute(
            select(AuditLog, User.display_name, User.username)
            .join(User, AuditLog.actor_id == User.id)
            .where(audit_filter)
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .offset(offset)
            .limit(limit)
        ).all()
        items = [{
            "id": item.id, "action": item.action, "resource_type": item.resource_type,
            "detail": item.detail, "actor": actor_name or actor_username,
            "created_at": item.created_at,
        } for item, actor_name, actor_username in rows]
    else:
        user_id = customer.user_id
        if not user_id:
            return {"items": [], "total": 0, "offset": offset, "limit": limit}
        empty_cents = cast(literal(None), BigInteger)
        empty_text = cast(literal(None), String)
        wallet_rows = select(
            literal("wallet").label("source"), WalletTransaction.id.label("source_id"),
            WalletTransaction.created_at.label("created_at"), WalletTransaction.transaction_type.label("type"),
            WalletTransaction.amount_cents.label("amount_cents"), WalletTransaction.balance_before_cents.label("before_cents"),
            WalletTransaction.balance_after_cents.label("after_cents"), literal("wallet").label("channel"),
            literal("completed").label("status"), Order.order_no.label("order_no"),
            PaymentOrder.out_trade_no.label("out_trade_no"), WalletTransaction.remark.label("remark"),
        ).join(WalletAccount, WalletTransaction.wallet_account_id == WalletAccount.id)
        wallet_rows = wallet_rows.outerjoin(Order, WalletTransaction.order_id == Order.id).outerjoin(
            PaymentOrder, WalletTransaction.payment_order_id == PaymentOrder.id,
        ).where(WalletAccount.user_id == user_id)
        payment_rows = select(
            literal("payment").label("source"), PaymentOrder.id.label("source_id"),
            func.coalesce(PaymentOrder.paid_at, PaymentOrder.created_at).label("created_at"),
            case((PaymentOrder.purpose == "order_payment", "order_payment"), else_=PaymentOrder.purpose).label("type"),
            case(
                (PaymentOrder.purpose == "order_payment", -PaymentOrder.amount_cents),
                else_=PaymentOrder.amount_cents,
            ).label("amount_cents"), empty_cents.label("before_cents"),
            empty_cents.label("after_cents"), PaymentOrder.channel.label("channel"),
            PaymentOrder.status.label("status"), Order.order_no.label("order_no"),
            PaymentOrder.out_trade_no.label("out_trade_no"), empty_text.label("remark"),
        ).outerjoin(Order, PaymentOrder.order_id == Order.id).where(
            PaymentOrder.user_id == user_id,
            ~select(1).where(WalletTransaction.payment_order_id == PaymentOrder.id).exists(),
        )
        refund_rows = select(
            literal("refund").label("source"), PaymentRefund.id.label("source_id"),
            PaymentRefund.created_at.label("created_at"), literal("refund").label("type"),
            PaymentRefund.amount_cents.label("amount_cents"), empty_cents.label("before_cents"),
            empty_cents.label("after_cents"), PaymentRefund.channel.label("channel"),
            PaymentRefund.status.label("status"), Order.order_no.label("order_no"),
            PaymentOrder.out_trade_no.label("out_trade_no"), PaymentRefund.reason.label("remark"),
        ).join(PaymentOrder, PaymentRefund.payment_order_id == PaymentOrder.id).outerjoin(
            Order, PaymentOrder.order_id == Order.id,
        ).where(
            PaymentOrder.user_id == user_id,
            ~select(1).where(
                WalletTransaction.payment_order_id == PaymentRefund.payment_order_id,
                WalletTransaction.transaction_type == "order_refund",
            ).exists(),
        )
        combined = wallet_rows.union_all(payment_rows, refund_rows).subquery()
        total = db.scalar(select(func.count()).select_from(combined)) or 0
        rows = db.execute(
            select(combined)
            .order_by(combined.c.created_at.desc(), combined.c.source_id.desc(), combined.c.source.desc())
            .offset(offset)
            .limit(limit)
        ).mappings().all()
        items = [{
            "id": f"{row['source']}-{row['source_id']}", "created_at": row["created_at"],
            "type": row["type"], "amount_cents": row["amount_cents"],
            "balance_before_cents": row["before_cents"], "balance_after_cents": row["after_cents"],
            "channel": row["channel"], "status": row["status"],
            "order_no": row["order_no"], "out_trade_no": row["out_trade_no"],
            "reference": row["order_no"] or row["out_trade_no"], "remark": row["remark"],
        } for row in rows]

    return {"items": items, "total": total, "offset": offset, "limit": limit}


@router.patch("/{customer_id}")
def update_admin_customer(
    customer_id: int,
    payload: CustomerUpdateIn,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("super_admin", "admin")),
):
    customer = _customer_or_404(db, customer_id)
    changes = payload.model_dump(exclude_unset=True)
    for field in ("name", "email", "status", "level"):
        if field in changes and changes[field] is None:
            raise HTTPException(status_code=422, detail=f"{field} 不能为空")
    if "sales_id" in changes and changes["sales_id"] is not None and changes["sales_id"] != customer.sales_id:
        sales = db.scalar(select(User).where(
            User.id == changes["sales_id"], User.role == "sales", User.is_active.is_(True),
        ))
        if not sales:
            raise HTTPException(status_code=422, detail="请选择有效的在职销售账号")

    changed_fields = []
    for field, value in changes.items():
        if getattr(customer, field) != value:
            setattr(customer, field, value)
            changed_fields.append(field)
    if customer.user_id:
        account = db.get(User, customer.user_id)
        if account:
            if "name" in changes and account.display_name != customer.name:
                account.display_name = customer.name
                changed_fields.append("account_display_name")
            if "phone" in changes and account.phone != customer.phone:
                account.phone = customer.phone
                changed_fields.append("account_phone")
    if changed_fields:
        write_audit(db, actor, "update_customer", "customer", customer.id,
                    "修改字段=" + ",".join(sorted(set(changed_fields))))
        db.commit()
        db.refresh(customer)
    return get_admin_customer(customer.id, db, actor)
