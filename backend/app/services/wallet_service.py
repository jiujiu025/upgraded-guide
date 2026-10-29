from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Customer, SalesCommissionEntry, User, WalletAccount, WalletTransaction


COMMISSION_RATE_BASIS_POINTS = 1500


def money_to_cents(value) -> int:
    amount = Decimal(str(value))
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def ensure_wallet(db: Session, user_id: int, for_update: bool = False) -> WalletAccount:
    query = select(WalletAccount).where(WalletAccount.user_id == user_id)
    if for_update:
        query = query.with_for_update()
    account = db.scalar(query)
    if account:
        return account
    try:
        with db.begin_nested():
            account = WalletAccount(user_id=user_id, balance_cents=0, currency="CNY")
            db.add(account)
            db.flush()
        return account
    except IntegrityError:
        account = db.scalar(query)
        if not account:
            raise
        return account


def apply_wallet_delta(
    db: Session,
    user_id: int,
    amount_cents: int,
    transaction_type: str,
    idempotency_key: str,
    actor_user_id: int | None = None,
    payment_order_id: int | None = None,
    order_id: int | None = None,
    remark: str | None = None,
) -> WalletTransaction:
    existing = db.scalar(select(WalletTransaction).where(WalletTransaction.idempotency_key == idempotency_key))
    if existing:
        if existing.amount_cents != amount_cents or existing.transaction_type != transaction_type:
            raise ValueError("账本幂等键已被不同操作使用")
        return existing
    account = ensure_wallet(db, user_id, for_update=True)
    before = int(account.balance_cents)
    after = before + int(amount_cents)
    if amount_cents == 0 or after < 0:
        raise ValueError("余额不足或金额无效")
    account.balance_cents = after
    transaction = WalletTransaction(
        wallet_account_id=account.id,
        transaction_type=transaction_type,
        amount_cents=int(amount_cents),
        balance_before_cents=before,
        balance_after_cents=after,
        idempotency_key=idempotency_key,
        actor_user_id=actor_user_id,
        payment_order_id=payment_order_id,
        order_id=order_id,
        remark=remark,
    )
    db.add(transaction)
    customer = db.scalar(select(Customer).where(Customer.user_id == user_id))
    if customer:
        customer.balance = after / 100
    db.flush()
    return transaction


def accrue_sales_commission(
    db: Session,
    payment_order,
    customer: Customer,
    actor_user_id: int | None,
) -> SalesCommissionEntry | None:
    sales_id = payment_order.sales_id_snapshot or customer.sales_id
    if not sales_id:
        return None
    sales = db.get(User, sales_id)
    if not sales or sales.role != "sales":
        return None
    idempotency_key = f"order-paid:{payment_order.id}"
    existing = db.scalar(select(SalesCommissionEntry).where(SalesCommissionEntry.idempotency_key == idempotency_key))
    if existing:
        return existing
    basis = int(payment_order.amount_cents)
    commission = int((Decimal(basis) * Decimal(COMMISSION_RATE_BASIS_POINTS) / Decimal(10000)).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP,
    ))
    if commission <= 0:
        return None
    entry = SalesCommissionEntry(
        sales_id=sales.id,
        customer_id=customer.id,
        order_id=payment_order.order_id,
        payment_order_id=payment_order.id,
        amount_cents=commission,
        basis_cents=basis,
        rate_basis_points=COMMISSION_RATE_BASIS_POINTS,
        idempotency_key=idempotency_key,
        actor_user_id=actor_user_id,
        reason="客户订单已收款，按消费金额 15% 计提",
    )
    db.add(entry)
    db.flush()
    return entry


def reverse_sales_commission(db: Session, payment_order, actor_user_id: int, reason: str) -> SalesCommissionEntry | None:
    original_entries = db.scalars(select(SalesCommissionEntry).where(
        SalesCommissionEntry.payment_order_id == payment_order.id,
        SalesCommissionEntry.amount_cents > 0,
    )).all()
    if not original_entries:
        return None
    existing_key = f"order-refund:{payment_order.id}"
    existing = db.scalar(select(SalesCommissionEntry).where(SalesCommissionEntry.idempotency_key == existing_key))
    if existing:
        return existing
    original = original_entries[0]
    reversed_cents = sum(abs(item.amount_cents) for item in db.scalars(select(SalesCommissionEntry).where(
        SalesCommissionEntry.payment_order_id == payment_order.id,
        SalesCommissionEntry.amount_cents < 0,
    )).all())
    remaining = max(0, original.amount_cents - reversed_cents)
    if remaining == 0:
        return None
    entry = SalesCommissionEntry(
        sales_id=original.sales_id,
        customer_id=original.customer_id,
        order_id=original.order_id,
        payment_order_id=payment_order.id,
        amount_cents=-remaining,
        basis_cents=-int(payment_order.amount_cents),
        rate_basis_points=original.rate_basis_points,
        idempotency_key=existing_key,
        actor_user_id=actor_user_id,
        reason=reason,
    )
    db.add(entry)
    db.flush()
    return entry
