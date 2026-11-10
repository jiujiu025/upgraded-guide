"""强制改密：管理员重置密码后，用户必须先改密才能用业务功能（P3-1）。

`must_change_password` 以前是死字段——登录时无条件置 False，管理员重置时置 True
也没人看。现在：重置 → 标记 True；登录不再清标记；除 /api/auth/* 外的接口一律 428。
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import hash_password
from app.db import get_db
from app.main import app
from app.models import Customer, User
from app.models.base import Base


@pytest.fixture()
def api():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()

    admin = User(username="admin", password_hash=hash_password("Admin_123456"), role="admin", display_name="管理员")
    customer_user = User(username="cust", password_hash=hash_password("Customer_123456"), role="customer",
                         display_name="客户")
    db.add_all([admin, customer_user])
    db.flush()
    db.add(Customer(name="客户一", email="c@test.com", user_id=customer_user.id))
    db.commit()

    def override_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    yield {"client": TestClient(app), "db": db, "admin": admin, "customer_user": customer_user, "Session": Session}
    app.dependency_overrides.clear()


def login(client, username, password):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def test_admin_reset_marks_password_change_required(api):
    admin_token = login(api["client"], "admin", "Admin_123456").json()["access_token"]
    response = api["client"].post(
        f"/api/admin/users/{api['customer_user'].id}/reset-password",
        json={"new_password": "TempPass_123456", "confirm": True},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 204
    api["db"].expire_all()
    assert api["db"].get(User, api["customer_user"].id).must_change_password is True


def test_login_keeps_flag_and_business_endpoints_are_blocked(api):
    admin_token = login(api["client"], "admin", "Admin_123456").json()["access_token"]
    api["client"].post(
        f"/api/admin/users/{api['customer_user'].id}/reset-password",
        json={"new_password": "TempPass_123456", "confirm": True},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    result = login(api["client"], "cust", "TempPass_123456")
    assert result.status_code == 200
    assert result.json()["user"]["must_change_password"] is True
    token = result.json()["access_token"]

    # 业务接口被拦住
    blocked = api["client"].get("/api/customer/me", headers={"Authorization": f"Bearer {token}"})
    assert blocked.status_code == 428
    assert "修改密码" in blocked.json()["detail"]

    # 认证类接口仍然可用，否则用户连改密页都进不去
    me = api["client"].get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200


def test_changing_password_clears_the_gate(api):
    admin_token = login(api["client"], "admin", "Admin_123456").json()["access_token"]
    api["client"].post(
        f"/api/admin/users/{api['customer_user'].id}/reset-password",
        json={"new_password": "TempPass_123456", "confirm": True},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    token = login(api["client"], "cust", "TempPass_123456").json()["access_token"]
    changed = api["client"].post(
        "/api/auth/change-password",
        json={"current_password": "TempPass_123456", "new_password": "MyOwnPass_123456"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert changed.status_code == 204

    api["db"].expire_all()
    assert api["db"].get(User, api["customer_user"].id).must_change_password is False

    # 改密后重新登录，业务接口恢复
    new_token = login(api["client"], "cust", "MyOwnPass_123456").json()["access_token"]
    assert api["client"].get("/api/customer/me", headers={"Authorization": f"Bearer {new_token}"}).status_code == 200


def test_normal_login_is_not_affected(api):
    token = login(api["client"], "cust", "Customer_123456").json()["access_token"]
    assert api["client"].get("/api/customer/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200
