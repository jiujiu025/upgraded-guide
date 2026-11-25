from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import hash_password
from app.db import get_db
from app.main import app
from app.models import AuditLog, AuthSession, User
from app.models.base import Base, utcnow
from app.services.registration_service import registration_rate_limiter


ORIGIN = "http://localhost:5173"


@pytest.fixture()
def api():
    registration_rate_limiter.clear()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        db.add_all([
            User(username="chief", password_hash=hash_password("Chief_123456!"), role="super_admin", display_name="主管理员"),
            User(username="staff", password_hash=hash_password("Staff_123456!"), role="admin", display_name="管理员"),
            User(username="sales", password_hash=hash_password("Sales_123456!"), role="sales", display_name="销售"),
            User(username="customer", password_hash=hash_password("Customer_123456!"), role="customer", display_name="客户"),
        ])
        db.commit()

    def isolated_db():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = isolated_db
    client = TestClient(app, base_url="https://localhost:5173")
    client.factory = factory
    yield client
    client.close()
    app.dependency_overrides.clear()
    registration_rate_limiter.clear()
    engine.dispose()


def login(api, name: str):
    response = api.post("/api/auth/login", json={"username": name, "password": f"{name.capitalize()}_123456!"}, headers={"Origin": ORIGIN})
    assert response.status_code == 200, response.text
    return response.json()


def portal_login(api, name: str, portal: str):
    response = api.post(f"/api/auth/login?portal={portal}",
                        json={"username": name, "password": f"{name.capitalize()}_123456!"},
                        headers={"Origin": ORIGIN})
    assert response.status_code == 200, response.text
    return response.json()


def bearer(data):
    return {"Authorization": f"Bearer {data['access_token']}"}


def test_cookie_rotation_replay_and_csrf(api):
    data = login(api, "customer")
    cookie = api.cookies.get("__Host-ops_refresh")
    assert cookie
    header = api.post("/api/auth/login", json={"username": "sales", "password": "Sales_123456!"}, headers={"Origin": ORIGIN}).headers["set-cookie"]
    assert "HttpOnly" in header and "Secure" in header and "SameSite=lax" in header and "Domain=" not in header
    api.cookies.set("__Host-ops_refresh", cookie)
    assert api.post("/api/auth/refresh", headers={"Origin": ORIGIN}).status_code == 403
    assert api.post("/api/auth/refresh", headers={"Origin": "https://evil.example", "X-CSRF-Token": data["csrf_token"]}).status_code == 403
    refreshed = api.post("/api/auth/refresh", headers={"Origin": ORIGIN, "X-CSRF-Token": data["csrf_token"]})
    assert refreshed.status_code == 200, refreshed.text
    new_data = refreshed.json()
    assert api.get("/api/auth/me", headers=bearer(new_data)).status_code == 200
    api.cookies.set("__Host-ops_refresh", cookie)
    replay = api.post("/api/auth/refresh", headers={"Origin": ORIGIN, "X-CSRF-Token": data["csrf_token"]})
    assert replay.status_code == 401
    assert api.get("/api/auth/me", headers=bearer(new_data)).status_code == 401


def test_logout_expiry_and_admin_revoke(api):
    chief = login(api, "chief")
    customer = login(api, "customer")
    session_id = api.cookies.get("__Host-ops_refresh")
    assert api.post("/api/auth/logout", headers={"Origin": ORIGIN, "X-CSRF-Token": customer["csrf_token"]}).status_code == 204
    assert api.get("/api/auth/me", headers=bearer(customer)).status_code == 401
    customer = login(api, "customer")
    with api.factory() as db:
        target = db.scalar(select(User).where(User.username == "customer"))
        session = db.scalar(select(AuthSession).where(AuthSession.user_id == target.id).order_by(AuthSession.id.desc()))
        target_id, session_id = target.id, session.id
    assert api.post(f"/api/admin/users/{target_id}/sessions/{session_id}/revoke", headers=bearer(chief)).status_code == 204
    assert api.get("/api/auth/me", headers=bearer(customer)).status_code == 401
    customer = login(api, "customer")
    with api.factory() as db:
        session = db.scalar(select(AuthSession).order_by(AuthSession.id.desc()))
        session.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert api.get("/api/auth/me", headers=bearer(customer)).status_code == 401
    assert api.post("/api/auth/refresh", headers={"Origin": ORIGIN, "X-CSRF-Token": customer["csrf_token"]}).status_code == 401


def test_reset_password_and_role_isolation(api):
    chief = login(api, "chief")
    staff = login(api, "staff")
    sales = login(api, "sales")
    customer = login(api, "customer")
    with api.factory() as db:
        ids = {item.username: item.id for item in db.scalars(select(User)).all()}
    assert api.get("/api/admin/users/1/sessions", headers=bearer(customer)).status_code == 403
    assert api.get("/api/admin/users/1/sessions", headers=bearer(sales)).status_code == 403
    assert api.post(f"/api/admin/users/{ids['staff']}/reset-password", json={"new_password": "NewAdmin_123!", "confirm": True}, headers=bearer(staff)).status_code == 403
    assert api.post(f"/api/admin/users/{ids['staff']}/reset-password", json={"new_password": "NewAdmin_123!", "confirm": False}, headers=bearer(chief)).status_code == 422
    reset = api.post(f"/api/admin/users/{ids['staff']}/reset-password", json={"new_password": "NewAdmin_123!", "confirm": True}, headers=bearer(chief))
    assert reset.status_code == 204
    assert api.get("/api/auth/me", headers=bearer(staff)).status_code == 401
    new_login = api.post("/api/auth/login", json={"username": "staff", "password": "NewAdmin_123!"}, headers={"Origin": ORIGIN}).json()
    # 管理员重置出来的是临时密码：标记为必须改密，业务接口会被 428 拦住
    assert new_login["user"]["must_change_password"] is True
    assert api.post("/api/auth/change-password", json={"current_password": "NewAdmin_123!", "new_password": "NextAdmin_123!"}, headers=bearer(new_login)).status_code == 204
    assert api.get("/api/auth/me", headers=bearer(new_login)).status_code == 401
    after_change = api.post("/api/auth/login", json={"username": "staff", "password": "NextAdmin_123!"},
                            headers={"Origin": ORIGIN})
    assert after_change.status_code == 200
    # 改密之后才轮到角色隔离：staff 不是管理员，访问管理接口必须是 403
    assert api.get("/api/admin/users/1/sessions", headers=bearer(after_change.json())).status_code == 403
    with api.factory() as db:
        audit = db.scalar(select(AuditLog).where(AuditLog.action == "reset_password"))
        assert "NewAdmin" not in audit.detail
    assert api.patch(f"/api/admin/users/{ids['sales']}/access", json={"role": "customer"}, headers=bearer(chief)).status_code == 200
    assert api.get("/api/sales/dashboard", headers=bearer(sales)).status_code == 401


def test_portals_keep_separate_refresh_cookies_in_one_browser(api):
    """同一个浏览器同时登管理端和客户端，两边的 refresh cookie 不能互相覆盖。

    旧实现只有一个 __Host-ops_refresh：后登的入口把 cookie 顶掉，先登的那一侧
    刷新时拿不到自己的令牌，于是"管理员一登录，客户端就自己下线"。现在每个
    入口各有一份 cookie，谁登录都不影响别人。
    """
    admin = portal_login(api, "chief", "admin")
    assert api.cookies.get("__Host-ops_refresh_admin")
    customer = portal_login(api, "customer", "customer")
    assert api.cookies.get("__Host-ops_refresh_customer")
    # 关键断言：客户登录后，管理员那份 cookie 还在
    assert api.cookies.get("__Host-ops_refresh_admin")

    # 两个会话都能独立通过鉴权
    assert api.get("/api/auth/me", headers=bearer(admin)).status_code == 200
    assert api.get("/api/auth/me", headers=bearer(customer)).status_code == 200

    # 各自拿自己入口的 CSRF，各自刷新，互不干扰
    admin_csrf = api.get("/api/auth/csrf?portal=admin", headers={"Origin": ORIGIN})
    customer_csrf = api.get("/api/auth/csrf?portal=customer", headers={"Origin": ORIGIN})
    assert admin_csrf.status_code == 200 and customer_csrf.status_code == 200
    admin_refreshed = api.post("/api/auth/refresh?portal=admin", headers={
        "Origin": ORIGIN, "X-CSRF-Token": admin_csrf.json()["csrf_token"]})
    assert admin_refreshed.status_code == 200, admin_refreshed.text
    assert api.get("/api/auth/me", headers=bearer(admin_refreshed.json())).status_code == 200

    # 客户登出只清自己那一侧，管理员不受影响
    assert api.post("/api/auth/logout?portal=customer", headers={
        "Origin": ORIGIN, "X-CSRF-Token": customer_csrf.json()["csrf_token"]}).status_code == 204
    assert api.cookies.get("__Host-ops_refresh_admin")
    assert api.get("/api/auth/me", headers=bearer(admin_refreshed.json())).status_code == 200


def test_login_rejects_wrong_portal(api):
    """入口与角色不符必须拒绝，否则客户能从后台登录页拿到管理端 cookie。"""
    response = api.post("/api/auth/login?portal=admin",
                        json={"username": "customer", "password": "Customer_123456!"},
                        headers={"Origin": ORIGIN})
    assert response.status_code == 401
    assert not api.cookies.get("__Host-ops_refresh_admin")
    # 销售也一样，不能从管理端入口进
    response = api.post("/api/auth/login?portal=admin",
                        json={"username": "sales", "password": "Sales_123456!"},
                        headers={"Origin": ORIGIN})
    assert response.status_code == 401
    # 自己的入口能正常登
    assert portal_login(api, "sales", "sales")["user"]["role"] == "sales"
