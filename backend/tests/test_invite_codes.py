"""销售邀请码：生成、注册绑定、失效兜底、权限边界。

设计约束（docs/销售邀请码客户归属设计.md）：
- 走谁的码注册，customers.sales_id 就是谁；
- 失效码不拒绝注册（归公海 + 通知管理员）；
- 码只能系统生成，一个销售最多 10 个 active；
- 销售只碰自己的码（IDOR 防御），管理员全量 + 强制停用。
"""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import hash_password
from app.db import get_db
from app.main import app
from app.models import AuditLog, Customer, Notification, SalesInviteCode, User
from app.models.base import Base, utcnow
from app.services.registration_service import registration_rate_limiter

MAX_ACTIVE_CODES = 10


@pytest.fixture()
def api():
    registration_rate_limiter.clear()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()
    admin = User(username="admin", password_hash=hash_password("Admin_123456"), role="admin", display_name="管理员")
    sales_a = User(username="sales_a", password_hash=hash_password("Sales_123456"), role="sales", display_name="销售 A")
    sales_b = User(username="sales_b", password_hash=hash_password("Sales_123456"), role="sales", display_name="销售 B")
    db.add_all([admin, sales_a, sales_b])
    db.commit()

    def override_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    client.db = db
    client.users = {"admin": admin, "sales_a": sales_a, "sales_b": sales_b}
    yield client
    registration_rate_limiter.clear()
    app.dependency_overrides.clear()
    db.close()
    engine.dispose()


def token(client, username, password):
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def auth(token_value):
    return {"Authorization": f"Bearer {token_value}"}


def register_payload(username, invite_code=None):
    payload = {"username": username, "password": "Register_123456", "name": f"客户{username}", "email": f"{username}@example.com"}
    if invite_code is not None:
        payload["invite_code"] = invite_code
    return payload


def make_code(api, sales_username="sales_a", token_value=None, **kwargs):
    if token_value is None:
        token_value = token(api, sales_username, "Sales_123456")
    response = api.post("/api/sales/invite-codes", json=kwargs, headers=auth(token_value))
    assert response.status_code == 201, response.text
    return response.json()


def test_sales_creates_code_and_register_binds_customer(api):
    code = make_code(api, note="官网轮播")
    assert len(code["code"]) == 8
    assert code["register_url"].endswith(f"/register?invite={code['code']}")
    response = api.post("/api/public/register", json=register_payload("invited_user", code["code"]))
    assert response.status_code == 201, response.text
    api.db.expire_all()
    customer = api.db.scalar(select(Customer).where(Customer.name == "客户invited_user"))
    assert customer.sales_id == api.users["sales_a"].id
    stored = api.db.scalar(select(SalesInviteCode).where(SalesInviteCode.code == code["code"]))
    assert stored.used_count == 1
    # 审计 + 销售通知
    assert api.db.scalar(select(AuditLog.id).where(AuditLog.action == "invite_code_bind"))
    assert api.db.scalar(select(Notification.id).where(
        Notification.user_id == api.users["sales_a"].id,
        Notification.title == "新客户通过邀请码注册"))


def test_invite_code_case_insensitive_and_whitespace_tolerant(api):
    code = make_code(api)
    raw = f"  {code['code'].lower()}  "
    response = api.post("/api/public/register", json=register_payload("case_user", raw))
    assert response.status_code == 201, response.text
    api.db.expire_all()
    customer = api.db.scalar(select(Customer).where(Customer.name == "客户case_user"))
    assert customer.sales_id == api.users["sales_a"].id


def test_invalid_code_still_registers_into_public_pool(api):
    """失效码不拒绝注册：客户无感知，归公海 + 通知管理员。"""
    response = api.post("/api/public/register", json=register_payload("no_code_user", "ZZZZ9999"))
    assert response.status_code == 201, response.text
    api.db.expire_all()
    customer = api.db.scalar(select(Customer).where(Customer.name == "客户no_code_user"))
    assert customer.sales_id is None
    assert api.db.scalar(select(AuditLog.id).where(AuditLog.action == "invite_code_rejected"))
    assert api.db.scalar(select(Notification.id).where(
        Notification.user_id == api.users["admin"].id,
        Notification.title == "公海新增客户（邀请码失效）"))


def test_disabled_code_falls_back_to_public_pool(api):
    sales_token = token(api, "sales_a", "Sales_123456")
    code = make_code(api)
    disabled = api.post(f"/api/sales/invite-codes/{code['id']}/disable", headers=auth(sales_token))
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "disabled"
    assert api.post("/api/public/register", json=register_payload("after_disable", code["code"])).status_code == 201
    api.db.expire_all()
    customer = api.db.scalar(select(Customer).where(Customer.name == "客户after_disable"))
    assert customer.sales_id is None


def test_expired_or_exhausted_code_falls_back(api):
    expired = make_code(api, expires_at=(utcnow() - timedelta(hours=1)).isoformat())
    assert api.post("/api/public/register", json=register_payload("expired_user", expired["code"])).status_code == 201
    limited = make_code(api, max_uses=1)
    assert api.post("/api/public/register", json=register_payload("first_user", limited["code"])).status_code == 201
    assert api.post("/api/public/register", json=register_payload("second_user", limited["code"])).status_code == 201
    api.db.expire_all()
    for name in ("客户expired_user", "客户second_user"):
        assert api.db.scalar(select(Customer).where(Customer.name == name)).sales_id is None


def test_code_of_inactive_holder_is_invalid(api):
    code = make_code(api)
    admin_token = token(api, "admin", "Admin_123456")
    response = api.patch(f"/api/admin/users/{api.users['sales_a'].id}/access",
                         json={"is_active": False}, headers=auth(admin_token))
    assert response.status_code == 200, response.text
    # 级联停用：账号一停，码全部失效。
    api.db.expire_all()
    stored = api.db.scalar(select(SalesInviteCode).where(SalesInviteCode.code == code["code"]))
    assert stored.status == "disabled"
    assert api.post("/api/public/register", json=register_payload("orphan_user", code["code"])).status_code == 201
    api.db.expire_all()
    assert api.db.scalar(select(Customer).where(Customer.name == "客户orphan_user")).sales_id is None


def test_sales_capped_at_ten_active_codes_and_recovers_after_disable(api):
    # 复用同一个令牌：每个用例内多次登录会撞 IP×用户名限流（10 次/分钟）。
    sales_token = token(api, "sales_a", "Sales_123456")
    created = [make_code(api, token_value=sales_token) for _ in range(MAX_ACTIVE_CODES)]
    overflow = api.post("/api/sales/invite-codes", json={}, headers=auth(sales_token))
    assert overflow.status_code == 422
    api.post(f"/api/sales/invite-codes/{created[0]['id']}/disable", headers=auth(sales_token))
    again = api.post("/api/sales/invite-codes", json={}, headers=auth(sales_token))
    assert again.status_code == 201


def test_sales_cannot_touch_other_sales_codes(api):
    code_a = make_code(api, sales_username="sales_a")
    sales_b_token = token(api, "sales_b", "Sales_123456")
    # 列表里只有自己的码
    listing = api.get("/api/sales/invite-codes", headers=auth(sales_b_token))
    assert listing.status_code == 200
    assert all(item["id"] != code_a["id"] for item in listing.json())
    # 停别人的码：404，不区分存在性
    assert api.post(f"/api/sales/invite-codes/{code_a['id']}/disable", headers=auth(sales_b_token)).status_code == 404


def test_customer_cannot_create_codes(api):
    api.post("/api/public/register", json=register_payload("plain_user"))
    customer_token = token(api, "plain_user", "Register_123456")
    assert api.post("/api/sales/invite-codes", json={}, headers=auth(customer_token)).status_code == 403
    assert api.get("/api/sales/invite-codes", headers=auth(customer_token)).status_code == 403


def test_public_check_endpoint_is_shape_stable_and_rate_limited(api):
    code = make_code(api)
    valid = api.get(f"/api/public/invite-codes/{code['code']}")
    assert valid.status_code == 200
    assert valid.json() == {"valid": True, "inviter_name": "销售 A"}
    invalid = api.get("/api/public/invite-codes/ZZZZ9999")
    assert invalid.status_code == 200
    assert invalid.json() == {"valid": False, "inviter_name": None}
    # 限流：与注册同级（默认 5 次/分钟/IP）。上面已消耗 2 次（有效 + 无效各一）。
    for _ in range(3):
        assert api.get("/api/public/invite-codes/ZZZZ9999").status_code == 200
    assert api.get("/api/public/invite-codes/ZZZZ9999").status_code == 429


def test_admin_force_disable_notifies_sales_and_audits(api):
    code = make_code(api)
    admin_token = token(api, "admin", "Admin_123456")
    response = api.post(f"/api/admin/invite-codes/{code['id']}/disable", headers=auth(admin_token))
    assert response.status_code == 200
    assert response.json()["status"] == "disabled"
    api.db.expire_all()
    assert api.db.scalar(select(Notification.id).where(
        Notification.user_id == api.users["sales_a"].id,
        Notification.title == "邀请码已被停用"))
    assert api.db.scalar(select(AuditLog.id).where(
        AuditLog.action == "disable", AuditLog.resource_type == "sales_invite_code"))
    # 管理员全量列表可见（带销售名）
    listing = api.get("/api/admin/invite-codes", headers=auth(admin_token))
    assert listing.status_code == 200
    assert any(item["sales_name"] == "销售 A" for item in listing.json())


def test_register_without_code_unchanged(api):
    """回归：不带 invite_code 的注册行为与改造前完全一致。"""
    response = api.post("/api/public/register", json=register_payload("plain_user2"))
    assert response.status_code == 201, response.text
    api.db.expire_all()
    assert api.db.scalar(select(Customer).where(Customer.name == "客户plain_user2")).sales_id is None


def test_register_rejects_privileged_fields_still(api):
    payload = {**register_payload("attack_user"), "sales_id": api.users["sales_a"].id}
    assert api.post("/api/public/register", json=payload).status_code == 422
    payload = {**register_payload("attack_user2"), "invite_code": "ab"}
    assert api.post("/api/public/register", json=payload).status_code == 422
