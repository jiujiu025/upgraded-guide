from datetime import datetime, timedelta, timezone
import unicodedata
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, Header
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import current_user, require_roles
from app.core.security import create_access_token, hash_password, verify_password
from app.core.client_ip import get_client_ip
from app.db import get_db
from app.models import (
    AuthSession, Customer, CustomerLine, HealthIncident, Node, Notification, Order, Product, Region, RetailPriceRule,
    SalesLoginEvent, SupplierIpSegmentSnapshot, SupplierProduct, SupplierQuote, SupplierRegion, User,
)
from app.schemas.common import (
    AssignCustomerIn, CompleteRenewalIn, CreateSalesUserIn, CustomerIn, CustomerLineNoteIn, CustomerLineOut, CustomerOut,
    DeliverOrderIn, LineIn, LineOut, LoginIn, NotificationOut, OrderIn, RegisterIn,
    OrderOut, OrderPreviewIn, OrderPreviewOut, PasswordRecoveryIn, ProductOut, PublicProductOut,
    RegionOut, RenewalIn, TokenOut, UpdateSalesUserIn, UserOut,
    SecurityQuestionsUpdateIn,
)
from app.services.audit_service import write_audit
from app.services.customer_scope import get_customer_profile, scoped_customer_ids
from app.services.health_service import check_node
from app.services.order_service import (
    TERMINAL_ORDER_STATUSES, add_notification, calculate_order_price, claim_delivery_slot,
    ensure_sales_user, initial_order_status, notify_order_parties,
)
from app.services.supplier_service import RESOURCE_CATEGORY_CODES
from app.services.registration_service import (enforce_login_rate_limit, enforce_password_recovery_rate_limit,
    enforce_registration_rate_limit)
from app.services.session_service import (SessionError, create_session, csrf_token, find_refresh,
    revoke_session, revoke_user_sessions, rotate_session, validate_session)
from app.core.config import get_settings
import hmac
import hashlib

router = APIRouter()
LOGIN_ERROR_DETAIL = "用户名或密码错误"
DUMMY_PASSWORD_HASH = hash_password("rate-limit-timing-placeholder")
REFRESH_COOKIE = "__Host-ops_refresh"


class ChangePasswordIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: str
    new_password: str = Field(min_length=10, max_length=128)


class ResetPasswordIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    new_password: str = Field(min_length=10, max_length=128)
    confirm: bool


class UserAccessIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: str | None = None
    is_active: bool | None = None


class AdminResourceOrderIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    customer_id: int = Field(gt=0)
    product_id: int = Field(gt=0)
    supplier_product_id: int = Field(gt=0)
    supplier_region_id: int = Field(gt=0)
    supplier_segment_snapshot_id: int = Field(gt=0)
    supplier_quote_id: int = Field(gt=0)
    idempotency_key: str = Field(min_length=36, max_length=64)
    resource_category: str
    quantity: int = Field(ge=1, le=300)
    billing_days: int = Field(ge=30, le=360)
    remark: str | None = Field(default=None, max_length=1000)


def _strong_password(value: str) -> bool:
    import re
    return all(re.search(pattern, value) for pattern in (r"[a-z]", r"[A-Z]", r"\d", r"[^A-Za-z0-9]"))


def _normalize_security_answer(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _security_answer_digest(value: str) -> str:
    settings = get_settings()
    pepper = settings.session_token_hash_key.get_secret_value() if settings.session_token_hash_key else settings.jwt_secret_key
    return hmac.new(pepper.encode("utf-8"), _normalize_security_answer(value).encode("utf-8"), hashlib.sha256).hexdigest()


def _cookie(response: Response, raw: str, expires_at: datetime) -> None:
    max_age = max(0, int((expires_at - utcnow()).total_seconds()))
    response.set_cookie(REFRESH_COOKIE, raw, max_age=max_age, path="/", secure=True,
                        httponly=True, samesite="lax")


def _clear_cookie(response: Response) -> None:
    response.delete_cookie(REFRESH_COOKIE, path="/", secure=True, httponly=True, samesite="lax")


def _origin(request: Request) -> None:
    if request.headers.get("origin") != get_settings().frontend_origin:
        raise HTTPException(status_code=403, detail="请求来源无效")


def _optional_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin and origin != get_settings().frontend_origin:
        raise HTTPException(status_code=403, detail="请求来源无效")


def _csrf(request: Request, session: AuthSession, generation: int | None = None) -> None:
    supplied = request.headers.get("x-csrf-token", "")
    if not hmac.compare_digest(supplied, csrf_token(session, generation)):
        raise HTTPException(status_code=403, detail="CSRF 校验失败")


def _token_out(user: User, session: AuthSession) -> TokenOut:
    return TokenOut(access_token=create_access_token(user.id, user.role, session.id, user.auth_version),
                    csrf_token=csrf_token(session), user=user)


def visible_node_ids(db: Session, user: User):
    if user.role in {"super_admin", "admin"}:
        return select(Node.id)
    if user.role == "sales":
        customer_ids = scoped_customer_ids(db, user)
    else:
        customer_ids = select(Customer.id).where(Customer.id == get_customer_profile(db, user).id)
    return select(CustomerLine.node_id).where(CustomerLine.customer_id.in_(customer_ids))


def _record_sales_login(db: Session, user: User | None, request: Request, outcome: str, failure_reason: str | None = None) -> None:
    if not user or user.role != "sales":
        return
    user_agent = request.headers.get("user-agent", "")
    db.add(SalesLoginEvent(
        user_id=user.id,
        outcome=outcome,
        source_ip=get_client_ip(request),
        user_agent="".join(char for char in user_agent if char.isprintable())[:512] or None,
        failure_reason=failure_reason,
    ))


def visible_incidents_query(db: Session, user: User):
    return select(HealthIncident).where(HealthIncident.node_id.in_(visible_node_ids(db, user)))


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def customer_line_payload(db: Session, line: CustomerLine) -> dict:
    product = db.get(Product, line.product_id)
    node = db.get(Node, line.node_id)
    region = db.get(Region, product.region_id) if product else None
    return {
        "id": line.id,
        "region": region.name if region else "未知地区",
        "ip": line.static_ip,
        "product_type": product.product_type if product else "未知产品",
        "line_name": node.name if node else "线路数据待接入",
        "status": line.status,
        "started_at": line.started_at,
        "expires_at": line.expires_at,
        "customer_note": line.customer_note,
        "node_link": line.node_link,
        "subscription_url": line.subscription_url,
        "node_qr_data": line.node_qr_data,
        "subscription_qr_data": line.subscription_qr_data,
        "clash_import": line.subscription_url,
        "v2ray_import": line.node_link,
        "nekobox_import": line.subscription_url,
    }


def build_order(db: Session, user: User, customer: Customer, payload: OrderIn, order_type: str = "purchase", renewal_line_id: int | None = None) -> Order:
    product = db.scalar(select(Product).where(Product.id == payload.product_id, Product.is_active.is_(True)))
    if not product or not product.is_in_stock:
        raise HTTPException(status_code=404, detail="产品不存在、已停用或暂时无货")
    region_id = payload.region_id or product.region_id
    region = db.scalar(select(Region).where(Region.id == region_id, Region.is_active.is_(True)))
    if region_id != product.region_id or not region:
        raise HTTPException(status_code=422, detail="产品与地区不匹配")
    billing_days = payload.billing_days or product.billing_days
    total_amount, _ = calculate_order_price(product, payload.quantity, billing_days)
    order = Order(
        order_no=f"ORD-{datetime.now().strftime('%Y%m%d')}-{uuid4().hex[:8].upper()}",
        customer_id=customer.id,
        product_id=product.id,
        amount=total_amount,
        quantity=payload.quantity,
        billing_days=billing_days,
        unit_price=product.price,
        total_amount=total_amount,
        region_id=product.region_id,
        region_name=region.name,
        product_name=product.name,
        payment_method=payload.payment_method,
        payment_status="unpaid",
        assigned_sales_id=customer.sales_id,
        status=initial_order_status(payload.payment_method, order_type),
        order_type=order_type,
        renewal_line_id=renewal_line_id,
        remark=payload.remark,
    )
    db.add(order)
    db.flush()
    if order_type == "renewal":
        notify_order_parties(db, customer, order, "续费订单已创建", f"续费订单 {order.order_no} 已创建，原线路到期时间未变更。", "renewal")
    else:
        notify_order_parties(db, customer, order, "订单已创建", f"订单 {order.order_no} 已创建，等待后续处理。", "order")
    write_audit(db, user, "create", "order", order.id, f"类型={order_type}; 数量={payload.quantity}; 周期={billing_days}")
    return order


@router.post("/auth/login", response_model=TokenOut)
def login(
    payload: LoginIn,
    response: Response,
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(enforce_login_rate_limit),
):
    if request.headers.get("origin") and request.headers["origin"] != get_settings().frontend_origin:
        raise HTTPException(status_code=403, detail="请求来源无效")
    user = db.scalar(select(User).where(User.username == payload.username))
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    password_valid = verify_password(payload.password, user.password_hash if user else DUMMY_PASSWORD_HASH)
    is_locked = bool(user and user.locked_until and user.locked_until > now)
    if not user or not user.is_active or is_locked or not password_valid:
        failure_reason = "account_disabled" if user and not user.is_active else "account_locked" if is_locked else "invalid_credentials"
        _record_sales_login(db, user, request, "failed", failure_reason)
        if user and user.is_active and not is_locked:
            if user.locked_until and user.locked_until <= now:
                user.failed_login_count = 0
                user.locked_until = None
            user.failed_login_count += 1
            if user.failed_login_count >= 5:
                user.locked_until = now + timedelta(minutes=15)
        if user:
            db.commit()
        raise HTTPException(status_code=401, detail=LOGIN_ERROR_DETAIL)
    user.failed_login_count = 0
    user.locked_until = None
    user.must_change_password = False
    session, raw = create_session(db, user)
    _record_sales_login(db, user, request, "success")
    db.commit()
    _cookie(response, raw, session.expires_at)
    return _token_out(user, session)


@router.get("/auth/me", response_model=UserOut)
def me(user: User = Depends(current_user)):
    return user


@router.get("/auth/csrf")
def get_csrf(request: Request, response: Response, db: Session = Depends(get_db)):
    _optional_origin(request)
    response.headers["Cache-Control"] = "no-store"
    try:
        session, token, user = find_refresh(db, request.cookies.get(REFRESH_COOKIE))
        validate_session(session, user)
        if token.consumed_at or token.generation != session.generation:
            raise SessionError("登录已失效")
        return {"csrf_token": csrf_token(session)}
    except SessionError as exc:
        raise HTTPException(status_code=401, detail="登录已失效") from exc


@router.post("/auth/refresh", response_model=TokenOut)
def refresh(request: Request, response: Response, db: Session = Depends(get_db)):
    _origin(request)
    try:
        session, token, user = find_refresh(db, request.cookies.get(REFRESH_COOKIE))
        _csrf(request, session, token.generation)
        raw = rotate_session(db, session, token, user)
        _cookie(response, raw, session.expires_at)
        return _token_out(user, session)
    except SessionError as exc:
        _clear_cookie(response)
        raise HTTPException(status_code=401, detail="登录已失效") from exc


@router.post("/auth/logout", status_code=204)
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    _origin(request)
    try:
        session, token, _ = find_refresh(db, request.cookies.get(REFRESH_COOKIE))
        _csrf(request, session, token.generation)
        revoke_session(session, "logout")
        db.commit()
    except SessionError:
        pass
    _clear_cookie(response)


@router.post("/auth/change-password", status_code=204)
def change_password(payload: ChangePasswordIn, response: Response, db: Session = Depends(get_db),
                    user: User = Depends(current_user)):
    if not _strong_password(payload.new_password):
        raise HTTPException(status_code=422, detail="新密码必须包含大小写字母、数字和特殊字符")
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=401, detail="当前密码错误")
    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = False
    revoke_user_sessions(db, user, "password_changed")
    write_audit(db, user, "change_password", "user", user.id)
    db.commit()
    _clear_cookie(response)


@router.get("/auth/security-questions")
def get_security_questions(response: Response, user: User = Depends(current_user)):
    response.headers["Cache-Control"] = "no-store"
    return {
        "configured": bool(user.security_question_1 and user.security_answer_hash_1
                           and user.security_question_2 and user.security_answer_hash_2),
        "question_1": user.security_question_1,
        "question_2": user.security_question_2,
    }


@router.put("/auth/security-questions", status_code=204)
def update_security_questions(payload: SecurityQuestionsUpdateIn, response: Response, db: Session = Depends(get_db),
                              user: User = Depends(current_user)):
    response.headers["Cache-Control"] = "no-store"
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=401, detail="当前密码错误")
    user.security_question_1 = payload.question_1
    user.security_answer_hash_1 = hash_password(_security_answer_digest(payload.answer_1))
    user.security_question_2 = payload.question_2
    user.security_answer_hash_2 = hash_password(_security_answer_digest(payload.answer_2))
    write_audit(db, user, "update_security_questions", "user", user.id, "更新两项密保问题")
    db.commit()


@router.post("/auth/password-recovery", status_code=200)
def recover_password(payload: PasswordRecoveryIn, request: Request, response: Response, db: Session = Depends(get_db)):
    if request.headers.get("origin") and request.headers["origin"] != get_settings().frontend_origin:
        raise HTTPException(status_code=403, detail="请求来源无效")
    response.headers["Cache-Control"] = "no-store"
    enforce_password_recovery_rate_limit(request, payload.username)
    user = db.scalar(select(User).where(User.username == payload.username))
    answer_1_hash = user.security_answer_hash_1 if user and user.security_answer_hash_1 else DUMMY_PASSWORD_HASH
    answer_2_hash = user.security_answer_hash_2 if user and user.security_answer_hash_2 else DUMMY_PASSWORD_HASH
    answer_1_digest = _security_answer_digest(payload.answer_1)
    answer_2_digest = _security_answer_digest(payload.answer_2)
    answer_1_matches_first = verify_password(answer_1_digest, answer_1_hash)
    answer_1_matches_second = verify_password(answer_1_digest, answer_2_hash)
    answer_2_matches_first = verify_password(answer_2_digest, answer_1_hash)
    answer_2_matches_second = verify_password(answer_2_digest, answer_2_hash)
    normal_order_matches = (
        user is not None
        and user.security_question_1 == payload.question_1
        and user.security_question_2 == payload.question_2
        and answer_1_matches_first
        and answer_2_matches_second
    )
    reverse_order_matches = (
        user is not None
        and user.security_question_2 == payload.question_1
        and user.security_question_1 == payload.question_2
        and answer_1_matches_second
        and answer_2_matches_first
    )
    answers_match = bool(user and user.is_active and (normal_order_matches or reverse_order_matches))
    if answers_match and user:
        user.password_hash = hash_password(payload.new_password)
        user.must_change_password = False
        revoke_user_sessions(db, user, "password_recovered")
        write_audit(db, user, "password_recovered", "user", user.id, "通过密保重置密码并撤销全部会话")
        db.commit()
    return {"message": "如账号和密保信息匹配，密码已重置；请使用新密码登录。"}


@router.get("/customers", response_model=list[CustomerOut])
def list_customers(keyword: str | None = None, db: Session = Depends(get_db), user: User = Depends(current_user)):
    query = select(Customer).where(Customer.id.in_(scoped_customer_ids(db, user)))
    if keyword:
        query = query.where(Customer.name.contains(keyword) | Customer.email.contains(keyword))
    return list(db.scalars(query.order_by(Customer.created_at.desc()).limit(200)).all())


@router.post("/customers", response_model=CustomerOut)
def create_customer(payload: CustomerIn, db: Session = Depends(get_db), user: User = Depends(require_roles("super_admin", "admin", "sales"))):
    sales_id = payload.sales_id if user.role in {"super_admin", "admin"} else user.id
    customer = Customer(**payload.model_dump(exclude={"sales_id"}), sales_id=sales_id)
    db.add(customer)
    write_audit(db, user, "create", "customer", detail=customer.name)
    db.commit()
    db.refresh(customer)
    return customer


@router.get("/customers/{customer_id}", response_model=CustomerOut)
def get_customer(customer_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    customer = db.scalar(select(Customer).where(Customer.id == customer_id, Customer.id.in_(scoped_customer_ids(db, user))))
    if not customer:
        raise HTTPException(status_code=404, detail="客户不存在或无权访问")
    return customer


@router.get("/products", response_model=list[ProductOut])
def list_products(db: Session = Depends(get_db), user: User = Depends(require_roles("super_admin", "admin"))):
    return list(db.scalars(select(Product).where(Product.is_active.is_(True)).order_by(Product.id)).all())


@router.get("/regions", response_model=list[RegionOut])
def list_regions(db: Session = Depends(get_db), user: User = Depends(require_roles("super_admin", "admin"))):
    return list(db.scalars(select(Region).where(Region.is_active.is_(True)).order_by(Region.id)).all())


@router.get("/lines", response_model=list[LineOut])
def list_lines(db: Session = Depends(get_db), user: User = Depends(current_user)):
    query = select(CustomerLine)
    if user.role == "customer":
        query = query.where(CustomerLine.customer_id == get_customer_profile(db, user).id)
    elif user.role == "sales":
        query = query.where(CustomerLine.customer_id.in_(scoped_customer_ids(db, user)))
    return list(db.scalars(query.order_by(CustomerLine.expires_at)).all())


@router.get("/lines/{line_id}", response_model=LineOut)
def get_line(line_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    line = db.get(CustomerLine, line_id)
    if not line:
        raise HTTPException(status_code=404, detail="线路不存在")
    allowed = user.role in {"super_admin", "admin"}
    if user.role == "sales":
        allowed = db.scalar(select(Customer.id).where(Customer.id == line.customer_id, Customer.sales_id == user.id, Customer.is_deleted.is_(False))) is not None
    elif user.role == "customer":
        allowed = line.customer_id == get_customer_profile(db, user).id
    if not allowed:
        raise HTTPException(status_code=404, detail="线路不存在或无权访问")
    return line


@router.post("/lines", response_model=LineOut)
def create_line(payload: LineIn, db: Session = Depends(get_db), user: User = Depends(require_roles("super_admin", "admin"))):
    customer = db.scalar(select(Customer).where(Customer.id == payload.customer_id, Customer.is_deleted.is_(False)))
    node = db.get(Node, payload.node_id)
    product = db.get(Product, payload.product_id)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if not customer or not customer.user_id:
        raise HTTPException(status_code=400, detail="客户不存在或未绑定账号")
    if not node or not node.is_enabled or not product or not product.is_active:
        raise HTTPException(status_code=400, detail="节点或产品不存在或已停用")
    if node.region_id != product.region_id:
        raise HTTPException(status_code=400, detail="节点地区与产品地区不一致")
    if payload.expires_at.replace(tzinfo=None) <= now:
        raise HTTPException(status_code=400, detail="到期时间必须晚于当前时间")
    line = CustomerLine(**payload.model_dump(), started_at=now)
    try:
        db.add(line)
        write_audit(db, user, "create", "customer_line", detail="手动创建线路")
        db.commit()
        db.refresh(line)
        return line
    except Exception:
        db.rollback()
        raise


@router.get("/orders", response_model=list[OrderOut])
def list_orders(db: Session = Depends(get_db), user: User = Depends(current_user)):
    query = select(Order)
    if user.role == "customer":
        query = query.where(Order.customer_id == get_customer_profile(db, user).id)
    elif user.role == "sales":
        query = query.where(Order.customer_id.in_(scoped_customer_ids(db, user)))
    return list(db.scalars(query.order_by(Order.created_at.desc())).all())


@router.post("/orders", response_model=OrderOut)
def create_order(payload: OrderIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    if user.role != "customer":
        raise HTTPException(status_code=400, detail="客户订单必须以客户身份创建")
    customer = get_customer_profile(db, user)
    try:
        order = build_order(db, user, customer, payload)
        db.commit()
        db.refresh(order)
        return order
    except Exception:
        db.rollback()
        raise


@router.post("/orders/{order_id}/confirm", response_model=OrderOut)
def confirm_order(order_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles("super_admin", "admin"))):
    return admin_confirm_order(order_id, db, user)


@router.post("/orders/{order_id}/deliver", response_model=CustomerLineOut)
def deliver_order(order_id: int, payload: DeliverOrderIn, db: Session = Depends(get_db), user: User = Depends(require_roles("super_admin", "admin"))):
    return admin_deliver_order(order_id, payload, db, user)


@router.get("/health/summary")
def health_summary(db: Session = Depends(get_db), user: User = Depends(current_user)):
    query = select(Node)
    if user.role == "sales":
        query = query.where(Node.id.in_(select(CustomerLine.node_id).where(CustomerLine.customer_id.in_(scoped_customer_ids(db, user)))))
    elif user.role == "customer":
        query = query.where(Node.id.in_(visible_node_ids(db, user)))
    nodes = list(db.scalars(query).all())
    counts = {status: sum(1 for n in nodes if n.health_status == status) for status in ["healthy", "degraded", "high_latency", "offline", "unknown", "maintenance"]}
    latencies = [n.health_latency_ms for n in nodes if n.health_latency_ms is not None]
    incident_query = visible_incidents_query(db, user).where(HealthIncident.status == "open")
    return {"total": len(nodes), "counts": counts, "average_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else 0, "open_incidents": db.scalar(select(func.count()).select_from(incident_query.subquery())) or 0}


@router.get("/health/lines")
def health_lines(db: Session = Depends(get_db), user: User = Depends(current_user)):
    query = select(Node)
    if user.role == "sales":
        query = query.where(Node.id.in_(select(CustomerLine.node_id).where(CustomerLine.customer_id.in_(scoped_customer_ids(db, user)))))
    elif user.role == "customer":
        query = query.where(Node.id.in_(visible_node_ids(db, user)))
    return list(db.scalars(query.order_by(Node.health_status, Node.name)).all())


@router.get("/health/incidents")
def health_incidents(db: Session = Depends(get_db), user: User = Depends(current_user)):
    query = visible_incidents_query(db, user)
    return list(db.scalars(query.order_by(HealthIncident.created_at.desc())).all())


@router.post("/health/lines/{node_id}/check")
def health_check(node_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles("super_admin", "admin"))):
    node = db.get(Node, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="线路不存在")
    return check_node(db, node)


@router.get("/notifications", response_model=list[NotificationOut])
def notifications(db: Session = Depends(get_db), user: User = Depends(current_user)):
    if user.role == "customer":
        get_customer_profile(db, user)
    return list(db.scalars(select(Notification).where(Notification.user_id == user.id).order_by(Notification.created_at.desc()).limit(100)).all())


@router.post("/notifications/{notification_id}/read")
def mark_notification_read(notification_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    item = db.scalar(select(Notification).where(Notification.id == notification_id, Notification.user_id == user.id))
    if not item:
        raise HTTPException(status_code=404, detail="通知不存在")
    item.is_read = True
    db.commit()
    return {"ok": True}


@router.post("/notifications/read-all")
def mark_all_notifications_read(db: Session = Depends(get_db), user: User = Depends(current_user)):
    items = db.scalars(select(Notification).where(Notification.user_id == user.id, Notification.is_read.is_(False))).all()
    for item in items:
        item.is_read = True
    db.commit()
    return {"ok": True, "updated": len(items)}


# 官网公开购买接口：仅返回销售所需字段。
@router.post("/public/register", response_model=TokenOut, status_code=201)
def public_register(
    payload: RegisterIn,
    response: Response,
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(enforce_registration_rate_limit),
):
    """创建客户账号及绑定档案，不接受角色或销售归属字段。"""
    if request.headers.get("origin") and request.headers["origin"] != get_settings().frontend_origin:
        raise HTTPException(status_code=403, detail="请求来源无效")
    password_hash = hash_password(payload.password)
    if db.scalar(select(User.id).where(User.username == payload.username)):
        raise HTTPException(status_code=400, detail="无法完成注册，请检查提交信息")
    try:
        user = User(
            username=payload.username,
            password_hash=password_hash,
            role="customer",
            display_name=payload.name,
            is_active=True,
        )
        db.add(user)
        db.flush()
        db.add(Customer(
            name=payload.name,
            email=payload.email,
            phone=payload.phone,
            user_id=user.id,
            sales_id=None,
        ))
        session, raw = create_session(db, user)
        db.commit()
        db.refresh(user)
        _cookie(response, raw, session.expires_at)
        return _token_out(user, session)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail="无法完成注册，请检查提交信息") from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail="注册暂时不可用，请稍后重试") from exc


@router.get("/public/products", response_model=list[PublicProductOut])
def public_products(db: Session = Depends(get_db)):
    rows = db.execute(select(Product, Region).join(Region, Product.region_id == Region.id).where(Product.is_active.is_(True), Region.is_active.is_(True)).order_by(Product.id)).all()
    return [{**ProductOut.model_validate(product).model_dump(), "region": region.name} for product, region in rows]


@router.get("/public/products/{product_id}", response_model=PublicProductOut)
def public_product(product_id: int, db: Session = Depends(get_db)):
    row = db.execute(select(Product, Region).join(Region, Product.region_id == Region.id).where(Product.id == product_id, Product.is_active.is_(True), Region.is_active.is_(True))).first()
    if not row:
        raise HTTPException(status_code=404, detail="产品不存在或已停用")
    product, region = row
    return {**ProductOut.model_validate(product).model_dump(), "region": region.name}


@router.get("/public/regions", response_model=list[RegionOut])
def public_regions(db: Session = Depends(get_db)):
    return list(db.scalars(select(Region).where(Region.is_active.is_(True)).order_by(Region.id)).all())


@router.post("/public/orders/preview", response_model=OrderPreviewOut)
def public_order_preview(payload: OrderPreviewIn, db: Session = Depends(get_db)):
    product = db.scalar(select(Product).where(Product.id == payload.product_id, Product.is_active.is_(True)))
    region = db.scalar(select(Region).where(Region.id == payload.region_id, Region.is_active.is_(True)))
    if not product or not product.is_in_stock:
        raise HTTPException(status_code=404, detail="产品不存在、已停用或暂时无货")
    if not region or product.region_id != region.id:
        raise HTTPException(status_code=422, detail="产品与地区不匹配")
    total, discount = calculate_order_price(product, payload.quantity, payload.billing_days)
    return {"product_id": product.id, "product_name": product.name, "region_id": region.id, "region": region.name, "quantity": payload.quantity, "billing_days": payload.billing_days, "unit_price": product.price, "discount": discount, "total_amount": total}


# 客户接口：客户身份和数据范围全部从 Token 解析。
@router.get("/customer/me", response_model=CustomerOut)
def customer_me(db: Session = Depends(get_db), user: User = Depends(require_roles("customer"))):
    return get_customer_profile(db, user)


@router.get("/customer/orders", response_model=list[OrderOut])
def customer_orders(db: Session = Depends(get_db), user: User = Depends(require_roles("customer"))):
    customer = get_customer_profile(db, user)
    return list(db.scalars(select(Order).where(Order.customer_id == customer.id).order_by(Order.created_at.desc())).all())


@router.get("/customer/orders/{order_id}", response_model=OrderOut)
def customer_order(order_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles("customer"))):
    customer = get_customer_profile(db, user)
    order = db.scalar(select(Order).where(Order.id == order_id, Order.customer_id == customer.id))
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在或无权访问")
    return order


@router.post("/customer/orders", response_model=OrderOut)
def customer_create_order(payload: OrderIn, db: Session = Depends(get_db), user: User = Depends(require_roles("customer"))):
    customer = get_customer_profile(db, user)
    try:
        order = build_order(db, user, customer, payload)
        db.commit()
        db.refresh(order)
        return order
    except Exception:
        db.rollback()
        raise


@router.post("/customer/orders/{order_id}/cancel", response_model=OrderOut)
def customer_cancel_order(order_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles("customer"))):
    customer = get_customer_profile(db, user)
    order = db.scalar(select(Order).where(Order.id == order_id, Order.customer_id == customer.id))
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在或无权访问")
    if order.status not in {"pending_payment", "pending_confirm", "renewal"}:
        raise HTTPException(status_code=409, detail="当前订单状态不允许取消")
    try:
        order.status = "cancelled"
        order.cancelled_at = utcnow()
        write_audit(db, user, "cancel", "order", order.id, "客户取消订单")
        notify_order_parties(db, customer, order, "订单已取消", f"订单 {order.order_no} 已取消。", "order")
        db.commit()
        db.refresh(order)
        return order
    except Exception:
        db.rollback()
        raise


@router.get("/customer/lines", response_model=list[CustomerLineOut])
def customer_lines(db: Session = Depends(get_db), user: User = Depends(require_roles("customer"))):
    customer = get_customer_profile(db, user)
    lines = db.scalars(select(CustomerLine).where(CustomerLine.customer_id == customer.id).order_by(CustomerLine.started_at.desc())).all()
    return [customer_line_payload(db, line) for line in lines]


@router.post("/customer/lines", response_model=OrderOut)
def customer_request_line(payload: OrderIn, db: Session = Depends(get_db), user: User = Depends(require_roles("customer"))):
    """兼容线路购买入口，仅创建订单，实际线路仍由管理员交付。"""
    customer = get_customer_profile(db, user)
    try:
        order = build_order(db, user, customer, payload)
        db.commit()
        db.refresh(order)
        return order
    except Exception:
        db.rollback()
        raise


@router.get("/customer/lines/{line_id}", response_model=CustomerLineOut)
def customer_line(line_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles("customer"))):
    customer = get_customer_profile(db, user)
    line = db.scalar(select(CustomerLine).where(CustomerLine.id == line_id, CustomerLine.customer_id == customer.id))
    if not line:
        raise HTTPException(status_code=404, detail="线路不存在或无权访问")
    return customer_line_payload(db, line)


@router.patch("/customer/lines/{line_id}/note", response_model=CustomerLineOut)
def customer_line_note(line_id: int, payload: CustomerLineNoteIn, db: Session = Depends(get_db), user: User = Depends(require_roles("customer"))):
    customer = get_customer_profile(db, user)
    line = db.scalar(select(CustomerLine).where(CustomerLine.id == line_id, CustomerLine.customer_id == customer.id))
    if not line:
        raise HTTPException(status_code=404, detail="线路不存在或无权访问")
    try:
        line.customer_note = payload.customer_note
        write_audit(db, user, "update_note", "customer_line", line.id)
        db.commit()
        db.refresh(line)
        return customer_line_payload(db, line)
    except Exception:
        db.rollback()
        raise


@router.get("/customer/renewals", response_model=list[OrderOut])
def customer_renewals(db: Session = Depends(get_db), user: User = Depends(require_roles("customer"))):
    customer = get_customer_profile(db, user)
    now = utcnow()
    expiring = db.scalars(select(CustomerLine).where(CustomerLine.customer_id == customer.id, CustomerLine.status == "active", CustomerLine.expires_at >= now, CustomerLine.expires_at <= now + timedelta(days=7))).all()
    for line in expiring:
        marker = f"线路 {line.id} 即将到期"
        if not db.scalar(select(Notification.id).where(Notification.user_id == user.id, Notification.category == "expiration", Notification.content.contains(marker))):
            add_notification(db, user.id, "线路即将到期", f"{marker}，请及时查看续费选项。", "expiration")
    if expiring:
        db.commit()
    return list(db.scalars(select(Order).where(Order.customer_id == customer.id, Order.order_type == "renewal").order_by(Order.created_at.desc())).all())


@router.post("/customer/lines/{line_id}/renew", response_model=OrderOut)
def customer_renew_line(line_id: int, payload: RenewalIn, db: Session = Depends(get_db), user: User = Depends(require_roles("customer"))):
    customer = get_customer_profile(db, user)
    line = db.scalar(select(CustomerLine).where(CustomerLine.id == line_id, CustomerLine.customer_id == customer.id))
    if not line:
        raise HTTPException(status_code=404, detail="线路不存在或无权访问")
    if line.status == "deleted":
        raise HTTPException(status_code=409, detail="已删除线路不能续费")
    duplicate = db.scalar(select(Order).where(Order.renewal_line_id == line.id, Order.order_type == "renewal", Order.status.not_in(TERMINAL_ORDER_STATUSES)))
    if duplicate:
        raise HTTPException(status_code=409, detail="该线路已有进行中的续费订单")
    order_payload = OrderIn(product_id=line.product_id, region_id=db.get(Product, line.product_id).region_id, quantity=1, billing_days=payload.billing_days, payment_method=payload.payment_method, remark=f"线路 {line.id} 续费")
    try:
        order = build_order(db, user, customer, order_payload, "renewal", line.id)
        db.commit()
        db.refresh(order)
        return order
    except Exception:
        db.rollback()
        raise


# 销售接口：始终限定到当前销售负责的客户。
@router.get("/sales/dashboard")
def sales_dashboard(db: Session = Depends(get_db), user: User = Depends(require_roles("sales"))):
    customer_ids = scoped_customer_ids(db, user)
    now = utcnow()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return {
        "customers": db.scalar(select(func.count()).select_from(Customer).where(Customer.id.in_(customer_ids))) or 0,
        "active_lines": db.scalar(select(func.count()).select_from(CustomerLine).where(CustomerLine.customer_id.in_(customer_ids), CustomerLine.status == "active")) or 0,
        "expiring_in_7_days": db.scalar(select(func.count()).select_from(CustomerLine).where(CustomerLine.customer_id.in_(customer_ids), CustomerLine.expires_at >= now, CustomerLine.expires_at <= now + timedelta(days=7))) or 0,
        "pending_confirm_orders": db.scalar(select(func.count()).select_from(Order).where(Order.customer_id.in_(customer_ids), Order.status == "pending_confirm")) or 0,
        "pending_delivery_orders": db.scalar(select(func.count()).select_from(Order).where(Order.customer_id.in_(customer_ids), Order.status.in_({"pending_delivery", "partially_delivered"}))) or 0,
        "unread_notifications": db.scalar(select(func.count()).select_from(Notification).where(Notification.user_id == user.id, Notification.is_read.is_(False))) or 0,
        "monthly_sales": db.scalar(select(func.coalesce(func.sum(Order.total_amount), 0)).where(Order.customer_id.in_(customer_ids), Order.created_at >= month_start, Order.order_type == "purchase", Order.status.not_in({"cancelled", "refunding", "refunded"}))) or 0,
        "monthly_renewals": db.scalar(select(func.coalesce(func.sum(Order.total_amount), 0)).where(Order.customer_id.in_(customer_ids), Order.created_at >= month_start, Order.order_type == "renewal", Order.status.not_in({"cancelled", "refunding", "refunded"}))) or 0,
    }


@router.get("/sales/customers", response_model=list[CustomerOut])
def sales_customers(db: Session = Depends(get_db), user: User = Depends(require_roles("sales"))):
    return list(db.scalars(select(Customer).where(Customer.sales_id == user.id, Customer.is_deleted.is_(False)).order_by(Customer.created_at.desc())).all())


@router.get("/sales/orders", response_model=list[OrderOut])
def sales_orders(db: Session = Depends(get_db), user: User = Depends(require_roles("sales"))):
    return list(db.scalars(select(Order).where(Order.customer_id.in_(scoped_customer_ids(db, user))).order_by(Order.created_at.desc())).all())


@router.get("/sales/expiring-lines", response_model=list[CustomerLineOut])
def sales_expiring_lines(days: int = Query(default=30, ge=1, le=365), db: Session = Depends(get_db), user: User = Depends(require_roles("sales"))):
    now = utcnow()
    lines = db.scalars(select(CustomerLine).where(CustomerLine.customer_id.in_(scoped_customer_ids(db, user)), CustomerLine.status == "active", CustomerLine.expires_at >= now, CustomerLine.expires_at <= now + timedelta(days=days)).order_by(CustomerLine.expires_at)).all()
    return [customer_line_payload(db, line) for line in lines]


@router.get("/sales/health-impacts")
def sales_health_impacts(db: Session = Depends(get_db), user: User = Depends(require_roles("sales"))):
    rows = db.execute(select(HealthIncident, Node).join(Node, HealthIncident.node_id == Node.id).where(HealthIncident.node_id.in_(visible_node_ids(db, user)), HealthIncident.status == "open").order_by(HealthIncident.started_at.desc())).all()
    return [{"incident_id": incident.id, "line_name": node.name, "status": node.health_status, "started_at": incident.started_at, "reason": incident.reason} for incident, node in rows]


# 管理员订单处理接口：状态变化、审计与通知在同一事务提交。
@router.get("/admin/orders", response_model=list[OrderOut])
def admin_orders(db: Session = Depends(get_db), user: User = Depends(require_roles("super_admin", "admin"))):
    return list(db.scalars(select(Order).order_by(Order.created_at.desc())).all())


@router.get("/admin/order-center/options")
def admin_order_center_options(
    resource_category: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("super_admin", "admin")),
):
    from app.core.config import get_settings

    settings = get_settings()
    customers = db.scalars(select(Customer).where(
        Customer.is_deleted.is_(False), Customer.user_id.is_not(None),
    ).order_by(Customer.name).limit(1000)).all()
    products = db.scalars(select(Product).where(Product.is_active.is_(True)).order_by(Product.name)).all()
    supplier_product_query = select(SupplierProduct).where(SupplierProduct.is_available.is_(True))
    if resource_category:
        supplier_product_query = supplier_product_query.where(SupplierProduct.resource_category == resource_category)
    supplier_products = db.scalars(supplier_product_query.order_by(SupplierProduct.name_snapshot).limit(500)).all()
    supplier_regions = db.scalars(select(SupplierRegion).where(
        SupplierRegion.is_available.is_(True), SupplierRegion.city_name.is_(None),
    ).order_by(SupplierRegion.country_name).limit(1000)).all()
    supplier_product_ids = {item.id for item in supplier_products}
    supplier_regions = [item for item in supplier_regions if item.supplier_product_id in supplier_product_ids]
    retail_price_rows = db.scalars(select(RetailPriceRule).order_by(
        RetailPriceRule.resource_category, RetailPriceRule.country_code, RetailPriceRule.billing_days,
    )).all()
    retail_prices: dict[tuple[str, str], dict] = {}
    for item in retail_price_rows:
        key = (item.resource_category, item.country_code)
        entry = retail_prices.setdefault(key, {
            "resource_category": item.resource_category,
            "country_code": item.country_code,
            "currency": item.currency,
            "prices": {},
        })
        entry["prices"][str(item.billing_days)] = float(item.unit_price)
    segments = db.scalars(select(SupplierIpSegmentSnapshot).where(
        SupplierIpSegmentSnapshot.checked_at >= datetime.utcnow() - timedelta(minutes=settings.supplier_inventory_ttl_minutes),
        SupplierIpSegmentSnapshot.is_request_sufficient.is_(True),
        SupplierIpSegmentSnapshot.is_segment_sufficient.is_(True),
    ).order_by(SupplierIpSegmentSnapshot.checked_at.desc()).limit(500)).all()
    segments = [item for item in segments if item.supplier_product_id in supplier_product_ids]
    quotes = db.scalars(select(SupplierQuote).where(
        SupplierQuote.is_available.is_(True),
        SupplierQuote.quoted_at >= datetime.utcnow() - timedelta(minutes=settings.supplier_quote_ttl_minutes),
    ).order_by(SupplierQuote.quoted_at.desc()).limit(1000)).all()
    quotes = [item for item in quotes if item.supplier_product_id in supplier_product_ids]
    return {
        "customers": [{"id": item.id, "name": item.name, "email": item.email} for item in customers],
        "products": [{"id": item.id, "name": item.name, "product_type": item.product_type,
                      "price": item.price, "billing_days": item.billing_days, "region_id": item.region_id}
                     for item in products],
        "categories": [{"code": code, "label": label} for code, label in RESOURCE_CATEGORY_CODES.items()],
        "supplier_products": [{"id": item.id, "external_product_ref": item.external_product_ref,
                               "name": item.name_snapshot, "resource_category": item.resource_category,
                               "local_product_id": item.local_product_id, "currency": item.currency,
                               "last_synced_at": item.last_synced_at}
                              for item in supplier_products],
        "supplier_regions": [{"id": item.id, "supplier_product_id": item.supplier_product_id,
                              "country_code": item.country_code, "country_name": item.country_name,
                              "last_synced_at": item.last_synced_at}
                             for item in supplier_regions],
        "retail_prices": list(retail_prices.values()),
        "segments": [{"id": item.id, "supplier_product_id": item.supplier_product_id,
                      "country_code": item.country_code, "cidr": item.cidr,
                      "requested_quantity": item.requested_quantity, "checked_at": item.checked_at}
                     for item in segments],
        "quotes": [{"id": item.id, "supplier_product_id": item.supplier_product_id,
                    "external_product_ref": item.external_product_ref, "country_code": item.country_code,
                    "duration_days": item.duration_days, "unit_cost": float(item.unit_cost),
                    "currency": item.currency, "quoted_at": item.quoted_at}
                   for item in quotes],
    }


@router.get("/admin/order-center/subscriptions")
def admin_subscriptions(
    customer_id: int | None = None,
    product_id: int | None = None,
    expires_from: datetime | None = None,
    expires_to: datetime | None = None,
    source_type: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("super_admin", "admin")),
):
    query = select(CustomerLine, Customer.name, Product.name, Order.source_type).join(
        Customer, Customer.id == CustomerLine.customer_id,
    ).join(Product, Product.id == CustomerLine.product_id).outerjoin(Order, Order.id == CustomerLine.order_id)
    if customer_id is not None:
        query = query.where(CustomerLine.customer_id == customer_id)
    if product_id is not None:
        query = query.where(CustomerLine.product_id == product_id)
    if expires_from is not None:
        query = query.where(CustomerLine.expires_at >= expires_from)
    if expires_to is not None:
        query = query.where(CustomerLine.expires_at <= expires_to)
    if source_type:
        if source_type == "owned":
            query = query.where((Order.source_type == "owned") | Order.id.is_(None))
        else:
            query = query.where(Order.source_type == source_type)
    rows = db.execute(query.order_by(CustomerLine.expires_at.asc()).limit(2000)).all()
    return [{
        "id": line.id, "customer_id": line.customer_id, "customer_name": customer_name,
        "product_id": line.product_id, "product_name": product_name, "order_id": line.order_id,
        "source_type": order_source or "owned", "static_ip": line.static_ip, "status": line.status,
        "started_at": line.started_at, "expires_at": line.expires_at, "created_at": line.created_at,
    } for line, customer_name, product_name, order_source in rows]


@router.get("/admin/order-center/orders")
def admin_order_center_orders(db: Session = Depends(get_db), user: User = Depends(require_roles("super_admin", "admin"))):
    rows = db.execute(select(Order, Customer.name).join(Customer, Customer.id == Order.customer_id)
                      .order_by(Order.created_at.desc()).limit(1000)).all()
    return [{**{column.name: getattr(order, column.name) for column in Order.__table__.columns},
             "customer_name": customer_name} for order, customer_name in rows]


@router.post("/admin/order-center/orders", status_code=201)
def admin_create_resource_order(
    payload: AdminResourceOrderIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("super_admin", "admin")),
):
    from decimal import Decimal, ROUND_HALF_UP
    from app.core.config import get_settings
    from app.services.order_service import notify_order_parties

    settings = get_settings()
    existing_order = db.scalar(select(Order).where(Order.idempotency_key == payload.idempotency_key))
    if existing_order:
        existing_customer = db.get(Customer, existing_order.customer_id)
        return {**{column.name: getattr(existing_order, column.name) for column in Order.__table__.columns},
                "customer_name": existing_customer.name if existing_customer else "未知客户"}
    customer = db.scalar(select(Customer).where(
        Customer.id == payload.customer_id, Customer.is_deleted.is_(False), Customer.user_id.is_not(None),
    ))
    product = db.scalar(select(Product).where(Product.id == payload.product_id, Product.is_active.is_(True)))
    supplier_product = db.get(SupplierProduct, payload.supplier_product_id)
    supplier_region = db.get(SupplierRegion, payload.supplier_region_id)
    segment = db.get(SupplierIpSegmentSnapshot, payload.supplier_segment_snapshot_id)
    quote = db.get(SupplierQuote, payload.supplier_quote_id)
    if not customer:
        raise HTTPException(status_code=422, detail="请选择已注册且可用的客户")
    if not product or not supplier_product or not supplier_region or not segment or not quote:
        raise HTTPException(status_code=422, detail="商品、国家、网段或报价快照不存在")
    if payload.resource_category not in RESOURCE_CATEGORY_CODES or supplier_product.resource_category != payload.resource_category:
        raise HTTPException(status_code=422, detail="供应商商品尚未人工映射到所选资源分类")
    if supplier_product.local_product_id != product.id or not supplier_product.is_available:
        raise HTTPException(status_code=409, detail="供应商商品与平台产品未匹配或已停用")
    if supplier_region.supplier_product_id != supplier_product.id or not supplier_region.is_available or supplier_region.city_name is not None:
        raise HTTPException(status_code=409, detail="所选国家不属于当前供应商商品或已不可用")
    freshness_floor = datetime.utcnow() - timedelta(minutes=settings.supplier_inventory_ttl_minutes)
    quote_floor = datetime.utcnow() - timedelta(minutes=settings.supplier_quote_ttl_minutes)
    if (segment.supplier_product_id != supplier_product.id or segment.country_code != supplier_region.country_code
            or segment.checked_at < freshness_floor or not segment.is_request_sufficient
            or not segment.is_segment_sufficient or segment.requested_quantity < payload.quantity):
        raise HTTPException(status_code=409, detail="网段快照已过期或数量不满足，请重新检测库存")
    billing_days = payload.billing_days or product.billing_days
    if (quote.supplier_product_id != supplier_product.id or quote.country_code not in (None, supplier_region.country_code)
            or quote.quoted_at < quote_floor or not quote.is_available
            or quote.duration_days not in (None, billing_days)):
        raise HTTPException(status_code=409, detail="报价快照已过期或与当前国家、周期不匹配，请重新同步报价")
    retail_rule = db.scalar(select(RetailPriceRule).where(
        RetailPriceRule.resource_category == payload.resource_category,
        RetailPriceRule.country_code == supplier_region.country_code,
        RetailPriceRule.billing_days == billing_days,
    ))
    if not retail_rule:
        raise HTTPException(status_code=409, detail="该产品分类、国家和周期暂无销售价，请勿手动估价")
    unit_price = Decimal(retail_rule.unit_price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    total = (unit_price * payload.quantity).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    cost_total = (Decimal(str(quote.unit_cost)) * payload.quantity).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    order = Order(
        order_no=f"ORD-{datetime.now().strftime('%Y%m%d')}-{uuid4().hex[:8].upper()}",
        customer_id=customer.id, product_id=product.id, source_type="resource_pool",
        idempotency_key=payload.idempotency_key,
        resource_category=payload.resource_category, supplier_product_id=supplier_product.id,
        supplier_region_id=supplier_region.id, supplier_segment_snapshot_id=segment.id,
        supplier_quote_id=quote.id, supplier_cost=cost_total, amount=float(total),
        quantity=payload.quantity, billing_days=billing_days, unit_price=float(unit_price),
        total_amount=float(total), region_id=product.region_id, region_name=supplier_region.country_name,
        product_name=product.name, payment_method="contact_manager", payment_status="unpaid",
        status="pending_payment", assigned_sales_id=customer.sales_id,
        order_type="purchase", remark=payload.remark,
    )
    try:
        db.add(order)
        db.flush()
        notify_order_parties(db, customer, order, "资源池订单已创建", f"订单 {order.order_no} 已创建，当前待付款和人工采购审核；系统不会自动下单或开通线路。", "order")
        write_audit(db, user, "create", "order", order.id,
                    f"来源=resource_pool; 分类={payload.resource_category}; 国家={supplier_region.country_code}; 周期={billing_days}; 数量={payload.quantity}; 售价来源={retail_rule.source_version}; 售价={total}; 成本快照={cost_total}")
        db.commit()
        db.refresh(order)
        return {**{column.name: getattr(order, column.name) for column in Order.__table__.columns},
                "customer_name": customer.name}
    except IntegrityError:
        db.rollback()
        existing_order = db.scalar(select(Order).where(Order.idempotency_key == payload.idempotency_key))
        if existing_order:
            existing_customer = db.get(Customer, existing_order.customer_id)
            return {**{column.name: getattr(existing_order, column.name) for column in Order.__table__.columns},
                    "customer_name": existing_customer.name if existing_customer else "未知客户"}
        raise
    except Exception:
        db.rollback()
        raise


@router.post("/admin/orders/{order_id}/confirm", response_model=OrderOut)
def admin_confirm_order(order_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles("super_admin", "admin"))):
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在")
    if order.source_type == "resource_pool":
        raise HTTPException(status_code=409, detail="资源池自动采购尚未启用，不能确认采购或开通线路")
    if order.order_type != "purchase":
        raise HTTPException(status_code=409, detail="仅普通购买订单可以确认交付")
    if order.status not in {"pending_confirm", "pending_payment", "paid", "processing"}:
        raise HTTPException(status_code=409, detail="订单状态不允许确认")
    customer = db.scalar(select(Customer).where(Customer.id == order.customer_id, Customer.is_deleted.is_(False)))
    product = db.scalar(select(Product).where(Product.id == order.product_id, Product.is_active.is_(True)))
    if not customer or not customer.user_id:
        raise HTTPException(status_code=400, detail="客户不存在、已删除或未绑定账号")
    if not product or (order.region_id is not None and product.region_id != order.region_id):
        raise HTTPException(status_code=400, detail="产品不存在、已停用或订单地区快照异常")
    try:
        if order.region_id is None:
            order.region_id = product.region_id
            order.region_name = db.get(Region, product.region_id).name
        order.status = "pending_delivery"
        order.confirmed_at = utcnow()
        write_audit(db, user, "confirm", "order", order.id)
        notify_order_parties(db, customer, order, "订单已确认", f"订单 {order.order_no} 已确认，等待线路交付。", "order")
        db.commit()
        db.refresh(order)
        return order
    except Exception:
        db.rollback()
        raise


@router.post("/admin/orders/{order_id}/deliver", response_model=CustomerLineOut)
def admin_deliver_order(order_id: int, payload: DeliverOrderIn, db: Session = Depends(get_db), user: User = Depends(require_roles("super_admin", "admin"))):
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在")
    if order.source_type == "resource_pool":
        raise HTTPException(status_code=409, detail="资源池订单不能通过手动线路交付接口开通")
    if order.order_type == "renewal":
        raise HTTPException(status_code=409, detail="续费订单不能创建新线路")
    if order.status not in {"pending_delivery", "partially_delivered"}:
        raise HTTPException(status_code=409, detail="订单状态不允许交付")
    customer = db.scalar(select(Customer).where(Customer.id == order.customer_id, Customer.is_deleted.is_(False)))
    product = db.scalar(select(Product).where(Product.id == order.product_id, Product.is_active.is_(True)))
    node = db.scalar(select(Node).where(Node.id == payload.node_id, Node.is_enabled.is_(True)))
    now = utcnow()
    if not customer or not customer.user_id or not product or not node:
        raise HTTPException(status_code=400, detail="客户、产品或节点不可用")
    order_region_id = order.region_id or product.region_id
    if node.region_id != order_region_id or product.region_id != order_region_id:
        raise HTTPException(status_code=400, detail="节点、产品与订单地区不一致")
    if payload.expires_at.replace(tzinfo=None) <= now:
        raise HTTPException(status_code=422, detail="到期时间必须晚于当前时间")
    try:
        if not claim_delivery_slot(db, order.id, now):
            db.rollback()
            raise HTTPException(status_code=409, detail="订单交付数量已满或状态不允许交付")
        db.expire(order)
        db.refresh(order)
        line = CustomerLine(customer_id=customer.id, node_id=node.id, product_id=product.id, order_id=order.id, static_ip=payload.static_ip, started_at=now, expires_at=payload.expires_at.replace(tzinfo=None), customer_note=payload.customer_note, node_link=payload.node_link, subscription_url=payload.subscription_url, node_qr_data=payload.node_qr_data, subscription_qr_data=payload.subscription_qr_data)
        db.add(line)
        db.flush()
        if order.region_id is None:
            order.region_id = product.region_id
            order.region_name = db.get(Region, product.region_id).name
        if order.delivered_line_id is None:
            order.delivered_line_id = line.id
        progress = f"{order.delivered_quantity}/{order.quantity}"
        write_audit(db, user, "deliver", "order", order.id, f"线路={line.id}; 进度={progress}")
        if order.status == "delivered":
            notify_order_parties(db, customer, order, "订单已全部交付", f"订单已完成全部 {order.quantity} 条线路交付，请进入我的线路查看。", "delivery")
        else:
            notify_order_parties(db, customer, order, "订单部分交付", f"订单线路交付进度为 {progress}，请进入我的线路查看。", "delivery")
        db.commit()
        db.refresh(line)
        return customer_line_payload(db, line)
    except Exception:
        db.rollback()
        raise


@router.post("/admin/orders/{order_id}/complete-renewal", response_model=OrderOut)
def admin_complete_renewal(
    order_id: int,
    payload: CompleteRenewalIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("super_admin", "admin")),
):
    if not payload.manually_confirmed:
        raise HTTPException(status_code=409, detail="必须明确确认续费条件后才能完成")
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在")
    if order.order_type != "renewal" or not order.renewal_line_id:
        raise HTTPException(status_code=409, detail="订单不是有效的续费订单")
    line = db.scalar(select(CustomerLine).where(
        CustomerLine.id == order.renewal_line_id,
        CustomerLine.customer_id == order.customer_id,
    ))
    customer = db.scalar(select(Customer).where(
        Customer.id == order.customer_id,
        Customer.is_deleted.is_(False),
    ))
    if not line or not customer or not customer.user_id:
        raise HTTPException(status_code=409, detail="续费线路与订单客户不匹配或客户不可用")
    if order.status == "completed" and order.renewal_completed_at:
        return order
    if order.status != "renewal":
        raise HTTPException(status_code=409, detail="当前续费订单状态不允许完成")
    now = utcnow()
    try:
        claimed = db.execute(
            update(Order)
            .where(Order.id == order.id, Order.status == "renewal", Order.renewal_completed_at.is_(None))
            .values(status="processing")
            .execution_options(synchronize_session=False)
        )
        if claimed.rowcount != 1:
            db.rollback()
            db.refresh(order)
            if order.status == "completed" and order.renewal_completed_at:
                return order
            raise HTTPException(status_code=409, detail="续费订单正在处理或状态已变化")
        db.expire(order)
        db.refresh(order)
        renewal_base = max(line.expires_at, now)
        line.expires_at = renewal_base + timedelta(days=order.billing_days)
        order.status = "completed"
        order.payment_status = "manually_confirmed"
        order.renewal_completed_at = now
        write_audit(db, user, "complete_renewal", "order", order.id, f"线路={line.id}; 延长天数={order.billing_days}")
        add_notification(db, customer.user_id, "线路续费已完成", f"续费订单 {order.order_no} 已人工确认完成，线路到期时间已更新。", "renewal")
        db.commit()
        db.refresh(order)
        return order
    except Exception:
        db.rollback()
        raise


@router.post("/admin/orders/{order_id}/cancel", response_model=OrderOut)
def admin_cancel_order(order_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles("super_admin", "admin"))):
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在")
    if order.status in {"partially_delivered", "delivered", "completed", "cancelled", "refunding", "refunded"}:
        raise HTTPException(status_code=409, detail="当前订单状态不允许取消")
    customer = db.get(Customer, order.customer_id)
    try:
        order.status = "cancelled"
        order.cancelled_at = utcnow()
        write_audit(db, user, "cancel", "order", order.id, "管理员取消订单")
        notify_order_parties(db, customer, order, "订单已取消", f"订单 {order.order_no} 已取消。", "order")
        db.commit()
        db.refresh(order)
        return order
    except Exception:
        db.rollback()
        raise


@router.post("/admin/customers/{customer_id}/assign", response_model=CustomerOut)
def admin_assign_customer(customer_id: int, payload: AssignCustomerIn, db: Session = Depends(get_db), user: User = Depends(require_roles("super_admin", "admin"))):
    customer = db.scalar(select(Customer).where(Customer.id == customer_id, Customer.is_deleted.is_(False)))
    if not customer:
        raise HTTPException(status_code=404, detail="客户不存在")
    ensure_sales_user(db, payload.sales_id)
    try:
        customer.sales_id = payload.sales_id
        write_audit(db, user, "assign_sales", "customer", customer.id, f"sales_id={payload.sales_id}")
        db.commit()
        db.refresh(customer)
        return customer
    except Exception:
        db.rollback()
        raise


@router.post("/admin/users/{user_id}/unlock-login")
def unlock_login(user_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles("super_admin", "admin"))):
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="用户不存在")
    target.failed_login_count = 0
    target.locked_until = None
    write_audit(db, user, "unlock_login", "user", target.id)
    db.commit()
    return {"ok": True}


def _manageable_user(db: Session, actor: User, user_id: int) -> User:
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="用户不存在")
    if target.role in {"admin", "super_admin"} and actor.role != "super_admin":
        raise HTTPException(status_code=403, detail="无权管理该账号")
    if target.role == "super_admin" and target.id == actor.id:
        raise HTTPException(status_code=403, detail="不能操作当前超级管理员")
    return target


@router.post("/admin/sales-users", response_model=UserOut, status_code=201)
def admin_create_sales_user(payload: CreateSalesUserIn, db: Session = Depends(get_db),
                            actor: User = Depends(require_roles("super_admin"))):
    if db.scalar(select(User.id).where(User.username == payload.username)) is not None:
        raise HTTPException(status_code=409, detail="用户名已存在")
    sales_user = User(
        username=payload.username,
        password_hash=hash_password(payload.password),
        role="sales",
        display_name=payload.display_name,
        phone=payload.phone,
        must_change_password=False,
    )
    db.add(sales_user)
    try:
        db.flush()
        write_audit(db, actor, "create_sales_user", "user", sales_user.id,
                    f"用户名={sales_user.username}")
        db.commit()
        db.refresh(sales_user)
        return sales_user
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="用户名已存在") from exc


@router.get("/admin/sales-users")
def admin_list_sales_users(
    keyword: str | None = Query(default=None, max_length=120),
    status: str | None = Query(default=None, pattern="^(active|disabled)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("super_admin")),
):
    query = select(User).where(User.role == "sales")
    if keyword and keyword.strip():
        pattern = f"%{keyword.strip()}%"
        query = query.where((User.username.ilike(pattern)) | (User.display_name.ilike(pattern)) | (User.phone.ilike(pattern)))
    if status == "active":
        query = query.where(User.is_active.is_(True))
    elif status == "disabled":
        query = query.where(User.is_active.is_(False))
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    users = db.scalars(query.order_by(User.created_at.desc(), User.id.desc())
                       .offset((page - 1) * page_size).limit(page_size)).all()
    user_ids = [item.id for item in users]
    customer_counts = dict(db.execute(
        select(Customer.sales_id, func.count(Customer.id))
        .where(Customer.sales_id.in_(user_ids), Customer.is_deleted.is_(False))
        .group_by(Customer.sales_id)
    ).all()) if user_ids else {}
    last_logins = dict(db.execute(
        select(SalesLoginEvent.user_id, func.max(SalesLoginEvent.created_at))
        .where(SalesLoginEvent.user_id.in_(user_ids), SalesLoginEvent.outcome == "success")
        .group_by(SalesLoginEvent.user_id)
    ).all()) if user_ids else {}
    return {
        "items": [{
            "id": item.id, "username": item.username, "display_name": item.display_name,
            "phone": item.phone, "is_active": item.is_active, "created_at": item.created_at,
            "customer_count": customer_counts.get(item.id, 0), "last_login_at": last_logins.get(item.id),
            "locked_until": item.locked_until,
        } for item in users],
        "total": total, "page": page, "page_size": page_size,
    }


@router.patch("/admin/sales-users/{sales_id}")
def admin_update_sales_user(sales_id: int, payload: UpdateSalesUserIn,
                            db: Session = Depends(get_db), actor: User = Depends(require_roles("super_admin"))):
    target = db.scalar(select(User).where(User.id == sales_id, User.role == "sales"))
    if not target:
        raise HTTPException(status_code=404, detail="销售账号不存在")
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=422, detail="请至少修改一项资料")
    if "display_name" in changes and changes["display_name"] is None:
        raise HTTPException(status_code=422, detail="销售姓名不能为空")
    for field, value in changes.items():
        setattr(target, field, value)
    write_audit(db, actor, "update_sales_profile", "user", target.id,
                f"修改字段={','.join(changes.keys())}")
    db.commit()
    return {"id": target.id, "username": target.username, "display_name": target.display_name,
            "phone": target.phone, "is_active": target.is_active}


@router.get("/admin/sales-users/{sales_id}/login-logs")
def admin_sales_login_logs(sales_id: int, limit: int = Query(default=50, ge=1, le=100),
                           db: Session = Depends(get_db), _: User = Depends(require_roles("super_admin"))):
    if not db.scalar(select(User.id).where(User.id == sales_id, User.role == "sales")):
        raise HTTPException(status_code=404, detail="销售账号不存在")
    events = db.scalars(select(SalesLoginEvent).where(SalesLoginEvent.user_id == sales_id)
                        .order_by(SalesLoginEvent.created_at.desc(), SalesLoginEvent.id.desc()).limit(limit)).all()
    return [{"id": item.id, "created_at": item.created_at, "outcome": item.outcome,
             "source_ip": item.source_ip, "user_agent": item.user_agent,
             "failure_reason": item.failure_reason} for item in events]


@router.get("/admin/sales-users/{sales_id}/customers")
def admin_sales_customers(sales_id: int, page: int = Query(default=1, ge=1),
                          page_size: int = Query(default=25, ge=1, le=100),
                          db: Session = Depends(get_db), _: User = Depends(require_roles("super_admin"))):
    if not db.scalar(select(User.id).where(User.id == sales_id, User.role == "sales")):
        raise HTTPException(status_code=404, detail="销售账号不存在")
    filters = (Customer.sales_id == sales_id, Customer.is_deleted.is_(False))
    total = db.scalar(select(func.count(Customer.id)).where(*filters)) or 0
    rows = db.execute(select(Customer, User.username)
                      .outerjoin(User, User.id == Customer.user_id)
                      .where(*filters)
                      .order_by(Customer.created_at.desc(), Customer.id.desc())
                      .offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": [{"id": item.id, "name": item.name, "phone": item.phone,
                        "email": item.email, "username": username, "status": item.status,
                        "level": item.level, "created_at": item.created_at} for item, username in rows],
            "total": total, "page": page, "page_size": page_size}


@router.get("/admin/users/{user_id}/sessions")
def admin_user_sessions(user_id: int, db: Session = Depends(get_db),
                        actor: User = Depends(require_roles("super_admin", "admin"))):
    target = _manageable_user(db, actor, user_id)
    sessions = db.scalars(select(AuthSession).where(AuthSession.user_id == target.id)
                          .order_by(AuthSession.id.desc()).limit(100)).all()
    return [{"id": item.id, "created_at": item.created_at, "expires_at": item.expires_at,
             "last_used_at": item.last_used_at, "revoked_at": item.revoked_at} for item in sessions]


@router.post("/admin/users/{user_id}/sessions/{session_id}/revoke", status_code=204)
def admin_revoke_session(user_id: int, session_id: int, db: Session = Depends(get_db),
                         actor: User = Depends(require_roles("super_admin", "admin"))):
    target = _manageable_user(db, actor, user_id)
    session = db.get(AuthSession, session_id)
    if not session or session.user_id != target.id:
        raise HTTPException(status_code=404, detail="会话不存在")
    revoke_session(session, "admin_revoke")
    write_audit(db, actor, "revoke_session", "auth_session", session.id, f"用户={target.id}")
    db.commit()


@router.post("/admin/users/{user_id}/sessions/revoke-all")
def admin_revoke_all_sessions(user_id: int, db: Session = Depends(get_db),
                              actor: User = Depends(require_roles("super_admin", "admin"))):
    target = _manageable_user(db, actor, user_id)
    count = revoke_user_sessions(db, target, "admin_revoke_all")
    write_audit(db, actor, "revoke_all_sessions", "user", target.id, f"会话数={count}")
    db.commit()
    return {"revoked_count": count}


@router.post("/admin/users/{user_id}/reset-password", status_code=204)
def admin_reset_password(user_id: int, payload: ResetPasswordIn, db: Session = Depends(get_db),
                         actor: User = Depends(require_roles("super_admin", "admin"))):
    if payload.confirm is not True:
        raise HTTPException(status_code=422, detail="必须二次确认密码重置")
    if not _strong_password(payload.new_password):
        raise HTTPException(status_code=422, detail="新密码必须包含大小写字母、数字和特殊字符")
    target = _manageable_user(db, actor, user_id)
    target.password_hash = hash_password(payload.new_password)
    target.must_change_password = False
    revoke_user_sessions(db, target, "password_reset")
    write_audit(db, actor, "reset_password", "user", target.id, "已撤销全部会话")
    db.commit()


@router.patch("/admin/users/{user_id}/access", response_model=UserOut)
def admin_change_access(user_id: int, payload: UserAccessIn, db: Session = Depends(get_db),
                        actor: User = Depends(require_roles("super_admin", "admin"))):
    target = _manageable_user(db, actor, user_id)
    if payload.role is not None:
        if actor.role != "super_admin" or payload.role not in {"customer", "sales", "admin", "super_admin"}:
            raise HTTPException(status_code=403, detail="无权变更角色")
        target.role = payload.role
    if payload.is_active is not None:
        target.is_active = payload.is_active
    if payload.role is not None or payload.is_active is not None:
        revoke_user_sessions(db, target, "access_changed")
        write_audit(db, actor, "change_access", "user", target.id,
                    f"角色={target.role}; 启用={target.is_active}")
        db.commit()
    return target
