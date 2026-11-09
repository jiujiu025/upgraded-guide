"""M0 收尾：密码 72 字节上限 + 登录限流双键。

背景（docs/功能缺陷与Bug清单.md P2-3.1/3.2/3.3）：
- bcrypt 静默截断 72 字节，超长密码实际只生效前 72 字节；
- 登录限流只按 IP 10 次/分钟，办公室共用 NAT 出口时集中登录必撞 429。
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import hash_password
from app.db import get_db
from app.main import app
from app.models import Customer, User
from app.models.base import Base
from app.services.registration_service import registration_rate_limiter


LONG_PASSWORD = "Aa1!" + "x" * 96  # 100 字符 = 100 字节，满足复杂度但超 72 字节
MULTIBYTE_PASSWORD = "Aa1!" + "字" * 25  # 4 + 75 = 79 字节


@pytest.fixture()
def api():
    registration_rate_limiter.clear()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()
    admin = User(username="admin", password_hash=hash_password("Admin_123456"), role="admin", display_name="管理员")
    customer_user = User(username="customer_a", password_hash=hash_password("Customer_123456"), role="customer", display_name="客户 A")
    db.add_all([admin, customer_user])
    db.flush()
    customer = Customer(name="客户 A", email="a@example.com", user_id=customer_user.id)
    db.add(customer)
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
    client.users = {u.username: u for u in [admin, customer_user]}
    yield client
    registration_rate_limiter.clear()
    app.dependency_overrides.clear()
    db.close()
    engine.dispose()


def token(client, username, password):
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def register_payload(username, password):
    return {"username": username, "password": password, "name": "测试用户", "email": f"{username}@example.com"}


def test_register_rejects_password_over_72_bytes(api):
    response = api.post("/api/public/register", json=register_payload("long_pwd", LONG_PASSWORD))
    assert response.status_code == 422, response.text
    assert api.db.scalar(select(User.id).where(User.username == "long_pwd")) is None


def test_register_rejects_multibyte_password_over_72_bytes(api):
    # 24 个汉字就 72 字节：中文密码最容易踩到静默截断。
    response = api.post("/api/public/register", json=register_payload("cn_pwd", MULTIBYTE_PASSWORD))
    assert response.status_code == 422, response.text
    assert "72" in str(response.json())


def test_change_password_rejects_over_72_bytes_and_keeps_old_password(api):
    customer_token = token(api, "customer_a", "Customer_123456")
    response = api.post("/api/auth/change-password",
                        json={"current_password": "Customer_123456", "new_password": LONG_PASSWORD},
                        headers={"Authorization": f"Bearer {customer_token}"})
    assert response.status_code == 422, response.text
    assert "72" in str(response.json())
    # 旧密码仍然可用：失败请求不能产生半截副作用。
    assert api.post("/api/auth/login", json={"username": "customer_a", "password": "Customer_123456"}).status_code == 200


def test_admin_reset_password_rejects_over_72_bytes(api):
    admin_token = token(api, "admin", "Admin_123456")
    target_id = api.users["customer_a"].id
    response = api.post(f"/api/admin/users/{target_id}/reset-password",
                        json={"new_password": LONG_PASSWORD, "confirm": True},
                        headers={"Authorization": f"Bearer {admin_token}"})
    assert response.status_code == 422, response.text
    assert "72" in str(response.json())
    assert api.post("/api/auth/login", json={"username": "customer_a", "password": "Customer_123456"}).status_code == 200


def test_login_rate_limit_isolated_per_account(api):
    """同一 IP 下，A 账号撞限不影响 B 账号登录（办公室共用 NAT 场景）。"""
    for _ in range(10):
        response = api.post("/api/auth/login", json={"username": "brute_target", "password": "Wrong_12345"})
        assert response.status_code == 401, response.text
    blocked = api.post("/api/auth/login", json={"username": "brute_target", "password": "Wrong_12345"})
    assert blocked.status_code == 429
    # 换一个账号，同 IP：不受 brute_target 的额度影响。
    other = api.post("/api/auth/login", json={"username": "customer_a", "password": "Customer_123456"})
    assert other.status_code == 200, other.text


def test_login_rate_limit_ip_total_blocks_username_rotation(api):
    """换用户名轰炸：单个账号额度没用完，IP 总量（默认 30）也会拦。"""
    for index in range(30):
        response = api.post("/api/auth/login", json={"username": f"rotating_{index}", "password": "Wrong_12345"})
        assert response.status_code == 401, response.text
    blocked = api.post("/api/auth/login", json={"username": "rotating_fresh", "password": "Wrong_12345"})
    assert blocked.status_code == 429
