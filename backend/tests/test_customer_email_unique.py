"""客户邮箱唯一（P3-5）：注册与后台建客户都不允许重复邮箱。

`customers.email` 没有数据库唯一索引（存量数据可能已有重复，且软删除客户不该占位），
所以在应用层校验；大小写与首尾空格视为同一个邮箱。
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import create_access_token, hash_password
from app.db import get_db
from app.main import app
from app.models import Customer, User
from app.models.base import Base
from app.services.session_service import create_session


@pytest.fixture()
def api():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()

    admin = User(username="admin", password_hash=hash_password("Admin_123456"), role="admin", display_name="管理员")
    db.add(admin)
    db.flush()
    db.add(Customer(name="已有客户", email="Taken@Test.com"))
    db.commit()
    session_row, _ = create_session(db, admin)
    db.commit()
    headers = {"Authorization": f"Bearer {create_access_token(admin.id, admin.role, session_row.id, admin.auth_version)}"}

    def override_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    yield {"client": TestClient(app), "headers": headers, "db": db}
    app.dependency_overrides.clear()


def test_create_customer_rejects_duplicate_email(api):
    response = api["client"].post(
        "/api/customers",
        json={"name": "新客户", "email": " taken@test.COM ", "phone": None, "status": "潜在客户", "level": "standard"},
        headers=api["headers"],
    )
    assert response.status_code == 400
    assert "邮箱" in response.json()["detail"]


def test_registration_rejects_duplicate_email(api):
    response = api["client"].post("/api/public/register", json={
        "username": "newuser01", "password": "StrongPass_123", "name": "注册客户",
        "email": "TAKEN@test.com", "phone": None,
    })
    assert response.status_code == 400
    assert "邮箱" in response.json()["detail"]


def test_same_customer_can_keep_its_own_email_on_update(api):
    customer = api["db"].query(Customer).first()
    response = api["client"].patch(
        f"/api/admin/customers/{customer.id}",
        json={"email": "taken@test.com", "phone": "13800000000"},
        headers=api["headers"],
    )
    assert response.status_code == 200, response.text
