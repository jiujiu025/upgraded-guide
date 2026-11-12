"""全局审计查询：只有管理员能看，过滤条件与系统动作的显示要对。

系统动作（支付回调、定时任务、自动采购）没有 actor_id，用 inner join 会把它们
全部过滤掉——排查时最想看的恰恰是这些，所以专门锁一条。
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import hash_password
from app.db import get_db
from app.main import app
from app.models import AuditLog, User
from app.models.base import Base


@pytest.fixture()
def api():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()

    admin = User(username="root", password_hash=hash_password("Admin_123456"), role="admin", display_name="管理员")
    customer_user = User(username="buyer", password_hash=hash_password("Customer_123456"), role="customer", display_name="客户")
    db.add_all([admin, customer_user])
    db.flush()
    db.add_all([
        AuditLog(actor_id=admin.id, action="deliver", resource_type="order", resource_id=7,
                 detail="线路=12; 进度=1/1; 收款状态=paid"),
        AuditLog(actor_id=admin.id, action="runtime_setting", resource_type="runtime_setting",
                 detail="auto_procurement=true"),
        # 系统动作：没有操作人
        AuditLog(actor_id=None, action="wallet_order_payment", resource_type="order", resource_id=7,
                 detail="金额分=10900; 类型=purchase"),
    ])
    db.commit()

    def override_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()
    db.close()
    engine.dispose()


def login(client: TestClient, username: str, password: str) -> dict:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_admin_can_list_and_customer_cannot(api):
    admin_headers = login(api, "root", "Admin_123456")
    response = api.get("/api/admin/audit-logs", headers=admin_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 3
    assert body["facets"]["actions"] == ["deliver", "runtime_setting", "wallet_order_payment"]

    customer_headers = login(api, "buyer", "Customer_123456")
    assert api.get("/api/admin/audit-logs", headers=customer_headers).status_code == 403


def test_system_action_shows_as_system(api):
    admin_headers = login(api, "root", "Admin_123456")
    body = api.get("/api/admin/audit-logs", headers=admin_headers).json()
    payment = next(item for item in body["items"] if item["action"] == "wallet_order_payment")
    # 没有 actor_id 的系统动作不能被 inner join 丢掉，显示为「系统」
    assert payment["actor"] == "系统"
    assert payment["actor_username"] is None


def test_filters_by_action_and_keyword(api):
    admin_headers = login(api, "root", "Admin_123456")

    by_action = api.get("/api/admin/audit-logs", params={"action": "deliver"}, headers=admin_headers).json()
    assert by_action["total"] == 1
    assert by_action["items"][0]["resource_type"] == "order"

    by_keyword = api.get("/api/admin/audit-logs", params={"keyword": "auto_procurement"}, headers=admin_headers).json()
    assert by_keyword["total"] == 1

    by_actor = api.get("/api/admin/audit-logs", params={"actor": "管理"}, headers=admin_headers).json()
    assert by_actor["total"] == 2


def test_summary_counts_recent_actions(api):
    admin_headers = login(api, "root", "Admin_123456")
    body = api.get("/api/admin/audit-logs/summary", headers=admin_headers).json()
    counts = {item["action"]: item["count"] for item in body["items"]}
    assert counts["deliver"] == 1
    assert sum(counts.values()) == 3
