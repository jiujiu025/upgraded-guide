"""模拟登录：只有 super_admin 能发起，10 分钟失效，审计记在管理员头上。

这些用例锁的是"出事后能不能追责"和"会不会把客户锁在门外"，不是接口能不能通：
- 模拟态的身份是 target，但审计 actor 必须是管理员；
- 模拟态不能改被模拟者的密码/密保；
- 过期、强制结束、发起人被停用，三种情况都必须立刻失效；
- 两个标签同时模拟两个人，各自 token 互不影响。
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
from app.models import AuditLog, Customer, ImpersonationSession, User
from app.models.base import Base, utcnow
from app.services.audit_service import write_audit
from app.services.impersonation_service import ImpersonationContext


@pytest.fixture()
def api():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()

    root = User(username="root", password_hash=hash_password("Admin_123456"), role="super_admin", display_name="超级管理员")
    admin = User(username="ops", password_hash=hash_password("Admin_123456"), role="admin", display_name="普通管理员")
    sales = User(username="seller", password_hash=hash_password("Sales_123456"), role="sales", display_name="销售甲")
    buyer = User(username="buyer", password_hash=hash_password("Customer_123456"), role="customer", display_name="客户乙")
    frozen = User(username="frozen", password_hash=hash_password("Sales_123456"), role="sales",
                  display_name="已停用销售", is_active=False)
    orphan = User(username="orphan", password_hash=hash_password("Customer_123456"), role="customer",
                  display_name="没档案的客户")
    db.add_all([root, admin, sales, buyer, frozen, orphan])
    db.flush()
    db.add(Customer(name="深圳乙公司", email="b@test.com", user_id=buyer.id, sales_id=sales.id))
    db.commit()

    def override_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    yield {"client": client, "db": db, "root": root, "admin": admin, "sales": sales,
           "buyer": buyer, "frozen": frozen, "orphan": orphan}
    app.dependency_overrides.clear()
    db.close()
    engine.dispose()


def login(client, username, password) -> dict:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def start(client, headers, user_id):
    return client.post("/api/admin/impersonation", json={"user_id": user_id}, headers=headers)


def test_super_admin_can_impersonate_sales(api):
    client = api["client"]
    headers = login(client, "root", "Admin_123456")
    response = start(client, headers, api["sales"].id)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["token"].startswith("imp_")
    assert body["user"]["role"] == "sales"

    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {body['token']}"}).json()
    assert me["id"] == api["sales"].id
    assert me["role"] == "sales"
    assert me["impersonation"]["actor_name"] == "超级管理员"
    assert me["impersonation"]["target_name"] == "销售甲"


def test_only_super_admin_can_start(api):
    client = api["client"]
    for username, password in (("ops", "Admin_123456"), ("seller", "Sales_123456")):
        headers = login(client, username, password)
        response = start(client, headers, api["buyer"].id)
        assert response.status_code == 403, f"{username} 不应能模拟"


def test_cannot_impersonate_admin_or_disabled(api):
    client = api["client"]
    headers = login(client, "root", "Admin_123456")
    assert start(client, headers, api["admin"].id).status_code == 400
    assert start(client, headers, api["root"].id).status_code == 400
    assert start(client, headers, api["frozen"].id).status_code == 400
    # 客户账号没有绑定客户档案：模拟进去是空页面，提前挡住
    assert start(client, headers, api["orphan"].id).status_code == 422


def test_blocked_paths_reject_password_change(api):
    client = api["client"]
    headers = login(client, "root", "Admin_123456")
    token = start(client, headers, api["buyer"].id).json()["token"]
    imp_headers = {"Authorization": f"Bearer {token}"}

    response = client.post("/api/auth/change-password",
                           json={"old_password": "Customer_123456", "new_password": "New_12345678"},
                           headers=imp_headers)
    assert response.status_code == 403
    assert "模拟" in response.json()["detail"]


def test_audit_blames_the_actor_not_the_target(api):
    db = api["db"]
    db.info["impersonation"] = ImpersonationContext(
        record_id=1, actor_id=api["root"].id, actor_name="超级管理员",
        target_id=api["buyer"].id, target_name="客户乙",
    )
    write_audit(db, api["buyer"], "create", "order", 9, "下单")
    db.commit()

    log = db.scalar(select(AuditLog).order_by(AuditLog.id.desc()))
    assert log.actor_id == api["root"].id
    assert "[模拟 客户乙]" in log.detail


def test_two_tabs_two_identities(api):
    client = api["client"]
    headers = login(client, "root", "Admin_123456")
    token_sales = start(client, headers, api["sales"].id).json()["token"]
    token_buyer = start(client, headers, api["buyer"].id).json()["token"]
    assert token_sales != token_buyer

    a = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token_sales}"}).json()
    b = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token_buyer}"}).json()
    assert a["role"] == "sales" and b["role"] == "customer"
    # 管理员自己的会话不受影响
    me = client.get("/api/auth/me", headers=headers).json()
    assert me["role"] == "super_admin"
    assert "impersonation" not in me


def test_impersonation_has_no_fixed_ttl(api):
    """不再预设 10 分钟：只要没被结束，多久都还能用。

    排查客户问题经常看半天，10 分钟一到被踢出去要重新进，反而更容易忘记自己在模拟态里。
    现在的兜底是"发起人被停用/降权""被模拟账号被停用""主动结束"。
    """
    import json
    from datetime import timedelta

    client = api["client"]
    db = api["db"]
    headers = login(client, "root", "Admin_123456")
    response = start(client, headers, api["sales"].id)
    assert response.status_code == 200
    body = response.json()
    assert body["ttl_minutes"] is None
    assert body["expires_at"] is None

    token = body["token"]
    record = db.scalar(select(ImpersonationSession).order_by(ImpersonationSession.id.desc()))
    # 两小时后依然有效
    record.expires_at = utcnow() + timedelta(hours=2)
    db.commit()
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200

    listing = client.get("/api/admin/impersonation", headers=headers).json()
    item = next(row for row in listing if row["id"] == record.id)
    assert item["active"] is True
    assert item["remaining_seconds"] == -1     # -1 = 由管理员手动结束


def test_over_max_hours_still_expires(api):
    """兜底：万一管理员忘了结束，超过 24 小时自动失效，不会永久留着后门。"""
    client = api["client"]
    db = api["db"]
    headers = login(client, "root", "Admin_123456")
    token = start(client, headers, api["sales"].id).json()["token"]

    record = db.scalar(select(ImpersonationSession).order_by(ImpersonationSession.id.desc()))
    record.expires_at = utcnow() - timedelta(seconds=1)
    db.commit()

    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_force_end_kills_the_tab(api):
    client = api["client"]
    db = api["db"]
    headers = login(client, "root", "Admin_123456")
    token = start(client, headers, api["sales"].id).json()["token"]
    record = db.scalar(select(ImpersonationSession).order_by(ImpersonationSession.id.desc()))

    response = client.delete(f"/api/admin/impersonation/{record.id}", headers=headers)
    assert response.status_code == 200, response.text

    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_cannot_chain_impersonation(api):
    client = api["client"]
    headers = login(client, "root", "Admin_123456")
    token = start(client, headers, api["sales"].id).json()["token"]

    response = client.post("/api/admin/impersonation", json={"user_id": api["buyer"].id},
                           headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403


def test_at_most_three_active_sessions(api):
    client = api["client"]
    headers = login(client, "root", "Admin_123456")
    buyer_user_id = api["buyer"].id
    assert start(client, headers, api["sales"].id).status_code == 200
    assert start(client, headers, buyer_user_id).status_code == 200
    assert start(client, headers, api["sales"].id).status_code == 200
    # 第四个要被挡住，避免误点开一堆标签
    assert start(client, headers, api["buyer"].id).status_code == 400


def test_disabling_actor_kills_impersonation(api):
    client = api["client"]
    db = api["db"]
    headers = login(client, "root", "Admin_123456")
    token = start(client, headers, api["sales"].id).json()["token"]

    root = db.get(User, api["root"].id)
    root.role = "admin"
    db.commit()

    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_impersonation_cannot_logout_admin_session(api):
    """模拟态点退出不能走 /auth/logout：refresh cookie 是浏览器共享的，
    走这里退掉的是管理员自己的后台会话。"""
    client = api["client"]
    headers = login(client, "root", "Admin_123456")
    token = start(client, headers, api["sales"].id).json()["token"]

    response = client.post("/api/auth/logout", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 400
    assert "退出模拟" in response.json()["detail"]
    # 管理员自己的会话必须还在
    assert client.get("/api/auth/me", headers=headers).status_code == 200
