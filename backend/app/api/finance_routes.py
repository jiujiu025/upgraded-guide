from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import current_user, require_roles
from app.core.config import get_settings
from app.db import get_db
from app.models import (
    Customer, Order, PaymentChannelConfig, PaymentOrder, PaymentRefund,
    SalesCommissionEntry, User, WalletAccount, WalletTransaction,
)
from app.services.audit_service import write_audit
from app.services.payment_gateway import (
    PAYMENT_API_URL, PaymentGatewayError, config_for_update, create_provider_payment,
    decrypt_secret, encrypt_secret, new_payment_order_values, verify_signature,
)
from app.services.wallet_service import (
    accrue_sales_commission, apply_wallet_delta, ensure_wallet, money_to_cents,
    reverse_sales_commission,
)


router = APIRouter(tags=["财务中心与钱包"])
SHANGHAI = ZoneInfo("Asia/Shanghai")


class OfflineRechargeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: int = Field(gt=0)
    amount_cents: int = Field(gt=0, le=10000000)
    idempotency_key: str = Field(min_length=16, max_length=100)
    remark: str = Field(min_length=2, max_length=500)


class PaymentConfigIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    merchant_id: str = Field(min_length=1, max_length=80)
    secret: str | None = Field(default=None, min_length=8, max_length=500)
    channel_types: list[str] = Field(min_length=1, max_length=8)
    min_amount_cents: int = Field(ge=100, le=10000000)
    max_amount_cents: int = Field(ge=100, le=10000000)
    online_enabled: bool = False


class OnlinePaymentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount_cents: int | None = Field(default=None, gt=0, le=10000000)
    payment_type: str = Field(min_length=2, max_length=30)
    idempotency_key: str = Field(min_length=16, max_length=100)


class IdempotencyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idempotency_key: str = Field(min_length=16, max_length=100)


class OfflineOrderPaymentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmed_received: bool
    external_reference: str = Field(min_length=2, max_length=120)
    remark: str = Field(min_length=2, max_length=500)


class RefundIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    refund_already_completed: bool
    reason: str = Field(min_length=2, max_length=500)
    external_reference: str | None = Field(default=None, max_length=120)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _amount(cents: int) -> str:
    whole, fraction = divmod(abs(int(cents)), 100)
    return f"{'-' if cents < 0 else ''}¥{whole}.{fraction:02d}"


def _payment_order_view(item: PaymentOrder, customer_name: str | None = None) -> dict:
    return {
        "id": item.id, "out_trade_no": item.out_trade_no, "user_id": item.user_id,
        "customer_name": customer_name, "purpose": item.purpose, "order_id": item.order_id,
        "amount_cents": item.amount_cents, "channel": item.channel, "provider_type": item.provider_type,
        "status": item.status, "platform_trade_no": item.platform_trade_no,
        "created_at": item.created_at, "paid_at": item.paid_at, "expires_at": item.expires_at,
    }


def _require_scoped_order(db: Session, user: User, order_id: int) -> tuple[Order, Customer]:
    order = db.scalar(select(Order).where(Order.id == order_id).with_for_update())
    customer = db.get(Customer, order.customer_id) if order else None
    if not order or not customer:
        raise HTTPException(status_code=404, detail="订单不存在")
    allowed = (user.role == "customer" and customer.user_id == user.id) or (
        user.role == "sales" and customer.sales_id == user.id
    )
    if not allowed:
        raise HTTPException(status_code=404, detail="订单不存在")
    return order, customer


def _record_paid_order(db: Session, payment_order: PaymentOrder, customer: Customer, actor: User | None) -> None:
    if not payment_order.order_id:
        return
    order = db.get(Order, payment_order.order_id)
    if not order:
        payment_order.status = "manual_review"
        return
    if order.source_type == "resource_pool":
        payment_order.status = "manual_review"
        return
    order.status = "paid"
    order.payment_status = "paid"
    accrue_sales_commission(db, payment_order, customer, actor.id if actor else None)


def _create_online_order(db: Session, user: User, payment_order: PaymentOrder, payment_type: str) -> dict:
    config = config_for_update(db)
    if not config.online_enabled:
        raise HTTPException(status_code=409, detail="在线充值尚未启用，管理员需先完成支付通道配置和沙箱验收")
    if payment_type not in (config.channel_types or []):
        raise HTTPException(status_code=422, detail="该支付方式未在商户后台确认开通")
    if not config.encrypted_secret or not config.merchant_id:
        raise HTTPException(status_code=409, detail="支付通道尚未完整配置")
    db.commit()
    try:
        url = create_provider_payment(config, payment_order, payment_type, user.role)
    except PaymentGatewayError as exc:
        payment_order.status = "manual_review" if exc.code in {
            "PAYMENT_TIMEOUT_UNKNOWN", "PAYMENT_INVALID_RESPONSE", "PAYMENT_CONNECTION_FAILED", "PAYMENT_HTTP_ERROR",
        } else "failed"
        db.commit()
        raise HTTPException(status_code=502, detail=exc.message) from exc
    payment_order.payment_url = url
    try:
        db.commit()
    except Exception:
        db.rollback()
        orphan = db.get(PaymentOrder, payment_order.id)
        if orphan and orphan.status == "pending":
            orphan.status = "manual_review"
            db.commit()
        raise HTTPException(status_code=503, detail="支付请求已发送但本地状态保存失败，请联系管理员核查，勿重复支付")
    return {"payment_order": _payment_order_view(payment_order), "payment_url": url}


@router.get("/admin/finance/summary")
def admin_finance_summary(
    date_from: date | None = None,
    date_to: date | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("super_admin", "admin")),
):
    today = datetime.now(SHANGHAI).date()
    end_day = date_to or today
    start_day = date_from or (end_day - timedelta(days=29))
    if end_day < start_day or (end_day - start_day).days > 89:
        raise HTTPException(status_code=422, detail="统计区间最多 90 天")
    start_utc = datetime.combine(start_day, time.min, SHANGHAI).astimezone(timezone.utc).replace(tzinfo=None)
    end_utc = datetime.combine(end_day + timedelta(days=1), time.min, SHANGHAI).astimezone(timezone.utc).replace(tzinfo=None)

    payments = db.scalars(select(PaymentOrder).where(
        PaymentOrder.paid_at >= start_utc, PaymentOrder.paid_at < end_utc,
        PaymentOrder.status.in_({"paid", "refunded"}),
    )).all()
    refunds = db.scalars(select(PaymentRefund).where(
        PaymentRefund.created_at >= start_utc, PaymentRefund.created_at < end_utc,
        PaymentRefund.status == "completed",
    )).all()
    commissions = db.scalars(select(SalesCommissionEntry).where(
        SalesCommissionEntry.created_at >= start_utc, SalesCommissionEntry.created_at < end_utc,
    )).all()
    customers = db.execute(select(Customer.created_at).where(
        Customer.user_id.is_not(None), Customer.created_at >= start_utc, Customer.created_at < end_utc,
    )).all()
    sales = db.execute(select(User.created_at).where(
        User.role == "sales", User.created_at >= start_utc, User.created_at < end_utc,
    )).all()

    buckets: dict[str, dict] = defaultdict(lambda: {
        "cash_received_cents": 0, "recharge_cents": 0, "sales_cents": 0,
        "refund_cents": 0, "commission_cents": 0, "new_customers": 0, "new_sales": 0,
    })
    for item in payments:
        day_key = item.paid_at.replace(tzinfo=timezone.utc).astimezone(SHANGHAI).date().isoformat()
        bucket = buckets[day_key]
        if item.purpose == "wallet_recharge":
            bucket["recharge_cents"] += item.amount_cents
        elif item.purpose == "order_payment":
            bucket["sales_cents"] += item.amount_cents
        if item.channel in {"online", "offline"}:
            bucket["cash_received_cents"] += item.amount_cents
    for item in refunds:
        day_key = item.created_at.replace(tzinfo=timezone.utc).astimezone(SHANGHAI).date().isoformat()
        bucket = buckets[day_key]
        bucket["refund_cents"] += item.amount_cents
        payment = db.get(PaymentOrder, item.payment_order_id)
        if payment and payment.purpose == "order_payment":
            bucket["sales_cents"] -= item.amount_cents
        if item.channel in {"online", "offline"}:
            bucket["cash_received_cents"] -= item.amount_cents
    for entry in commissions:
        day_key = entry.created_at.replace(tzinfo=timezone.utc).astimezone(SHANGHAI).date().isoformat()
        buckets[day_key]["commission_cents"] += entry.amount_cents
    for created_at, in customers:
        day_key = created_at.replace(tzinfo=timezone.utc).astimezone(SHANGHAI).date().isoformat()
        buckets[day_key]["new_customers"] += 1
    for created_at, in sales:
        day_key = created_at.replace(tzinfo=timezone.utc).astimezone(SHANGHAI).date().isoformat()
        buckets[day_key]["new_sales"] += 1

    daily = [{"date": (start_day + timedelta(days=index)).isoformat(), **buckets[(start_day + timedelta(days=index)).isoformat()]}
             for index in range((end_day - start_day).days + 1)]
    aggregate = {key: sum(day[key] for day in daily) for key in daily[0] if key != "date"}
    today_values = buckets[today.isoformat()]
    commission_rows = db.execute(
        select(User.id, User.display_name, func.coalesce(func.sum(SalesCommissionEntry.amount_cents), 0))
        .outerjoin(SalesCommissionEntry, SalesCommissionEntry.sales_id == User.id)
        .where(User.role == "sales")
        .group_by(User.id, User.display_name)
        .order_by(User.display_name)
    ).all()
    recent_rows = db.execute(select(PaymentOrder, User.display_name).join(User, User.id == PaymentOrder.user_id)
                             .order_by(PaymentOrder.created_at.desc()).limit(80)).all()
    return {
        "date_from": start_day.isoformat(), "date_to": end_day.isoformat(),
        "totals": aggregate,
        "today": today_values,
        "daily": daily,
        "sales_commissions": [{"sales_id": row[0], "display_name": row[1], "net_cents": int(row[2])}
                              for row in commission_rows],
        "recent_payments": [_payment_order_view(row[0], row[1]) for row in recent_rows],
    }


@router.get("/admin/finance/accounts")
def search_finance_accounts(
    q: str = Query(min_length=1, max_length=120),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("super_admin", "admin")),
):
    keyword = q.strip()
    if not keyword:
        return []
    query = select(User, Customer, WalletAccount).outerjoin(Customer, Customer.user_id == User.id).outerjoin(
        WalletAccount, WalletAccount.user_id == User.id,
    ).where(User.role.in_({"customer", "sales"}), User.is_active.is_(True))
    pattern = f"%{keyword}%"
    query = query.where(or_(User.display_name.ilike(pattern), User.username.ilike(pattern), User.phone.ilike(pattern),
                            Customer.name.ilike(pattern), Customer.phone.ilike(pattern), Customer.email.ilike(pattern)))
    rows = db.execute(query.order_by(User.display_name).limit(30)).all()
    return [{
        "user_id": user.id, "role": user.role, "role_label": "客户" if user.role == "customer" else "销售 / 代理",
        "username": user.username, "display_name": customer.name if customer else user.display_name,
        "phone": customer.phone if customer else user.phone, "balance_cents": wallet.balance_cents if wallet else 0,
    } for user, customer, wallet in rows]


@router.post("/admin/finance/offline-recharges", status_code=201)
def create_offline_recharge(
    payload: OfflineRechargeIn,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("super_admin", "admin")),
):
    target = db.scalar(select(User).where(User.id == payload.user_id, User.role.in_({"customer", "sales"}), User.is_active.is_(True)))
    customer = db.scalar(select(Customer).where(Customer.user_id == payload.user_id, Customer.is_deleted.is_(False)))
    if not target or (target.role == "customer" and not customer):
        raise HTTPException(status_code=404, detail="客户或销售账号不存在或不可用")
    idem = f"offline-recharge:{actor.id}:{payload.idempotency_key}"
    existing = db.scalar(select(PaymentOrder).where(PaymentOrder.idempotency_key == idem))
    if existing:
        if existing.user_id != target.id or existing.amount_cents != payload.amount_cents or existing.purpose != "wallet_recharge":
            raise HTTPException(status_code=409, detail="充值幂等键已被其他请求使用")
        return _payment_order_view(existing, customer.name if customer else target.display_name)
    now = _now_utc()
    item = PaymentOrder(
        out_trade_no=f"OFF{now.strftime('%Y%m%d%H%M%S')}{uuid4().hex[:10].upper()}",
        idempotency_key=idem, user_id=target.id, purpose="wallet_recharge", amount_cents=payload.amount_cents,
        channel="offline", status="paid", paid_at=now,
    )
    try:
        db.add(item)
        db.flush()
        apply_wallet_delta(db, target.id, payload.amount_cents, "offline_recharge", f"payment:{idem}",
                           actor.id, item.id, remark=payload.remark)
        write_audit(db, actor, "offline_recharge", "wallet", target.id,
                    f"充值金额分={payload.amount_cents}; 收款账号角色={target.role}; 凭证备注={payload.remark}")
        db.commit()
        db.refresh(item)
        return _payment_order_view(item, customer.name if customer else target.display_name)
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(PaymentOrder).where(PaymentOrder.idempotency_key == idem))
        if existing:
            if existing.user_id != target.id or existing.amount_cents != payload.amount_cents or existing.purpose != "wallet_recharge":
                raise HTTPException(status_code=409, detail="充值幂等键已被其他请求使用")
            return _payment_order_view(existing, customer.name if customer else target.display_name)
        raise
    except Exception:
        db.rollback()
        raise


@router.get("/admin/finance/payment-config")
def get_payment_config(db: Session = Depends(get_db), _: User = Depends(require_roles("super_admin"))):
    config = db.get(PaymentChannelConfig, 1)
    return {
        "merchant_id": (config.merchant_id or "") if config else "", "secret_configured": bool(config.encrypted_secret) if config else False,
        "channel_types": (config.channel_types or []) if config else [], "min_amount_cents": config.min_amount_cents if config else 100,
        "max_amount_cents": config.max_amount_cents if config else 10000000, "online_enabled": config.online_enabled if config else False,
        "sandbox_verified": bool(config.verified_at) if config else False, "verified_at": config.verified_at if config else None,
        "provider_url": PAYMENT_API_URL,
    }


@router.put("/admin/finance/payment-config")
def update_payment_config(
    payload: PaymentConfigIn,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("super_admin")),
):
    if payload.max_amount_cents < payload.min_amount_cents:
        raise HTTPException(status_code=422, detail="单笔上限不能小于单笔下限")
    if any(not value.strip() or len(value) > 30 for value in payload.channel_types):
        raise HTTPException(status_code=422, detail="支付方式编码无效")
    config = config_for_update(db)
    merchant_changed = config.merchant_id != payload.merchant_id.strip()
    secret_changed = payload.secret is not None and payload.secret != ""
    config.merchant_id = payload.merchant_id.strip()
    config.channel_types = sorted(set(value.strip() for value in payload.channel_types))
    config.min_amount_cents = payload.min_amount_cents
    config.max_amount_cents = payload.max_amount_cents
    if secret_changed:
        try:
            config.encrypted_secret = encrypt_secret(payload.secret)
        except PaymentGatewayError as exc:
            raise HTTPException(status_code=503, detail=exc.message) from exc
    if merchant_changed or secret_changed:
        config.verified_at = None
        config.online_enabled = False
    if payload.online_enabled:
        raise HTTPException(status_code=409, detail="在线收款保持关闭；支付域名连通性检查不能代替真实沙箱、回调验签和小额支付核对")
    config.online_enabled = False
    write_audit(db, actor, "payment_config_update", "payment_channel", 1,
                f"商户号已配置; 支付方式={','.join(config.channel_types)}; 线上收款={'启用' if config.online_enabled else '关闭'}")
    db.commit()
    return {"ok": True, "secret_configured": bool(config.encrypted_secret), "online_enabled": config.online_enabled,
            "sandbox_verified": bool(config.verified_at)}


@router.post("/admin/finance/payment-config/health")
def check_payment_api(db: Session = Depends(get_db), actor: User = Depends(require_roles("super_admin"))):
    config = db.get(PaymentChannelConfig, 1)
    if not config or not config.merchant_id or not config.encrypted_secret:
        return {"status": "not_configured", "message": "商户号或密钥尚未配置；未发送任何凭据"}
    try:
        response = httpx.get(PAYMENT_API_URL, timeout=6, follow_redirects=False, headers={"Accept": "application/json"})
        reachable = response.status_code < 500
        status = "reachable" if reachable else "provider_error"
        message = "官方支付 API 域名可连接；本检查不验证商户密钥、支付方式或真实收款能力" if reachable else "支付 API 返回服务端错误；未验证商户密钥"
        code = response.status_code
    except httpx.TimeoutException:
        status, message, code = "timeout", "连接官方支付 API 超时；未发送商户凭据", None
    except httpx.HTTPError:
        status, message, code = "connection_failed", "无法连接官方支付 API；未发送商户凭据", None
    write_audit(db, actor, "payment_api_connectivity_check", "payment_channel", 1, f"状态={status}; HTTP={code or '无响应'}")
    db.commit()
    return {"status": status, "message": message, "http_status": code, "credential_validated": False}


@router.get("/wallet")
def wallet_home(db: Session = Depends(get_db), user: User = Depends(require_roles("customer", "sales"))):
    account = db.scalar(select(WalletAccount).where(WalletAccount.user_id == user.id))
    transactions = db.scalars(select(WalletTransaction).where(
        WalletTransaction.wallet_account_id == account.id if account else WalletTransaction.id == -1,
    ).order_by(WalletTransaction.created_at.desc()).limit(50)).all()
    payments = db.scalars(select(PaymentOrder).where(PaymentOrder.user_id == user.id)
                          .order_by(PaymentOrder.created_at.desc()).limit(30)).all()
    if user.role == "customer":
        customer = db.scalar(select(Customer).where(Customer.user_id == user.id, Customer.is_deleted.is_(False)))
        customer_id = customer.id if customer else -1
        pending_query = select(Order).where(Order.customer_id == customer_id)
    else:
        pending_query = select(Order).where(Order.assigned_sales_id == user.id)
    pending_orders = db.scalars(pending_query.where(Order.status == "pending_payment", Order.source_type != "resource_pool")
                                .order_by(Order.created_at.desc()).limit(100)).all()
    config = db.get(PaymentChannelConfig, 1)
    return {
        "balance_cents": account.balance_cents if account else 0,
        "payment_options": {
            "online_enabled": bool(config and config.online_enabled),
            "channel_types": (config.channel_types or []) if config else [],
            "min_amount_cents": config.min_amount_cents if config else 100,
            "max_amount_cents": config.max_amount_cents if config else 10000000,
        },
        "transactions": [{"id": item.id, "type": item.transaction_type, "amount_cents": item.amount_cents,
                          "balance_after_cents": item.balance_after_cents, "remark": item.remark,
                          "created_at": item.created_at, "order_id": item.order_id} for item in transactions],
        "payments": [_payment_order_view(item) for item in payments],
        "pending_orders": [{"id": item.id, "order_no": item.order_no, "product_name": item.product_name,
                            "total_cents": money_to_cents(item.total_amount), "created_at": item.created_at,
                            "can_pay_online": item.payment_method == "online"} for item in pending_orders],
    }


@router.post("/wallet/recharges", status_code=201)
def create_online_recharge(payload: OnlinePaymentIn, db: Session = Depends(get_db), user: User = Depends(require_roles("customer", "sales"))):
    if payload.amount_cents is None:
        raise HTTPException(status_code=422, detail="请输入充值金额")
    config = config_for_update(db)
    if not config.online_enabled:
        raise HTTPException(status_code=409, detail="在线充值通道尚未启用")
    if not config.min_amount_cents <= payload.amount_cents <= config.max_amount_cents:
        raise HTTPException(status_code=422, detail="充值金额超出单笔限额")
    idem = f"online-recharge:{user.id}:{payload.idempotency_key}"
    existing = db.scalar(select(PaymentOrder).where(PaymentOrder.idempotency_key == idem))
    if existing:
        if existing.payment_url:
            return {"payment_order": _payment_order_view(existing), "payment_url": existing.payment_url}
        raise HTTPException(status_code=409, detail="该充值请求已受理或待人工核查，请勿重复提交")
    item = PaymentOrder(**new_payment_order_values(user.id, "wallet_recharge", payload.amount_cents, "online"))
    item.idempotency_key = idem
    item.provider_type = payload.payment_type
    db.add(item)
    try:
        db.flush()
        return _create_online_order(db, user, item, payload.payment_type)
    except HTTPException:
        raise
    except Exception:
        db.rollback()
        raise


@router.post("/wallet/orders/{order_id}/pay-balance")
def pay_order_with_balance(order_id: int, payload: IdempotencyIn, db: Session = Depends(get_db), user: User = Depends(require_roles("customer", "sales"))):
    order, customer = _require_scoped_order(db, user, order_id)
    if order.source_type == "resource_pool":
        raise HTTPException(status_code=409, detail="资源池订单支付功能尚未启用")
    prior = db.scalar(select(PaymentOrder).where(PaymentOrder.order_id == order.id, PaymentOrder.status == "paid"))
    if prior:
        return {"status": "paid", "order_id": order.id, "amount_cents": prior.amount_cents}
    online_pending = db.scalar(select(PaymentOrder.id).where(
        PaymentOrder.order_id == order.id, PaymentOrder.channel == "online",
        PaymentOrder.status.in_({"pending", "manual_review"}),
    ))
    if online_pending:
        raise HTTPException(status_code=409, detail="订单已有在线支付请求待确认，请先核对支付状态")
    if order.status != "pending_payment":
        raise HTTPException(status_code=409, detail="订单当前状态不能进行余额支付")
    amount_cents = money_to_cents(order.total_amount)
    payment = PaymentOrder(**new_payment_order_values(user.id, "order_payment", amount_cents, "balance", order.id, customer.sales_id))
    payment.status = "paid"
    payment.paid_at = _now_utc()
    payment.idempotency_key = f"balance-order:{order.id}"
    db.add(payment)
    try:
        db.flush()
        apply_wallet_delta(db, user.id, -amount_cents, "order_payment", f"balance-order:{order.id}",
                           user.id, payment.id, order.id, f"订单 {order.order_no} 余额支付")
        order.status = "paid"
        order.payment_status = "paid"
        accrue_sales_commission(db, payment, customer, user)
        write_audit(db, user, "wallet_order_payment", "order", order.id, f"金额分={amount_cents}; 佣金比例=15%")
        db.commit()
        return {"status": "paid", "order_id": order.id, "amount_cents": amount_cents}
    except IntegrityError:
        db.rollback()
        prior = db.scalar(select(PaymentOrder).where(PaymentOrder.order_id == order.id, PaymentOrder.status == "paid"))
        if prior:
            return {"status": "paid", "order_id": order.id, "amount_cents": prior.amount_cents}
        raise HTTPException(status_code=409, detail="余额支付正在处理，请刷新后核对状态")
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception:
        db.rollback()
        raise


@router.post("/wallet/orders/{order_id}/pay-online", status_code=201)
def pay_order_online(order_id: int, payload: OnlinePaymentIn, db: Session = Depends(get_db), user: User = Depends(require_roles("customer", "sales"))):
    order, customer = _require_scoped_order(db, user, order_id)
    if order.source_type == "resource_pool":
        raise HTTPException(status_code=409, detail="资源池订单支付功能尚未启用")
    if order.status != "pending_payment":
        raise HTTPException(status_code=409, detail="订单当前状态不能进行在线支付")
    existing = db.scalar(select(PaymentOrder).where(
        PaymentOrder.order_id == order.id, PaymentOrder.status.in_({"pending", "manual_review"}),
    ))
    if existing:
        if existing.payment_url:
            return {"payment_order": _payment_order_view(existing), "payment_url": existing.payment_url}
        raise HTTPException(status_code=409, detail="在线支付订单待核查，请勿重复提交")
    amount_cents = money_to_cents(order.total_amount)
    config = config_for_update(db)
    if not config.min_amount_cents <= amount_cents <= config.max_amount_cents:
        raise HTTPException(status_code=422, detail="订单金额超出支付通道单笔限额")
    payment = PaymentOrder(**new_payment_order_values(user.id, "order_payment", amount_cents, "online", order.id, customer.sales_id))
    payment.idempotency_key = f"online-order:{order.id}:{payload.idempotency_key}"
    payment.provider_type = payload.payment_type
    db.add(payment)
    try:
        db.flush()
        return _create_online_order(db, user, payment, payload.payment_type)
    except HTTPException:
        raise
    except Exception:
        db.rollback()
        raise


@router.get("/payments/suisui/notify")
async def suisui_payment_notify(request: Request, response: Response, db: Session = Depends(get_db)):
    pairs = request.query_params.multi_items()
    parameters = dict(pairs)
    if len(parameters) != len(pairs):
        return Response(content="fail", media_type="text/plain", status_code=400)
    config = db.get(PaymentChannelConfig, 1)
    if not config or not config.encrypted_secret or not config.merchant_id:
        return Response(content="fail", media_type="text/plain", status_code=503)
    try:
        secret = decrypt_secret(config.encrypted_secret)
    except PaymentGatewayError:
        return Response(content="fail", media_type="text/plain", status_code=503)
    if not verify_signature(parameters, secret) or parameters.get("pid") != config.merchant_id:
        return Response(content="fail", media_type="text/plain", status_code=403)
    out_trade_no = parameters.get("out_trade_no", "")
    candidate = db.scalar(select(PaymentOrder).where(PaymentOrder.out_trade_no == out_trade_no))
    if not candidate:
        return Response(content="fail", media_type="text/plain", status_code=404)
    order = None
    if candidate.purpose == "order_payment" and candidate.order_id:
        order = db.scalar(select(Order).where(Order.id == candidate.order_id).with_for_update().execution_options(populate_existing=True))
    item = db.scalar(select(PaymentOrder).where(PaymentOrder.id == candidate.id).with_for_update().execution_options(populate_existing=True))
    if not item:
        return Response(content="fail", media_type="text/plain", status_code=404)
    if item.status in {"paid", "refunded"}:
        return Response(content="success", media_type="text/plain")
    try:
        amount = Decimal(parameters.get("money", "")) * 100
    except InvalidOperation:
        return Response(content="fail", media_type="text/plain", status_code=400)
    if not amount.is_finite() or amount != amount.to_integral_value() or int(amount) != item.amount_cents:
        item.status = "manual_review"
        db.commit()
        return Response(content="success", media_type="text/plain")
    if item.provider_type and parameters.get("type") != item.provider_type:
        item.status = "manual_review"
        db.commit()
        return Response(content="success", media_type="text/plain")
    if parameters.get("trade_status") != "TRADE_SUCCESS":
        return Response(content="success", media_type="text/plain")
    if item.status not in {"pending", "manual_review"} or (item.expires_at and item.expires_at < _now_utc()):
        item.status = "manual_review"
        db.commit()
        return Response(content="success", media_type="text/plain")
    item.status = "paid"
    item.platform_trade_no = parameters.get("trade_no", "")[:100] or None
    item.paid_at = _now_utc()
    if item.purpose == "wallet_recharge":
        apply_wallet_delta(db, item.user_id, item.amount_cents, "online_recharge",
                           f"payment:{item.idempotency_key}", None, item.id, remark="在线充值到账")
    elif item.purpose == "order_payment":
        customer = db.get(Customer, order.customer_id) if order else None
        if not customer or order.status != "pending_payment" or order.source_type == "resource_pool":
            item.status = "manual_review"
        else:
            _record_paid_order(db, item, customer, None)
    db.commit()
    return Response(content="success", media_type="text/plain")


@router.post("/admin/finance/orders/{order_id}/offline-payment")
def record_offline_order_payment(order_id: int, payload: OfflineOrderPaymentIn, db: Session = Depends(get_db), actor: User = Depends(require_roles("super_admin", "admin"))):
    if not payload.confirmed_received:
        raise HTTPException(status_code=409, detail="必须确认线下款项已实际到账")
    order = db.scalar(select(Order).where(Order.id == order_id).with_for_update())
    if not order or order.source_type == "resource_pool":
        raise HTTPException(status_code=404, detail="订单不存在或来源不支持线下收款登记")
    if order.status != "pending_payment":
        raise HTTPException(status_code=409, detail="订单当前状态不能登记收款")
    customer = db.get(Customer, order.customer_id)
    if not customer or not customer.user_id:
        raise HTTPException(status_code=409, detail="客户账号不可用")
    amount = money_to_cents(order.total_amount)
    item = PaymentOrder(**new_payment_order_values(customer.user_id, "order_payment", amount, "offline", order.id, customer.sales_id))
    item.status = "paid"
    item.paid_at = _now_utc()
    item.platform_trade_no = payload.external_reference.strip()
    db.add(item)
    try:
        db.flush()
        order.status = "paid"
        order.payment_status = "manually_confirmed"
        accrue_sales_commission(db, item, customer, actor.id)
        write_audit(db, actor, "offline_order_payment", "order", order.id,
                    f"线下收款金额分={amount}; 凭证={payload.external_reference}; 备注={payload.remark}")
        db.commit()
        return _payment_order_view(item, customer.name)
    except Exception:
        db.rollback()
        raise


@router.post("/admin/finance/orders/{order_id}/refund")
def record_completed_refund(order_id: int, payload: RefundIn, db: Session = Depends(get_db), actor: User = Depends(require_roles("super_admin", "admin"))):
    if not payload.refund_already_completed:
        raise HTTPException(status_code=409, detail="此入口只登记已实际完成的退款，不会发起转账或调用支付平台")
    order = db.scalar(select(Order).where(Order.id == order_id).with_for_update())
    if not order or order.source_type == "resource_pool":
        raise HTTPException(status_code=404, detail="订单不存在或来源不支持退款登记")
    payment = db.scalar(select(PaymentOrder).where(
        PaymentOrder.order_id == order.id, PaymentOrder.purpose == "order_payment", PaymentOrder.status == "paid",
    ).order_by(PaymentOrder.paid_at.desc()))
    if not payment:
        raise HTTPException(status_code=409, detail="未找到已收款记录，不能登记退款")
    if db.scalar(select(PaymentRefund.id).where(PaymentRefund.payment_order_id == payment.id)):
        raise HTTPException(status_code=409, detail="该收款已登记退款")
    if payment.channel != "balance" and not payload.external_reference:
        raise HTTPException(status_code=422, detail="线下或在线退款需填写外部退款凭证号")
    customer = db.get(Customer, order.customer_id)
    if not customer:
        raise HTTPException(status_code=409, detail="订单客户不存在")
    refund = PaymentRefund(
        payment_order_id=payment.id, amount_cents=payment.amount_cents, channel=payment.channel,
        external_reference=payload.external_reference, reason=payload.reason, actor_user_id=actor.id,
        status="completed",
    )
    db.add(refund)
    try:
        db.flush()
        if payment.channel == "balance":
            apply_wallet_delta(db, payment.user_id, payment.amount_cents, "order_refund",
                               f"refund:{payment.id}", actor.id, payment.id, order.id, payload.reason)
        payment.status = "refunded"
        order.status = "refunded"
        order.payment_status = "refunded"
        reverse_sales_commission(db, payment, actor.id, f"订单退款冲销佣金：{payload.reason}")
        write_audit(db, actor, "order_refund_completed", "order", order.id,
                    f"退款金额分={payment.amount_cents}; 渠道={payment.channel}; 外部凭证={payload.external_reference or '余额退回'}; 原因={payload.reason}")
        db.commit()
        return {"status": "refunded", "order_id": order.id, "amount_cents": payment.amount_cents}
    except Exception:
        db.rollback()
        raise
