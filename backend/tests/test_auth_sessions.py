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


def test_reset_forced_change_and_role_isolation(api):
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
    assert new_login["user"]["must_change_password"] is True
    assert api.get("/api/admin/users/1/sessions", headers=bearer(new_login)).status_code == 403
    assert api.post("/api/auth/change-password", json={"current_password": "NewAdmin_123!", "new_password": "NextAdmin_123!"}, headers=bearer(new_login)).status_code == 204
    assert api.get("/api/auth/me", headers=bearer(new_login)).status_code == 401
    with api.factory() as db:
        audit = db.scalar(select(AuditLog).where(AuditLog.action == "reset_password"))
        assert "NewAdmin" not in audit.detail
    assert api.patch(f"/api/admin/users/{ids['sales']}/access", json={"role": "customer"}, headers=bearer(chief)).status_code == 200
    assert api.get("/api/sales/dashboard", headers=bearer(sales)).status_code == 401
