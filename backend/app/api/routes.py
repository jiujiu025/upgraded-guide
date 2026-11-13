from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
import unicodedata
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import current_user, require_roles
from app.core.security import create_access_token, hash_password, verify_password
from app.core.client_ip import get_client_ip
from app.core.timeutil import local_date_of, local_day_range_utc
from app.db import get_db
from app.models import (
    AuthSession, CatalogCityOption, CatalogListing, Customer, CustomerLine, HealthIncident, Node, Notification, Order, PaymentOrder, PaymentRefund, Procurement, Product, ProductSupplyRule, Region, ResourcePool, RetailPriceRule,
    SalesInviteCode, SalesLoginEvent, SupplierIpSegmentSnapshot, SupplierProduct, SupplierQuote, SupplierRegion, User,
)
from app.schemas.common import (
    AssignCustomerIn, CaptchaChallengeOut, CompleteRenewalIn, CreateSalesUserIn, CustomerIn, CustomerLineNoteIn, CustomerLineOut, CustomerOut,
    DeliverOrderIn, InviteCodeCreateIn, InviteCodeOut, InviteCodePublicOut, LineIn, LineOut, LoginIn, NotificationOut, OnBehalfOrderIn, OrderIn, RegisterIn,
    OrderOut, OrderPageOut, OrderPreviewIn, OrderPreviewOut, PasswordRecoveryIn, ProductOut, PublicProductOut,
    RegionOut, RenewalIn, TokenOut, UpdateSalesUserIn, UserOut,
    SecurityQuestionsUpdateIn,
)
from app.api.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, counts_by, like_pattern, page_payload, paginate
from app.services.audit_service import write_audit
from app.services.city_option_service import CitySelectionError, customer_options, normalize_segments, resolve_choice
from app.services.proxy_link import ProxyLinkError, parse_link
from app.services.subscription_service import issue_token
from app.services.customer_scope import get_customer_profile, scoped_customer_ids
from app.services.customer_email import ensure_unique_customer_email
from app.services.customer_view import balance_cents_of, customer_view, customer_views
from app.services.health_service import check_node
from app.services.procurement_service import run_auto_procure_in_background
from app.services.order_service import (
    TERMINAL_ORDER_STATUSES, add_notification, calculate_order_price, claim_delivery_slot,
    ensure_order_cancellable, ensure_payment_settled, ensure_sales_user, initial_order_status,
    notify_order_parties, settle_renewal, settle_upgrade,
)
from app.services.pricing_service import (
    SELLABLE_PERIODS, PricingError, monthly_price_for, periods_of, unit_price_for,
)
from app.services.invite_code_service import (
    create_invite_code, disable_codes_of_sales, disable_invite_code, lookup_active_code, normalize_code,
)
from app.data.site_catalog import CATEGORY_TO_LINE, LINE_LABELS, LINE_ORDER, LINE_SUBTITLES, LIVE_SPEC_LABELS
from app.services.supplier_service import RESOURCE_CATEGORY_CODES
from app.services.registration_service import (enforce_captcha_rate_limit, enforce_invite_check_rate_limit, enforce_login_rate_limit,
    enforce_password_recovery_rate_limit, enforce_public_rate_limit, enforce_registration_rate_limit)
from app.services.captcha_service import CaptchaError, issue_challenge, verify_challenge
from app.services.session_service import (SessionError, create_session, csrf_token, find_refresh,
    revoke_session, revoke_user_sessions, rotate_session, validate_session)
from app.tasks.notify import notify_admins
from app.core.config import get_settings
import hmac
import hashlib

router = APIRouter()
LOGIN_ERROR_DETAIL = "用户名或密码错误"
DUMMY_PASSWORD_HASH = hash_password("rate-limit-timing-placeholder")
REFRESH_COOKIE = "__Host-ops_refresh"


def _origin_allowed(request: Request) -> bool:
    """来源白名单校验：与 CORS 用同一份列表，支持多个前端来源（官网 + 控制台）。

    以前只比对 FRONTEND_ORIGIN 单个值，本地从 127.0.0.1:5174 访问时浏览器带着
    这个 Origin，登录会被判"请求来源无效"——白名单明明配了，登录却过不去。
    """
    origin = request.headers.get("origin")
    if not origin:
        return True
    return origin.rstrip("/") in get_settings().cors_origins


def cap_rows(response: Response, rows: list, limit: int) -> list:
    """硬上限列表的统一收尾：多查一条判断有没有被截断，并回标记头。

    这几个列表按业务量做了硬上限（客户 200、订单 1000、订阅 2000）。之前超限的
    数据直接静默消失，界面上看不出少了东西。现在多取一条：有第 N+1 条就说明被
    截断，回 ``X-Result-Truncated: true`` 与 ``X-Result-Limit``，前端据此提示
    "仅显示前 N 条"，而不是让人误以为数据就这么多。
    """
    if len(rows) > limit:
        response.headers["X-Result-Truncated"] = "true"
        response.headers["X-Result-Limit"] = str(limit)
        return rows[:limit]
    return rows


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


class LineUpgradeIn(BaseModel):
    """把一条既有线路升级到更高的产品线（静态住宅 → 社媒/直播）。

    只补差价，到期时间不变；差额按原订单周期倍数折算。
    """
    model_config = ConfigDict(extra="forbid")

    target_category: str = Field(min_length=1, max_length=50)
    payment_method: str = "contact_manager"
    idempotency_key: str = Field(min_length=36, max_length=64)


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


def _password_byte_length_error(value: str) -> str | None:
    """bcrypt 静默截断 72 字节之后的输入：100 位密码用前 72 位就能登录。

    与字符数校验分开报错，用户才知道是"太长"而不是"不够复杂"。
    中文等 UTF-8 多字节密码尤其容易踩：24 个汉字就超 72 字节。
    """
    byte_length = len(value.encode("utf-8"))
    if byte_length > 72:
        return f"密码 UTF-8 长度不能超过 72 字节（当前 {byte_length} 字节）"
    return None


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


def customer_line_payload(db: Session, line: CustomerLine, cache: dict | None = None) -> dict:
    """单条线路的展示载荷。

    ``cache`` 由列表调用方传入，用来在同一个请求里复用 Product / Node / Region
    与周期价（``periods_of`` 每次都要查价目表）。不传就是单条查询，行为不变。
    之前每条线路 3 次 db.get + 一次价目表查询，300 条线路接近 1200 次 SQL。
    """
    if cache is None:
        cache = {}
    token = issue_token(line.customer_id)
    products = cache.setdefault("products", {})
    nodes = cache.setdefault("nodes", {})
    regions = cache.setdefault("regions", {})
    periods_cache = cache.setdefault("periods", {})
    if line.product_id not in products:
        products[line.product_id] = db.get(Product, line.product_id)
    product = products[line.product_id]
    if line.node_id not in nodes:
        nodes[line.node_id] = db.get(Node, line.node_id) if line.node_id else None
    node = nodes[line.node_id]
    region = None
    if product:
        if product.region_id not in regions:
            regions[product.region_id] = db.get(Region, product.region_id)
        region = regions[product.region_id]
        if line.product_id not in periods_cache:
            periods_cache[line.product_id] = periods_of(db, product)
    return {
        "id": line.id,
        "region": region.name if region else "未知地区",
        "ip": line.static_ip,
        "product_id": line.product_id,
        "product_type": product.product_type if product else "未知产品",
        "billing_days": product.billing_days if product else 0,
        # 续费表单按这里给出的档位渲染，避免前端写死 30/180/360 导致 90 天线路无法续费。
        "periods": periods_cache.get(line.product_id, []) if product else [],
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
        # 二维码由后端按需渲染 SVG：客户扫的是链接本身，不依赖任何第三方图片服务。
        # token 是客户级签名，同一个人所有线路共用一个订阅地址。
        "node_qr_url": f"/api/sub/{token}/node/{line.id}.svg" if line.node_link else None,
        "subscription_qr_url": f"/api/sub/{token}/qr.svg" if line.subscription_url else None,
    }


def build_order(db: Session, user: User, customer: Customer, payload: OrderIn, order_type: str = "purchase",
                renewal_line_id: int | None = None, discount_percent: float = 0.0,
                created_via: str = "self_service", skip_sales_notification: bool = False,
                allow_inactive_region: bool = False) -> Order:
    product = db.scalar(select(Product).where(Product.id == payload.product_id, Product.is_active.is_(True)))
    if not product or not product.is_in_stock:
        raise HTTPException(status_code=404, detail="产品不存在、已停用或暂时无货")
    region_id = payload.region_id or product.region_id
    # 续费要放行已停用地区：老线路的商品可能挂在一个后来被合并停用的地区上
    # （例如"香港"并入"中国香港"），客户照样应当能续费，否则停用一个地区
    # 就把历史客户的续费一起停掉了。新购仍严格要求地区在线。
    region_query = select(Region).where(Region.id == region_id)
    if not allow_inactive_region:
        region_query = region_query.where(Region.is_active.is_(True))
    region = db.scalar(region_query)
    if region_id != product.region_id or not region:
        raise HTTPException(status_code=422, detail="产品与地区不匹配")
    billing_days = payload.billing_days or product.billing_days
    unit_price, total_amount = calculate_order_price(db, product, payload.quantity, billing_days)
    # 客户指定的城市 / 号段：下单时校验并快照，失败直接 422 让人改选，
    # 绝不"自动换个城市"——静默替换等于交付了客户没要的东西。
    try:
        city_choice = resolve_choice(
            db, product, region,
            getattr(payload, "city_code", None), getattr(payload, "ip_segment", None),
        )
    except CitySelectionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    remark = payload.remark
    if discount_percent:
        # 折扣只在订单层施加，pricing_service 仍然是无折扣的唯一定价源：
        # 先按标价算出该周期单价，再按折扣比例折算，合计始终等于 折后单价 × 数量。
        factor = (Decimal("100") - Decimal(str(discount_percent))) / Decimal("100")
        discounted_unit = (Decimal(str(unit_price)) * factor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        unit_price = float(discounted_unit)
        total_amount = float((discounted_unit * payload.quantity).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        discount_note = f"管理员折扣 {discount_percent:g}%"
        remark = f"{remark}；{discount_note}" if remark else discount_note
    order = Order(
        order_no=f"ORD-{datetime.now().strftime('%Y%m%d')}-{uuid4().hex[:8].upper()}",
        customer_id=customer.id,
        product_id=product.id,
        amount=total_amount,
        quantity=payload.quantity,
        billing_days=billing_days,
        unit_price=unit_price,
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
        remark=remark,
        created_via=created_via,
        created_by_user_id=user.id,
        requested_city_code=city_choice.city_code,
        requested_city_name=city_choice.city_name,
        requested_ip_segment=city_choice.ip_segment,
    )
    db.add(order)
    db.flush()
    if order_type == "renewal":
        notify_order_parties(db, customer, order, "续费订单已创建", f"续费订单 {order.order_no} 已创建，原线路到期时间未变更。", "renewal", skip_sales=skip_sales_notification)
    else:
        city_note = ""
        if city_choice.specified:
            city_note = f"（指定城市：{city_choice.city_name or city_choice.city_code}"
            city_note += f"，IP 段 {city_choice.ip_segment}）" if city_choice.ip_segment else "）"
        notify_order_parties(db, customer, order, "订单已创建", f"订单 {order.order_no} 已创建，等待后续处理。{city_note}", "order", skip_sales=skip_sales_notification)
    detail = f"类型={order_type}; 数量={payload.quantity}; 周期={billing_days}"
    if city_choice.specified:
        detail += f"; 城市={city_choice.city_code or '-'}; 号段={city_choice.ip_segment or '-'}"
    write_audit(db, user, "create", "order", order.id, detail)
    return order


@router.post("/auth/login", response_model=TokenOut)
def login(
    payload: LoginIn,
    response: Response,
    request: Request,
    db: Session = Depends(get_db),
):
    # 限流要按用户名分桶，必须在拿到 payload 之后做，不能挂在依赖里。
    enforce_login_rate_limit(request, payload.username)
    if not _origin_allowed(request):
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
    # 不清 must_change_password：管理员重置密码后它就是 True，登录后必须先去改密。
    # 以前这里每次登录都置 False，等于把这个字段做成了死字段。
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
    length_error = _password_byte_length_error(payload.new_password)
    if length_error:
        raise HTTPException(status_code=422, detail=length_error)
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
    if not _origin_allowed(request):
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
def list_customers(response: Response, keyword: str | None = None, db: Session = Depends(get_db), user: User = Depends(current_user)):
    query = select(Customer).where(Customer.id.in_(scoped_customer_ids(db, user)))
    if keyword:
        query = query.where(Customer.name.contains(keyword) | Customer.email.contains(keyword))
    rows = db.scalars(query.order_by(Customer.created_at.desc()).limit(201)).all()
    return customer_views(db, cap_rows(response, rows, 200))


@router.post("/customers", response_model=CustomerOut)
def create_customer(payload: CustomerIn, db: Session = Depends(get_db), user: User = Depends(require_roles("super_admin", "admin", "sales"))):
    sales_id = payload.sales_id if user.role in {"super_admin", "admin"} else user.id
    ensure_unique_customer_email(db, payload.email)
    customer = Customer(**payload.model_dump(exclude={"sales_id"}), sales_id=sales_id)
    db.add(customer)
    write_audit(db, user, "create", "customer", detail=customer.name)
    db.commit()
    db.refresh(customer)
    return customer_view(customer, balance_cents_of(db, customer.user_id))


@router.get("/customers/{customer_id}", response_model=CustomerOut)
def get_customer(customer_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    customer = db.scalar(select(Customer).where(Customer.id == customer_id, Customer.id.in_(scoped_customer_ids(db, user))))
    if not customer:
        raise HTTPException(status_code=404, detail="客户不存在或无权访问")
    return customer_view(customer, balance_cents_of(db, customer.user_id))


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


@router.get("/orders", response_model=OrderPageOut)
def list_orders(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    status: str | None = Query(default=None, max_length=32),
    keyword: str | None = Query(default=None, max_length=64),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    base = select(Order)
    if user.role == "customer":
        base = base.where(Order.customer_id == get_customer_profile(db, user).id)
    elif user.role == "sales":
        base = base.where(Order.customer_id.in_(scoped_customer_ids(db, user)))
    status_counts = counts_by(db, base, "status")
    query = base
    if status:
        query = query.where(Order.status == status)
    if keyword and keyword.strip():
        query = query.where(Order.order_no.ilike(like_pattern(keyword.strip()), escape="\\"))
    items, total = paginate(db, query.order_by(Order.created_at.desc(), Order.id.desc()), page, page_size)
    return page_payload(items, total, page, page_size, status_counts=status_counts)


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
@router.get("/public/captcha", response_model=CaptchaChallengeOut)
def public_captcha(request: Request, _: None = Depends(enforce_captcha_rate_limit)):
    """注册用的九宫格点选题。

    答案存在加密 token 里，响应只给图形、目标名字和目标个数。图形是服务端现画的
    SVG，不含任何文本语义——扒 DOM 也抄不到答案（见 captcha_service 模块说明）。
    """
    if not _origin_allowed(request):
        raise HTTPException(status_code=403, detail="请求来源无效")
    return issue_challenge().public_view()


@router.post("/public/register", response_model=TokenOut, status_code=201)
def public_register(
    payload: RegisterIn,
    response: Response,
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(enforce_registration_rate_limit),
):
    """创建客户账号及绑定档案，不接受角色或销售归属字段。

    邀请码是唯一例外：客户走销售的码注册，归属写进 customers.sales_id。
    失效码不拒绝注册——归属是商家的内部状态，不是客户的错（设计见
    docs/销售邀请码客户归属设计.md §3.1）。

    图形验证码先于一切业务校验：它在最前面，用户名是否位占用、邮箱是否重复这些
    可以被探测的信号，就不能绕过验证码白拿。
    """
    if not _origin_allowed(request):
        raise HTTPException(status_code=403, detail="请求来源无效")
    try:
        verify_challenge(payload.captcha_token, payload.captcha_selection)
    except CaptchaError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    password_hash = hash_password(payload.password)
    if db.scalar(select(User.id).where(User.username == payload.username)):
        raise HTTPException(status_code=400, detail="无法完成注册，请检查提交信息")
    invite = lookup_active_code(db, payload.invite_code) if payload.invite_code else None
    ensure_unique_customer_email(db, payload.email)
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
        customer = Customer(
            name=payload.name,
            email=payload.email,
            phone=payload.phone,
            user_id=user.id,
            sales_id=invite[1].id if invite else None,
        )
        db.add(customer)
        db.flush()
        attempted_code = normalize_code(payload.invite_code)
        if invite:
            code, holder = invite
            code.used_count += 1
            write_audit(db, user, "invite_code_bind", "sales_invite_code", code.id,
                        f"码={code.code}; 客户={customer.id}; 销售={holder.id}")
            add_notification(db, holder.id, "新客户通过邀请码注册",
                             f"客户 {payload.name} 通过你的邀请码 {code.code} 注册，请在工作台跟进。", "customer")
        elif attempted_code:
            write_audit(db, user, "invite_code_rejected", "sales_invite_code", None,
                        f"尝试码={attempted_code}; 客户={customer.id}; 原因=失效或不存在，归公海")
            notify_admins(db, "公海新增客户（邀请码失效）",
                          f"新注册客户 {payload.name} 使用了失效邀请码 {attempted_code}，已进入公海待分配。",
                          "customer", marker=f"invite:{attempted_code}")
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
def public_products(db: Session = Depends(get_db), _: None = Depends(enforce_public_rate_limit)):
    rows = db.execute(select(Product, Region).join(Region, Product.region_id == Region.id).where(Product.is_active.is_(True), Region.is_active.is_(True)).order_by(Product.id)).all()
    return [{**ProductOut.model_validate(product).model_dump(), "region": region.name,
             "resource_category": product.resource_category,
             "periods": periods_of(db, product)} for product, region in rows]


@router.get("/public/products/{product_id}", response_model=PublicProductOut)
def public_product(product_id: int, db: Session = Depends(get_db), _: None = Depends(enforce_public_rate_limit)):
    row = db.execute(select(Product, Region).join(Region, Product.region_id == Region.id).where(Product.id == product_id, Product.is_active.is_(True), Region.is_active.is_(True))).first()
    if not row:
        raise HTTPException(status_code=404, detail="产品不存在或已停用")
    product, region = row
    return {**ProductOut.model_validate(product).model_dump(), "region": region.name,
            "resource_category": product.resource_category,
            "periods": periods_of(db, product)}


@router.get("/public/regions", response_model=list[RegionOut])
def public_regions(db: Session = Depends(get_db), _: None = Depends(enforce_public_rate_limit)):
    return list(db.scalars(select(Region).where(Region.is_active.is_(True)).order_by(Region.id)).all())


@router.post("/public/orders/preview", response_model=OrderPreviewOut)
def public_order_preview(payload: OrderPreviewIn, db: Session = Depends(get_db),
                         _: None = Depends(enforce_public_rate_limit),
                         user: User = Depends(current_user)):
    """算价预览。**必须登录**：它是价格的一手来源，免登录就等于绕过"登录可见价"。"""
    product = db.scalar(select(Product).where(Product.id == payload.product_id, Product.is_active.is_(True)))
    region = db.scalar(select(Region).where(Region.id == payload.region_id, Region.is_active.is_(True)))
    if not product or not product.is_in_stock:
        raise HTTPException(status_code=404, detail="产品不存在、已停用或暂时无货")
    if not region or product.region_id != region.id:
        raise HTTPException(status_code=422, detail="产品与地区不匹配")
    unit_price, total = calculate_order_price(db, product, payload.quantity, payload.billing_days)
    return {"product_id": product.id, "product_name": product.name, "region_id": region.id, "region": region.name,
            "quantity": payload.quantity, "billing_days": payload.billing_days, "unit_price": unit_price,
            "periods": periods_of(db, product), "total_amount": total}


def _site_line_of(product: Product) -> str | None:
    """商品归属的官网产品线；不属于三条线之一的商品不上官网。

    三条线：静态住宅IP / 社媒运营专线 / 直播专线。旧的原生、ISP、机房分类都归到
    住宅线（它们本来就是静态住宅的细分）；没有资源分类的固定价商品不上官网。
    """
    return CATEGORY_TO_LINE.get(product.resource_category or "")


def _active_regions(db: Session) -> list[Region]:
    return list(db.scalars(select(Region).where(Region.is_active.is_(True)).order_by(Region.name)).all())


def _catalog_rows(db: Session):
    return db.execute(
        select(Product, Region)
        .join(Region, Product.region_id == Region.id)
        .where(
            Product.is_active.is_(True),
            Region.is_active.is_(True),
            Product.resource_category.is_not(None),
        )
        .order_by(Region.name, Product.billing_days, Product.id)
    ).all()


def _all_product_cells(db: Session) -> set[tuple[str, int]]:
    """所有（含已停用）商品覆盖的 (产品线, 地区)，用于区分两种"没报价"：

    - 这个国家本来就没配过商品 → 管理员上架后展示「暂无报价」是有意为之；
    - 配过商品但被停用 → 跟随商品一起从官网消失，不展示"暂无报价"的假入口。
    """
    rows = db.execute(
        select(Product.resource_category, Product.region_id)
        .where(Product.resource_category.is_not(None))
        .distinct()
    ).all()
    cells = set()
    for category, region_id in rows:
        line = CATEGORY_TO_LINE.get(category or "")
        if line:
            cells.add((line, region_id))
    return cells


def _listed_cells(db: Session) -> set[tuple[str, int]]:
    """(产品线, 地区) → 是否在官网可售。

    2026-11-10 起改为**人工上架制**：只有存在 status=listed 的记录才算上架，
    没有记录一律视为下架。池级记录（pool_id 非空）同样计入——只要某个池上架了，
    这个国家就对客户可见；具体由哪个池供货由路由决定（见 docs/双上游资源池设计.md）。
    """
    return {
        (row.line_code, row.region_id)
        for row in db.scalars(select(CatalogListing).where(CatalogListing.status == "listed")).all()
    }


def _build_catalog(db: Session, *, with_price: bool) -> list[dict]:
    """按三条产品线聚合，只输出**管理员已上架**的国家。

    可见性规则（见 docs/双上游资源池设计.md 第 3 节）：

    1. **人工上架制**：没有上架记录 = 不出现。新国家、新商品、新池的货都不会自动上线，
       必须管理员在「商品与价格 → 官网可见性」里点开。
    2. 池维度：某个池上架了该国家即可见；池级与平台级记录取并集。
    3. 上架了但没报价：仍然出现，展示「暂无报价」且不可下单（这是管理员的有意决定）。

    ``with_price=False`` 时不返回任何金额与 offer，未登录用户拿不到价格。
    """
    regions = _active_regions(db)
    listed = _listed_cells(db)
    known_cells = _all_product_cells(db)

    # 先把每条线上"哪些国家有商品"统计出来，再决定每个格子要不要出现。
    priced: dict[str, dict[int, list]] = {line: {} for line in LINE_ORDER}
    for product, region in _catalog_rows(db):
        line = _site_line_of(product)
        if line:
            priced.setdefault(line, {}).setdefault(region.id, []).append(product)

    groups: dict[str, dict] = {}
    for line in sorted(LINE_ORDER, key=lambda code: LINE_ORDER[code]):
        entries: dict[int, dict] = {}
        for region in regions:
            key = (line, region.id)
            if key not in listed:
                continue  # 人工上架制：没上架就不出现
            has_offer = region.id in priced.get(line, {})
            if not has_offer and key in known_cells:
                continue  # 商品被停用：跟随下架，不展示"暂无报价"的假入口
            entries[region.id] = {
                "id": region.id,
                "name": region.name,
                "country_code": region.country_code,
                "min_price": None,
                "in_stock": False,
                "offers": [],
                "listed_by_admin": True,
            }
        groups[line] = {
            "code": line,
            "name": LINE_LABELS[line],
            "min_price": None,
            "periods": set(),
            "in_stock": False,
            "regions": entries,
        }
    for product, region in _catalog_rows(db):
        line = _site_line_of(product)
        group = groups.get(line) if line else None
        if group is None:
            continue
        entry = group["regions"].get(region.id)
        if entry is None:
            continue
        # 周期档位按商品的 30 天基础价线性派生，与定价服务口径一致。
        for days in (SELLABLE_PERIODS if product.billing_days == 30 else (product.billing_days,)):
            group["periods"].add(days)
        group["in_stock"] = group["in_stock"] or bool(product.is_in_stock)
        entry["in_stock"] = entry["in_stock"] or bool(product.is_in_stock)
        # 未登录也要能选「产品 → 国家 → 规格」，所以 offer 结构照发，只把金额抹掉。
        # 网页上本来就公开展示有哪些产品和国家，这里不构成信息泄露；
        # 金额相关的聚合（min_price）仍然只在 with_price=True 时计算。
        price = float(product.price) if with_price else None
        if price is not None:
            group["min_price"] = price if group["min_price"] is None else min(group["min_price"], price)
            entry["min_price"] = price if entry["min_price"] is None else min(entry["min_price"], price)
        entry["offers"].append({
            "product_id": product.id,
            "billing_days": product.billing_days,
            "unit_price": price,
            "in_stock": bool(product.is_in_stock),
            "spec": LIVE_SPEC_LABELS.get(product.resource_category or ""),
        })
    result = []
    for line in sorted(groups, key=lambda code: LINE_ORDER[code]):
        group = groups[line]
        result.append({
            "code": group["code"],
            "name": group["name"],
            "subtitle": LINE_SUBTITLES.get(group["code"]),
            "min_price": group["min_price"],
            "periods": sorted(group["periods"]),
            "region_count": len(group["regions"]),
            "in_stock": group["in_stock"],
            "regions": list(group["regions"].values()),
        })
    return result


def _catalog_payload(db: Session, *, with_price: bool) -> dict:
    lines = _build_catalog(db, with_price=with_price)
    return {
        "lines": lines,
        "price_visible": with_price,
        "product_count": len(_catalog_rows(db)),
        "region_count": len(_active_regions(db)),
    }


def _catalog_line(db: Session, code: str, *, with_price: bool) -> dict:
    for line in _build_catalog(db, with_price=with_price):
        if line["code"] == code:
            return line
    raise HTTPException(status_code=404, detail="产品线不存在")


@router.get("/public/catalog")
def public_catalog(db: Session = Depends(get_db), _: None = Depends(enforce_public_rate_limit)):
    """未登录可见的目录：产品线、国家、周期，**不含任何价格**。

    价格属于登录后可见的信息，公开接口一律脱敏，避免"前端藏起来、接口照给"。
    """
    return _catalog_payload(db, with_price=False)


@router.get("/public/catalog/{code}")
def public_catalog_detail(code: str, db: Session = Depends(get_db), _: None = Depends(enforce_public_rate_limit)):
    """未登录可见的产品线详情：地区与周期，不含价格与 offer。"""
    return _catalog_line(db, code, with_price=False)


@router.get("/catalog")
def catalog_for_signed_in_user(db: Session = Depends(get_db), _: User = Depends(current_user)):
    """登录后可见的目录：与公开接口同结构，多出价格与可下单的 offer。"""
    return _catalog_payload(db, with_price=True)


@router.get("/catalog/cities")
def catalog_city_options(
    product_id: int = Query(ge=1),
    db: Session = Depends(get_db),
    _: User = Depends(current_user),
):
    """某个商品可指定的城市码与 IP 段（可选精细化选项）。

    必须注册在 ``/catalog/{code}`` 之前，否则 "cities" 会被当成产品线代码。
    未配置城市时返回 supported=False，前端只显示"自动分配"。
    """
    product = db.get(Product, product_id)
    if not product or not product.is_active:
        raise HTTPException(status_code=404, detail="产品不存在或已下架")
    return customer_options(db, product)


@router.get("/catalog/{code}")
def catalog_line_for_signed_in_user(code: str, db: Session = Depends(get_db), _: User = Depends(current_user)):
    return _catalog_line(db, code, with_price=True)


# 客户接口：客户身份和数据范围全部从 Token 解析。
@router.get("/customer/me", response_model=CustomerOut)
def customer_me(db: Session = Depends(get_db), user: User = Depends(require_roles("customer"))):
    customer = get_customer_profile(db, user)
    return customer_view(customer, balance_cents_of(db, customer.user_id))


@router.get("/customer/orders", response_model=OrderPageOut)
def customer_orders(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    status: str | None = Query(default=None, max_length=32),
    keyword: str | None = Query(default=None, max_length=64),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("customer")),
):
    customer = get_customer_profile(db, user)
    base = select(Order).where(Order.customer_id == customer.id)
    # 分布要在加状态筛选之前算，否则选中"已完成"后下拉框里只剩"已完成"。
    status_counts = counts_by(db, base, "status")
    query = base
    if status:
        query = query.where(Order.status == status)
    if keyword and keyword.strip():
        query = query.where(Order.order_no.ilike(like_pattern(keyword.strip()), escape="\\"))
    items, total = paginate(db, query.order_by(Order.created_at.desc(), Order.id.desc()), page, page_size)
    return page_payload(items, total, page, page_size, status_counts=status_counts)


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


@router.post("/orders/on-behalf", response_model=OrderOut)
def create_order_on_behalf(
    payload: OnBehalfOrderIn,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("sales", "admin", "super_admin")),
):
    """销售/管理员代客下单。

    - 销售只能给名下客户开单；别人的客户一律 404，不区分"不存在"与"无权"，
      否则这个接口就成了客户存在性探测工具；
    - 折扣只有管理员能设，上限 MAX_ORDER_DISCOUNT_PERCENT（默认 30%，即 7 折）；
    - 幂等键必填：同键同客户返回原单，同键不同客户 409，避免重试串单。
    """
    customer = db.scalar(select(Customer).where(Customer.id == payload.customer_id, Customer.is_deleted.is_(False)))
    if not customer or (actor.role == "sales" and customer.sales_id != actor.id):
        raise HTTPException(status_code=404, detail="客户不存在或无权操作")
    existing = db.scalar(select(Order).where(Order.idempotency_key == payload.idempotency_key))
    if existing is not None:
        if existing.customer_id != customer.id:
            raise HTTPException(status_code=409, detail="请求重复但客户不一致，请刷新后重试")
        return existing
    if payload.discount_percent:
        if actor.role not in {"admin", "super_admin"}:
            raise HTTPException(status_code=403, detail="折扣只能由管理员设置")
        limit = get_settings().max_order_discount_percent
        if payload.discount_percent > limit:
            raise HTTPException(status_code=422, detail=f"折扣超过上限（{limit}%），请联系管理员")
    if not customer.user_id and payload.payment_method != "contact_manager":
        # 没有登录账号的客户无法自己在线支付，也看不到余额；只能走人工收款。
        raise HTTPException(status_code=422, detail="该客户还没有登录账号，只能用「联系经理」方式下单")
    order_payload = OrderIn(
        product_id=payload.product_id, region_id=payload.region_id, quantity=payload.quantity,
        billing_days=payload.billing_days, payment_method=payload.payment_method, remark=payload.remark,
        city_code=payload.city_code, ip_segment=payload.ip_segment,
    )
    try:
        order = build_order(
            db, actor, customer, order_payload,
            discount_percent=payload.discount_percent or 0.0,
            created_via="sales_assisted" if actor.role == "sales" else "admin",
            skip_sales_notification=True,
        )
        order.idempotency_key = payload.idempotency_key
        write_audit(db, actor, "create", "order", order.id, f"代客下单; 客户={customer.name}; 折扣={payload.discount_percent or 0:g}%")
        db.commit()
    except IntegrityError:
        # 并发同键：唯一约束挡住第二条，回滚后回查并返回已有单，仍然保证幂等。
        db.rollback()
        existing = db.scalar(select(Order).where(Order.idempotency_key == payload.idempotency_key))
        if existing is not None and existing.customer_id == customer.id:
            return existing
        raise HTTPException(status_code=409, detail="重复提交，请刷新后重试")
    except Exception:
        db.rollback()
        raise
    db.refresh(order)
    return order


@router.post("/customer/orders/{order_id}/cancel", response_model=OrderOut)
def customer_cancel_order(order_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles("customer"))):
    customer = get_customer_profile(db, user)
    order = db.scalar(select(Order).where(Order.id == order_id, Order.customer_id == customer.id))
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在或无权访问")
    if order.status not in {"pending_payment", "pending_confirm", "renewal"}:
        raise HTTPException(status_code=409, detail="当前订单状态不允许取消")
    ensure_order_cancellable(db, order)
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
    cache: dict = {}
    return [customer_line_payload(db, line, cache) for line in lines]


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


def _create_renewal_order(db: Session, actor: User, customer: Customer, line: CustomerLine,
                          payload: RenewalIn, remark_suffix: str = "") -> Order:
    """续费建单。客户自助与销售代客共用这一份，价格口径与闸门完全一致。"""
    if line.status == "deleted":
        raise HTTPException(status_code=409, detail="已删除线路不能续费")
    duplicate = db.scalar(select(Order).where(
        Order.renewal_line_id == line.id,
        Order.order_type == "renewal",
        Order.status.not_in(TERMINAL_ORDER_STATUSES),
    ))
    if duplicate:
        raise HTTPException(status_code=409, detail="该线路已有进行中的续费订单")
    product = db.get(Product, line.product_id)
    order_payload = OrderIn(product_id=line.product_id, region_id=product.region_id if product else None,
                            quantity=1, billing_days=payload.billing_days,
                            payment_method=payload.payment_method,
                            remark=f"线路 {line.id} 续费{remark_suffix}")
    # 续费放行已停用地区，理由见 build_order 里 allow_inactive_region 的注释
    return build_order(db, actor, customer, order_payload, "renewal", line.id,
                       allow_inactive_region=True)


@router.post("/customer/lines/{line_id}/renew", response_model=OrderOut)
def customer_renew_line(line_id: int, payload: RenewalIn, db: Session = Depends(get_db), user: User = Depends(require_roles("customer"))):
    customer = get_customer_profile(db, user)
    line = db.scalar(select(CustomerLine).where(CustomerLine.id == line_id, CustomerLine.customer_id == customer.id))
    if not line:
        raise HTTPException(status_code=404, detail="线路不存在或无权访问")
    try:
        order = _create_renewal_order(db, user, customer, line, payload)
        db.commit()
        db.refresh(order)
        return order
    except Exception:
        db.rollback()
        raise


def received_revenue_cents(db: Session, customer_ids, order_type: str, start_utc: datetime, end_utc: datetime) -> int:
    """区间内某类订单的实际回款（分）：已收款减去同期已完成的退款。

    口径与财务概览一致——按 ``paid_at`` 落在区间内的收款统计，退款按退款发生时间冲减，
    因此退款不会追溯改写上个月的业绩。含续费单。
    """
    paid = db.scalar(
        select(func.coalesce(func.sum(PaymentOrder.amount_cents), 0))
        .join(Order, Order.id == PaymentOrder.order_id)
        .where(
            Order.customer_id.in_(customer_ids),
            Order.order_type == order_type,
            PaymentOrder.purpose == "order_payment",
            PaymentOrder.status.in_({"paid", "refunded"}),
            PaymentOrder.paid_at.is_not(None),
            PaymentOrder.paid_at >= start_utc,
            PaymentOrder.paid_at < end_utc,
        )
    ) or 0
    refunded = db.scalar(
        select(func.coalesce(func.sum(PaymentRefund.amount_cents), 0))
        .join(PaymentOrder, PaymentOrder.id == PaymentRefund.payment_order_id)
        .join(Order, Order.id == PaymentOrder.order_id)
        .where(
            Order.customer_id.in_(customer_ids),
            Order.order_type == order_type,
            PaymentRefund.status == "completed",
            PaymentRefund.created_at >= start_utc,
            PaymentRefund.created_at < end_utc,
        )
    ) or 0
    return int(paid) - int(refunded)


# 销售接口：始终限定到当前销售负责的客户。
@router.get("/sales/dashboard")
def sales_dashboard(db: Session = Depends(get_db), user: User = Depends(require_roles("sales"))):
    customer_ids = scoped_customer_ids(db, user)
    now = utcnow()
    # 业绩按下单转成按回款：区间取上海时区的「本月 1 日 00:00 至今」，与财务口径一致。
    today_local = local_date_of(now)
    start_utc, end_utc = local_day_range_utc(today_local.replace(day=1), today_local)
    return {
        "customers": db.scalar(select(func.count()).select_from(Customer).where(Customer.id.in_(customer_ids))) or 0,
        "active_lines": db.scalar(select(func.count()).select_from(CustomerLine).where(CustomerLine.customer_id.in_(customer_ids), CustomerLine.status == "active")) or 0,
        "expiring_in_7_days": db.scalar(select(func.count()).select_from(CustomerLine).where(CustomerLine.customer_id.in_(customer_ids), CustomerLine.status == "active", CustomerLine.expires_at >= now, CustomerLine.expires_at <= now + timedelta(days=7))) or 0,
        "pending_confirm_orders": db.scalar(select(func.count()).select_from(Order).where(Order.customer_id.in_(customer_ids), Order.status == "pending_confirm")) or 0,
        "pending_delivery_orders": db.scalar(select(func.count()).select_from(Order).where(Order.customer_id.in_(customer_ids), Order.status.in_({"pending_delivery", "partially_delivered"}))) or 0,
        "unread_notifications": db.scalar(select(func.count()).select_from(Notification).where(Notification.user_id == user.id, Notification.is_read.is_(False))) or 0,
        "monthly_sales": received_revenue_cents(db, customer_ids, "purchase", start_utc, end_utc) / 100,
        "monthly_renewals": received_revenue_cents(db, customer_ids, "renewal", start_utc, end_utc) / 100,
    }


@router.get("/sales/customers", response_model=list[CustomerOut])
def sales_customers(db: Session = Depends(get_db), user: User = Depends(require_roles("sales"))):
    return customer_views(db, db.scalars(select(Customer).where(Customer.sales_id == user.id, Customer.is_deleted.is_(False)).order_by(Customer.created_at.desc())).all())


@router.get("/sales/orders", response_model=OrderPageOut)
def sales_orders(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    status: str | None = Query(default=None, max_length=32),
    keyword: str | None = Query(default=None, max_length=64),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("sales")),
):
    base = select(Order).where(Order.customer_id.in_(scoped_customer_ids(db, user)))
    status_counts = counts_by(db, base, "status")
    query = base
    if status:
        query = query.where(Order.status == status)
    if keyword and keyword.strip():
        query = query.where(Order.order_no.ilike(like_pattern(keyword.strip()), escape="\\"))
    items, total = paginate(db, query.order_by(Order.created_at.desc(), Order.id.desc()), page, page_size)
    return page_payload(items, total, page, page_size, status_counts=status_counts)


@router.get("/sales/orders/{order_id}", response_model=OrderOut)
def sales_order_detail(order_id: int, db: Session = Depends(get_db),
                       user: User = Depends(require_roles("sales"))):
    """销售看名下客户的订单详情。越界返回 404（与"不存在"同一文案，不能用来探测）。"""
    order = db.scalar(select(Order).where(
        Order.id == order_id,
        Order.customer_id.in_(scoped_customer_ids(db, user)),
    ))
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在或无权访问")
    return order


@router.get("/sales/lines", response_model=list[CustomerLineOut])
def sales_lines(
    customer_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("sales")),
):
    """名下客户的线路。带 customer_id 时只返回该客户，越界 404。"""
    # scoped_customer_ids 返回的是子查询，要先取出 id 集合才能做成员判断
    visible_ids = set(db.scalars(scoped_customer_ids(db, user)).all())
    query = select(CustomerLine).where(CustomerLine.customer_id.in_(visible_ids))
    if customer_id:
        if customer_id not in visible_ids:
            raise HTTPException(status_code=404, detail="客户不存在或无权访问")
        query = query.where(CustomerLine.customer_id == customer_id)
    lines = db.scalars(query.order_by(CustomerLine.expires_at.asc())).all()
    cache: dict = {}
    return [customer_line_payload(db, line, cache) for line in lines]


@router.get("/sales/renewals", response_model=list[OrderOut])
def sales_renewals(db: Session = Depends(get_db), user: User = Depends(require_roles("sales"))):
    """名下客户的续费订单，按创建时间倒序。"""
    return list(db.scalars(
        select(Order)
        .where(Order.customer_id.in_(scoped_customer_ids(db, user)), Order.order_type == "renewal")
        .order_by(Order.created_at.desc())
        .limit(200)
    ).all())


@router.post("/sales/lines/{line_id}/renew", response_model=OrderOut)
def sales_renew_line(line_id: int, payload: RenewalIn, db: Session = Depends(get_db),
                     user: User = Depends(require_roles("sales"))):
    """销售代客户发起续费。金额与官网一致，不另给折扣。"""
    line = db.scalar(select(CustomerLine).where(
        CustomerLine.id == line_id,
        CustomerLine.customer_id.in_(scoped_customer_ids(db, user)),
    ))
    if not line:
        raise HTTPException(status_code=404, detail="线路不存在或无权访问")
    customer = db.get(Customer, line.customer_id)
    if customer is None or customer.is_deleted:
        raise HTTPException(status_code=404, detail="客户不存在或无权访问")
    try:
        order = _create_renewal_order(db, user, customer, line, payload, remark_suffix="（销售代办）")
        db.commit()
        db.refresh(order)
        return order
    except Exception:
        db.rollback()
        raise


@router.get("/sales/expiring-lines", response_model=list[CustomerLineOut])
def sales_expiring_lines(days: int = Query(default=30, ge=1, le=365), db: Session = Depends(get_db), user: User = Depends(require_roles("sales"))):
    now = utcnow()
    lines = db.scalars(select(CustomerLine).where(CustomerLine.customer_id.in_(scoped_customer_ids(db, user)), CustomerLine.status == "active", CustomerLine.expires_at >= now, CustomerLine.expires_at <= now + timedelta(days=days)).order_by(CustomerLine.expires_at)).all()
    cache: dict = {}
    return [customer_line_payload(db, line, cache) for line in lines]


@router.get("/sales/health-impacts")
def sales_health_impacts(db: Session = Depends(get_db), user: User = Depends(require_roles("sales"))):
    rows = db.execute(select(HealthIncident, Node).join(Node, HealthIncident.node_id == Node.id).where(HealthIncident.node_id.in_(visible_node_ids(db, user)), HealthIncident.status == "open").order_by(HealthIncident.started_at.desc())).all()
    return [{"incident_id": incident.id, "line_name": node.name, "status": node.health_status, "started_at": incident.started_at, "reason": incident.reason} for incident, node in rows]


# ---- 销售邀请码：走谁的码注册，客户就归谁（docs/销售邀请码客户归属设计.md）----

def _invite_code_view(code: SalesInviteCode, sales_name: str | None) -> InviteCodeOut:
    """码 → 输出视图。注册链接由前端域名拼接，方便销售直接贴进微信。"""
    origin = get_settings().frontend_origin.rstrip("/")
    return InviteCodeOut(
        id=code.id, code=code.code, sales_user_id=code.sales_user_id, sales_name=sales_name,
        status=code.status, max_uses=code.max_uses, used_count=code.used_count,
        expires_at=code.expires_at, note=code.note,
        register_url=f"{origin}/register?invite={code.code}",
        created_at=code.created_at, disabled_at=code.disabled_at,
    )


@router.get("/public/invite-codes/{code}", response_model=InviteCodePublicOut)
def public_invite_code_check(code: str, db: Session = Depends(get_db),
                             _: None = Depends(enforce_invite_check_rate_limit)):
    """注册页展示"邀请人：某某"用。有效/失效响应同构，不回原因细节，防枚举探测。"""
    found = lookup_active_code(db, code)
    if not found:
        return {"valid": False, "inviter_name": None}
    return {"valid": True, "inviter_name": found[1].display_name}


@router.get("/sales/invite-codes", response_model=list[InviteCodeOut])
def sales_invite_codes(db: Session = Depends(get_db), user: User = Depends(require_roles("sales"))):
    codes = db.scalars(select(SalesInviteCode).where(SalesInviteCode.sales_user_id == user.id)
                       .order_by(SalesInviteCode.created_at.desc(), SalesInviteCode.id.desc())).all()
    return [_invite_code_view(code, user.display_name) for code in codes]


@router.post("/sales/invite-codes", response_model=InviteCodeOut, status_code=201)
def sales_create_invite_code(payload: InviteCodeCreateIn, db: Session = Depends(get_db),
                             user: User = Depends(require_roles("sales"))):
    code = create_invite_code(db, user, note=payload.note, max_uses=payload.max_uses, expires_at=payload.expires_at)
    write_audit(db, user, "create", "sales_invite_code", code.id, f"码={code.code}; 备注={payload.note or '-'}")
    db.commit()
    db.refresh(code)
    return _invite_code_view(code, user.display_name)


@router.post("/sales/invite-codes/{code_id}/disable", response_model=InviteCodeOut)
def sales_disable_invite_code(code_id: int, db: Session = Depends(get_db),
                              user: User = Depends(require_roles("sales"))):
    # 只查自己的码：别人的码 ID 直接 404，不区分存在性。
    code = db.scalar(select(SalesInviteCode).where(SalesInviteCode.id == code_id, SalesInviteCode.sales_user_id == user.id))
    if not code:
        raise HTTPException(status_code=404, detail="邀请码不存在")
    disable_invite_code(db, code, user)
    write_audit(db, user, "disable", "sales_invite_code", code.id, f"码={code.code}")
    db.commit()
    db.refresh(code)
    return _invite_code_view(code, user.display_name)


@router.get("/admin/invite-codes", response_model=list[InviteCodeOut])
def admin_invite_codes(sales_id: int | None = Query(default=None), status: str | None = Query(default=None, max_length=20),
                       db: Session = Depends(get_db), _: User = Depends(require_roles("super_admin", "admin"))):
    query = select(SalesInviteCode, User).join(User, SalesInviteCode.sales_user_id == User.id)
    if sales_id is not None:
        query = query.where(SalesInviteCode.sales_user_id == sales_id)
    if status:
        query = query.where(SalesInviteCode.status == status)
    rows = db.execute(query.order_by(SalesInviteCode.created_at.desc(), SalesInviteCode.id.desc())).all()
    return [_invite_code_view(code, holder.display_name) for code, holder in rows]


@router.post("/admin/invite-codes/{code_id}/disable", response_model=InviteCodeOut)
def admin_disable_invite_code(code_id: int, db: Session = Depends(get_db),
                              actor: User = Depends(require_roles("super_admin", "admin"))):
    row = db.execute(select(SalesInviteCode, User).join(User, SalesInviteCode.sales_user_id == User.id)
                     .where(SalesInviteCode.id == code_id)).first()
    if not row:
        raise HTTPException(status_code=404, detail="邀请码不存在")
    code, holder = row
    disable_invite_code(db, code, actor)
    write_audit(db, actor, "disable", "sales_invite_code", code.id,
                f"码={code.code}; 强制停用; 持有人={holder.display_name}")
    add_notification(db, holder.id, "邀请码已被停用",
                     f"你的邀请码 {code.code} 已被管理员停用。", "customer")
    db.commit()
    db.refresh(code)
    return _invite_code_view(code, holder.display_name)


# 管理员订单处理接口：状态变化、审计与通知在同一事务提交。
@router.get("/admin/orders", response_model=OrderPageOut)
def admin_orders(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    status: str | None = Query(default=None, max_length=32),
    keyword: str | None = Query(default=None, max_length=64),
    customer_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("super_admin", "admin")),
):
    base = select(Order)
    if customer_id:
        base = base.where(Order.customer_id == customer_id)
    status_counts = counts_by(db, base, "status")
    query = base
    if status:
        query = query.where(Order.status == status)
    if keyword and keyword.strip():
        query = query.where(Order.order_no.ilike(like_pattern(keyword.strip()), escape="\\"))
    items, total = paginate(db, query.order_by(Order.created_at.desc(), Order.id.desc()), page, page_size)
    return page_payload(items, total, page, page_size, status_counts=status_counts)


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
    # 订单中心的「销售单 · 自动采购」标签必须反映真实开关：
    # 开关关着时客户付款只到 paid，不会向上游下单，标成自动采购会误导运营。
    from app.services.settings_service import get_flag, AUTO_PROCUREMENT_KEY

    procurement_enabled, procurement_source = get_flag(db, AUTO_PROCUREMENT_KEY)

    # 商品所在国家：销售单要按「类型 → 国家 → 城市」逐级收敛，
    # 前端不能只靠 product_type 猜，得拿到 region 的国家码。
    region_ids = {item.region_id for item in products if item.region_id}
    regions = {item.id: item for item in db.scalars(
        select(Region).where(Region.id.in_(region_ids))).all()} if region_ids else {}

    # 城市与号段：与客户下单页同一口径（该国有池 1 同步行时只信任池 1），
    # 否则手工清单会把上游买不到的城市带进后台下拉。
    # 社媒/直播没有自有城市清单，复用住宅位置（shared=True）且不给号段。
    from app.services.city_option_service import city_rows_for

    products_payload = [{"id": item.id, "name": item.name, "product_type": item.product_type,
                        "price": item.price, "billing_days": item.billing_days, "region_id": item.region_id,
                        "resource_category": item.resource_category,
                        "country_code": (regions.get(item.region_id).country_code or "").upper()
                        if regions.get(item.region_id) else None,
                        "country_name": regions.get(item.region_id).name if regions.get(item.region_id) else None}
                       for item in products]
    sale_pairs = sorted({
        (item["resource_category"], item["country_code"])
        for item in products_payload
        if item["resource_category"] and item["country_code"]
    })
    city_options: list[dict] = []
    for category, country_code in sale_pairs:
        rows, shared = city_rows_for(db, category, country_code)
        for row in rows:
            city_options.append({
                "resource_category": category,
                "country_code": country_code,
                "city_code": row.city_code,
                "city_name": row.city_name,
                "segments": [] if shared else normalize_segments(row.ip_segments),
                "shared": shared,
            })

    return {
        "auto_procurement": {"enabled": procurement_enabled, "source": procurement_source},
        "customers": [{"id": item.id, "name": item.name, "email": item.email} for item in customers],
        "products": products_payload,
        "city_options": city_options,
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
    response: Response,
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
    rows = db.execute(query.order_by(CustomerLine.expires_at.asc()).limit(2001)).all()
    rows = cap_rows(response, rows, 2000)
    return [{
        "id": line.id, "customer_id": line.customer_id, "customer_name": customer_name,
        "product_id": line.product_id, "product_name": product_name, "order_id": line.order_id,
        "source_type": order_source or "owned", "static_ip": line.static_ip, "status": line.status,
        "started_at": line.started_at, "expires_at": line.expires_at, "created_at": line.created_at,
    } for line, customer_name, product_name, order_source in rows]


@router.get("/admin/order-center/orders")
def admin_order_center_orders(response: Response, db: Session = Depends(get_db), user: User = Depends(require_roles("super_admin", "admin"))):
    rows = db.execute(select(Order, Customer.name).join(Customer, Customer.id == Order.customer_id)
                      .order_by(Order.created_at.desc()).limit(1001)).all()
    rows = cap_rows(response, rows, 1000)
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
    supply_rule = db.scalar(select(ProductSupplyRule).join(
        ResourcePool, ResourcePool.id == ProductSupplyRule.pool_id,
    ).where(
        ProductSupplyRule.product_id == product.id,
        ProductSupplyRule.region_id == product.region_id,
        ProductSupplyRule.supplier_product_id == supplier_product.id,
        ProductSupplyRule.supplier_region_id == supplier_region.id,
        ResourcePool.code == "global_proxy_pool",
    ))
    legacy_mapping = supplier_product.local_product_id == product.id
    if (not (supply_rule or legacy_mapping) or not supplier_product.is_available):
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
    # 定价口径：月价 × 周期数（线性、无折扣）。资源池下单与官网必须同价。
    if billing_days not in SELLABLE_PERIODS:
        raise HTTPException(status_code=409, detail=f"仅支持 {'/'.join(map(str, SELLABLE_PERIODS))} 天周期")
    try:
        unit_price = unit_price_for(monthly_price_for(db, payload.resource_category, supplier_region.country_code), billing_days)
    except PricingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    # 审计里要写清售价取自哪一版价目表；月价只认 30 天档，长周期价是派生的。
    retail_rule = db.scalar(select(RetailPriceRule).where(
        RetailPriceRule.resource_category == payload.resource_category,
        RetailPriceRule.country_code == supplier_region.country_code.strip().upper(),
        RetailPriceRule.billing_days == 30,
    ))
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
                    f"来源=resource_pool; 分类={payload.resource_category}; 国家={supplier_region.country_code}; 周期={billing_days}; 数量={payload.quantity}; 售价来源={retail_rule.source_version if retail_rule else '零售价目表'}; 售价={total}; 成本快照={cost_total}")
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


@router.get("/admin/lines/{line_id}/upgrade-options")
def admin_line_upgrade_options(line_id: int, db: Session = Depends(get_db),
                               user: User = Depends(require_roles("super_admin", "admin"))):
    """这条线路能升级到哪些产品线，以及各自要补多少差价。

    差价按原订单周期折算（不按剩余天数），与 ``admin_upgrade_line`` 同一口径，
    前端只负责展示，不自己算钱。
    """
    line = db.get(CustomerLine, line_id)
    if not line or line.status == "deleted":
        raise HTTPException(status_code=404, detail="线路不存在或已删除")
    current = db.get(Product, line.product_id)
    if current is None or not current.resource_category:
        # 老数据里有些产品没归入资源分类（无法按价目表算升级差价）。
        # 这不是"崩溃"，而是一条明确的可解释状态，前端据此提示而不是弹红色错误。
        return {
            "current": None,
            "options": [],
            "reason": "线路当前产品没有资源分类，无法计算升级差价；请先在商品管理里归类",
        }
    current_unit, _ = calculate_order_price(db, current, 1, current.billing_days)
    candidates = db.scalars(select(Product).where(
        Product.region_id == current.region_id,
        Product.billing_days == current.billing_days,
        Product.is_active.is_(True),
        Product.resource_category.is_not(None),
        Product.resource_category != current.resource_category,
    ).order_by(Product.id)).all()
    options = []
    for item in candidates:
        try:
            unit, _ = calculate_order_price(db, item, 1, current.billing_days)
        except HTTPException:
            continue
        diff = round(float(unit) - float(current_unit), 2)
        if diff <= 0:
            continue
        options.append({
            "category": item.resource_category,
            "label": RESOURCE_CATEGORY_CODES.get(item.resource_category, item.resource_category),
            "product_id": item.id,
            "product_name": item.name,
            "unit_price": float(unit),
            "diff": diff,
        })
    return {
        "current": {
            "category": current.resource_category,
            "label": RESOURCE_CATEGORY_CODES.get(current.resource_category, current.resource_category),
            "product_name": current.name, "unit_price": float(current_unit),
            "billing_days": current.billing_days, "expires_at": line.expires_at,
        },
        "options": options,
    }


@router.post("/admin/lines/{line_id}/upgrade", status_code=201)
def admin_upgrade_line(line_id: int, payload: LineUpgradeIn, db: Session = Depends(get_db),
                       user: User = Depends(require_roles("super_admin", "admin"))):
    """把一条既有线路升级到更高的产品线（静态住宅 → 社媒/直播）。

    口径（业务确认）：只补差价 =（目标单价 − 原单价）× 原订单周期倍数，
    **到期时间不变**。原住宅 IP 保留，社媒/直播只是多一层中转。

    这里只创建待付款/待确认的升级单，线路在收款后才切换（见 ``settle_upgrade``）——
    先改线路再收钱，退款时就回不去了。
    """
    line = db.get(CustomerLine, line_id)
    if not line or line.status == "deleted":
        raise HTTPException(status_code=404, detail="线路不存在或已删除")
    customer = db.get(Customer, line.customer_id)
    if not customer or customer.is_deleted or not customer.user_id:
        raise HTTPException(status_code=409, detail="客户账号不可用，无法升级")
    current = db.get(Product, line.product_id)
    if current is None:
        raise HTTPException(status_code=409, detail="线路当前产品不存在")
    if payload.target_category not in RESOURCE_CATEGORY_CODES:
        raise HTTPException(status_code=422, detail="目标产品类型无效")
    if payload.target_category == current.resource_category:
        raise HTTPException(status_code=409, detail="目标类型与当前类型相同，无需升级")

    target = db.scalar(select(Product).where(
        Product.region_id == current.region_id,
        Product.billing_days == current.billing_days,
        Product.resource_category == payload.target_category,
        Product.is_active.is_(True),
    ).order_by(Product.id).limit(1))
    if target is None:
        raise HTTPException(status_code=409, detail="该国家与周期下没有目标产品线，请先上架对应 SKU")

    current_unit, _ = calculate_order_price(db, current, 1, current.billing_days)
    target_unit, _ = calculate_order_price(db, target, 1, current.billing_days)
    diff = round(float(target_unit) - float(current_unit), 2)
    if diff <= 0:
        raise HTTPException(status_code=409, detail="只能升级到单价更高的产品线")

    existing = db.scalar(select(Order).where(Order.idempotency_key == payload.idempotency_key))
    if existing is not None:
        if existing.renewal_line_id != line.id:
            raise HTTPException(status_code=409, detail="请求重复但线路不一致，请刷新后重试")
        existing_customer = db.get(Customer, existing.customer_id)
        return {**{column.name: getattr(existing, column.name) for column in Order.__table__.columns},
                "customer_name": existing_customer.name if existing_customer else "未知客户"}

    order = Order(
        order_no=f"ORD-{datetime.now().strftime('%Y%m%d')}-{uuid4().hex[:8].upper()}",
        customer_id=customer.id, product_id=target.id, source_type="owned",
        idempotency_key=payload.idempotency_key,
        amount=diff, quantity=1, billing_days=current.billing_days,
        unit_price=diff, total_amount=diff,
        region_id=current.region_id,
        region_name=(db.get(Region, current.region_id).name if current.region_id else ""),
        product_name=f"{target.name}（升级）",
        payment_method=payload.payment_method, payment_status="unpaid",
        status=initial_order_status(payload.payment_method, "upgrade"),
        order_type="upgrade", renewal_line_id=line.id,
        assigned_sales_id=customer.sales_id, created_via="admin",
        created_by_user_id=user.id,
        remark=f"升级差价：{target.name} {target_unit} − {current.name} {current_unit} = {diff}；到期时间不变",
    )
    try:
        db.add(order)
        db.flush()
        write_audit(db, user, "upgrade_line", "customer_line", line.id,
                    f"线路={line.id}; 从={current.resource_category}; 到={payload.target_category}; "
                    f"差价={diff}; 周期={current.billing_days}天; 订单={order.order_no}")
        db.commit()
        db.refresh(order)
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(Order).where(Order.idempotency_key == payload.idempotency_key))
        if existing is not None:
            existing_customer = db.get(Customer, existing.customer_id)
            return {**{column.name: getattr(existing, column.name) for column in Order.__table__.columns},
                    "customer_name": existing_customer.name if existing_customer else "未知客户"}
        raise
    except Exception:
        db.rollback()
        raise
    return {**{column.name: getattr(order, column.name) for column in Order.__table__.columns},
            "customer_name": customer.name}


@router.post("/admin/orders/{order_id}/confirm", response_model=OrderOut)
def admin_confirm_order(order_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db), user: User = Depends(require_roles("super_admin", "admin"))):
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在")
    if order.source_type == "resource_pool":
        raise HTTPException(status_code=409, detail="资源池自动采购尚未启用，不能确认采购或开通线路")
    if order.order_type == "upgrade":
        # 升级单用「确认收款」推进：确认即切产品线，到期时间不变。
        if order.status not in {"pending_confirm", "pending_payment", "paid"}:
            raise HTTPException(status_code=409, detail="订单状态不允许确认升级")
        line = settle_upgrade(db, order, "manually_confirmed")
        write_audit(db, user, "confirm_upgrade", "order", order.id,
                    f"升级确认；线路={line.id}; 差价={order.total_amount}")
        db.commit()
        db.refresh(order)
        return order
    if order.order_type != "purchase":
        raise HTTPException(status_code=409, detail="仅普通购买订单可以确认交付")
    if order.status not in {"pending_confirm", "pending_payment", "paid", "processing"}:
        raise HTTPException(status_code=409, detail="订单状态不允许确认")
    ensure_payment_settled(order)
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
        write_audit(db, user, "confirm", "order", order.id, f"收款状态={order.payment_status}; 收款方式={order.payment_method}")
        notify_order_parties(db, customer, order, "订单已确认", f"订单 {order.order_no} 已确认，等待线路交付。", "order")
        db.commit()
        db.refresh(order)
        # 确认收款后尝试自动采购；开关没开或产品不支持时静默跳过
        if order.order_type == "purchase":
            background_tasks.add_task(run_auto_procure_in_background, order.id)
        return order
    except Exception:
        db.rollback()
        raise


@router.post("/admin/orders/{order_id}/retry-procurement")
def retry_order_procurement(
    order_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("super_admin", "admin")),
):
    """人工再试一次自动开通。

    只有在「已收款、还没有成功采购记录」时才会真的去上游下单——采购是扣钱动作，
    不能因为点了一下就重复买。已经有成功记录的直接返回结果，不重复下单。
    """
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="订单不存在")
    if order.order_type != "purchase":
        raise HTTPException(status_code=409, detail="只有购买订单可以自动开通")
    if order.source_type == "resource_pool":
        raise HTTPException(status_code=409, detail="资源池订单不走这条开通链路")
    if order.payment_status != "paid":
        raise HTTPException(status_code=409, detail="订单还没收款，不能开通")
    done = db.scalar(select(Procurement).where(
        Procurement.order_id == order.id, Procurement.status == "succeeded",
    ))
    if done is not None:
        return {"status": "already_delivered", "procurement_id": done.id, "line_id": done.line_id}
    background_tasks.add_task(run_auto_procure_in_background, order.id)
    write_audit(db, user, "retry_procurement", "order", order.id, "人工触发自动开通")
    db.commit()
    return {"status": "queued"}


def _resolve_delivery_node(db: Session, payload: DeliverOrderIn, region_id: int):
    """交付时确定节点：贴了链接就按链接解析，同一个 server/端口/协议 直接复用。

    复用是为了避免每交付一条线路就多一行 nodes——同一个出口卖给多个客户时，
    节点信息本来就一样，重复建行只会让后台节点列表越滚越长。
    """
    parsed = None
    if payload.node_link:
        try:
            parsed = parse_link(payload.node_link)
        except ProxyLinkError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    if payload.node_id:
        node = db.scalar(select(Node).where(Node.id == payload.node_id, Node.is_enabled.is_(True)))
        if node is None:
            raise HTTPException(status_code=400, detail="指定的节点不存在或已停用")
        return node, parsed
    node = db.scalar(select(Node).where(
        Node.server == parsed.server,
        Node.port == parsed.port,
        Node.protocol == parsed.protocol,
        Node.region_id == region_id,
    ))
    if node is None:
        node = Node(
            name=(parsed.name or f"{parsed.protocol}-{parsed.server}")[:120],
            server=parsed.server,
            port=parsed.port,
            protocol=parsed.protocol,
            region_id=region_id,
            is_enabled=True,
        )
        db.add(node)
        db.flush()
    return node, parsed


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
    ensure_payment_settled(order)
    customer = db.scalar(select(Customer).where(Customer.id == order.customer_id, Customer.is_deleted.is_(False)))
    product = db.scalar(select(Product).where(Product.id == order.product_id, Product.is_active.is_(True)))
    now = utcnow()
    if not customer or not customer.user_id or not product:
        raise HTTPException(status_code=400, detail="客户或产品不可用")
    if not payload.node_id and not payload.node_link:
        raise HTTPException(status_code=422, detail="请粘贴节点链接，或指定已有节点")
    order_region_id = order.region_id or product.region_id
    node, _parsed = _resolve_delivery_node(db, payload, order_region_id)
    if node.region_id != order_region_id or product.region_id != order_region_id:
        raise HTTPException(status_code=400, detail="节点、产品与订单地区不一致")
    # 订阅地址是客户级的：同一个客户所有线路共用一个地址，换线路不用换订阅。
    # 只有贴了节点链接才给订阅——只填 node_id 时订阅里没有任何节点，给了反而让客户点开是空配置。
    public_base = (get_settings().frontend_origin or "").rstrip("/")
    subscription_url = payload.subscription_url or (
        f"{public_base}/api/sub/{issue_token(customer.id)}"
        if public_base and payload.node_link else None
    )
    if payload.expires_at.replace(tzinfo=None) <= now:
        raise HTTPException(status_code=422, detail="到期时间必须晚于当前时间")
    try:
        if not claim_delivery_slot(db, order.id, now):
            db.rollback()
            raise HTTPException(status_code=409, detail="订单交付数量已满或状态不允许交付")
        db.expire(order)
        db.refresh(order)
        line = CustomerLine(customer_id=customer.id, node_id=node.id, product_id=product.id, order_id=order.id, static_ip=payload.static_ip, started_at=now, expires_at=payload.expires_at.replace(tzinfo=None), customer_note=payload.customer_note, node_link=payload.node_link, subscription_url=subscription_url, node_qr_data=payload.node_qr_data or payload.node_link, subscription_qr_data=payload.subscription_qr_data or subscription_url)
        db.add(line)
        db.flush()
        if order.region_id is None:
            order.region_id = product.region_id
            order.region_name = db.get(Region, product.region_id).name
        if order.delivered_line_id is None:
            order.delivered_line_id = line.id
        progress = f"{order.delivered_quantity}/{order.quantity}"
        write_audit(db, user, "deliver", "order", order.id, f"线路={line.id}; 进度={progress}; 收款状态={order.payment_status}")
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
    if order.status not in {"renewal", "pending_payment"}:
        raise HTTPException(status_code=409, detail="当前续费订单状态不允许完成")
    try:
        claimed = db.execute(
            update(Order)
            .where(
                Order.id == order.id,
                Order.status.in_({"renewal", "pending_payment"}),
                Order.renewal_completed_at.is_(None),
            )
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
        line = settle_renewal(db, order, "manually_confirmed")
        write_audit(db, user, "complete_renewal", "order", order.id,
                    f"线路={line.id}; 延长天数={order.billing_days}; 收款口径=人工确认")
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
    if order.status in {"partially_delivered", "delivered", "completed", "cancelled", "refunding", "refunded", "paid", "processing"}:
        raise HTTPException(status_code=409, detail="当前订单状态不允许取消，已收款订单请走退款")
    ensure_order_cancellable(db, order)
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
        return customer_view(customer, balance_cents_of(db, customer.user_id))
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
    length_error = _password_byte_length_error(payload.new_password)
    if length_error:
        raise HTTPException(status_code=422, detail=length_error)
    target = _manageable_user(db, actor, user_id)
    target.password_hash = hash_password(payload.new_password)
    # 管理员重置出来的是"临时密码"：标记强制改密，用户下次登录必须先改成自己的密码。
    target.must_change_password = True
    revoke_user_sessions(db, target, "password_reset")
    write_audit(db, actor, "reset_password", "user", target.id, "已撤销全部会话；要求下次登录修改密码")
    db.commit()


@router.patch("/admin/users/{user_id}/access", response_model=UserOut)
def admin_change_access(user_id: int, payload: UserAccessIn, db: Session = Depends(get_db),
                        actor: User = Depends(require_roles("super_admin", "admin"))):
    target = _manageable_user(db, actor, user_id)
    was_sales = target.role == "sales"
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
        # 销售被停用或改角色后，其邀请码必须跟着失效，否则新客户还会绑到
        # 一个不再服务的人头上（注册时 lookup_active_code 也会按持有人过滤，
        # 这里是双保险 + 让码列表状态正确）。
        if was_sales and (not target.is_active or target.role != "sales"):
            disabled_count = disable_codes_of_sales(db, target.id, actor)
            if disabled_count:
                write_audit(db, actor, "disable_invite_codes", "user", target.id,
                            f"级联停用邀请码 {disabled_count} 个")
        db.commit()
    return target
