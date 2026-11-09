"""通知渠道分发：站内信照旧，渠道推送是附加动作。

设计约束（docs/通知渠道分发设计.md）：
- 业务事务零改动，分发异步幂等（投递流水唯一约束 + 有限重试）；
- 只补发 24 小时内的通知；
- 失效渠道连续失败会降频，分发自身告警不再分发（防风暴环）；
- 渠道配置加密落库，接口只回掩码。
"""

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.security import hash_password
from app.db import get_db
from app.main import app
from app.models import Notification, NotificationChannel, NotificationChannelBinding, NotificationDelivery, User
from app.models.base import Base, utcnow
from app.services import notification_channel_service as service
from app.services.registration_service import registration_rate_limiter

MASTER_KEY = "11" * 32  # 64 位十六进制 = 32 字节 AES-256 密钥


@pytest.fixture()
def api(monkeypatch):
    registration_rate_limiter.clear()
    # 渠道配置加密依赖 SUPPLIER_SECRET_ENCRYPTION_KEY；测试里以显式 Settings 注入。
    settings = Settings(supplier_secret_encryption_key=MASTER_KEY, _env_file=None)
    monkeypatch.setattr(service, "get_settings", lambda: settings)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()
    admin = User(username="admin", password_hash=hash_password("Admin_123456"), role="super_admin", display_name="管理员")
    sales = User(username="sales_a", password_hash=hash_password("Sales_123456"), role="sales", display_name="销售 A")
    db.add_all([admin, sales])
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
    client.users = {"admin": admin, "sales_a": sales}
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


def make_webhook_channel(api):
    response = api.post("/api/admin/notification-channels", json={
        "channel_type": "wecom_webhook",
        "name": "运维告警群",
        "config": {"webhook_url": "https://example.com/hook/secret-token"},
    }, headers=auth(token(api, "admin", "Admin_123456")))
    assert response.status_code == 201, response.text
    return response.json()


def make_app_channel(api):
    response = api.post("/api/admin/notification-channels", json={
        "channel_type": "wecom_app",
        "name": "企微应用通知",
        "config": {"corpid": "ww123", "agent_id": 1000002, "secret": "app-secret-value"},
    }, headers=auth(token(api, "admin", "Admin_123456")))
    assert response.status_code == 201, response.text
    return response.json()


def add_notification(db, user_id, title="测试通知", content="测试内容", category="order"):
    db.add(Notification(user_id=user_id, title=title, content=content, category=category))
    db.commit()


def test_channel_config_is_encrypted_and_masked(api):
    make_webhook_channel(api)
    api.db.expire_all()
    stored = api.db.scalar(select(NotificationChannel))
    # 密钥落库是密文：明文 webhook 与 secret 都不能出现在库或接口里。
    assert "secret-token" not in stored.config_encrypted
    listing = api.get("/api/admin/notification-channels", headers=auth(token(api, "admin", "Admin_123456")))
    assert listing.status_code == 200
    assert "webhook_url" not in listing.text
    assert "secret-token" not in listing.text
    # 解密可还原（发送路径的必要条件）。
    assert json.loads(service.decrypt_channel_config(stored.config_encrypted))["webhook_url"].endswith("secret-token")


def test_create_channel_validates_type_and_config(api):
    admin_headers = auth(token(api, "admin", "Admin_123456"))
    bad_type = api.post("/api/admin/notification-channels",
                        json={"channel_type": "sms", "name": "x", "config": {}}, headers=admin_headers)
    assert bad_type.status_code == 422
    incomplete = api.post("/api/admin/notification-channels",
                          json={"channel_type": "wecom_app", "name": "x", "config": {"corpid": "ww"}}, headers=admin_headers)
    assert incomplete.status_code == 422
    assert "secret" in incomplete.json()["detail"]


def test_channels_are_super_admin_only(api):
    sales_headers = auth(token(api, "sales_a", "Sales_123456"))
    assert api.get("/api/admin/notification-channels", headers=sales_headers).status_code == 403
    assert api.post("/api/admin/notification-channels",
                    json={"channel_type": "wecom_webhook", "name": "x", "config": {"webhook_url": "https://a.b/c"}},
                    headers=sales_headers).status_code == 403


def test_binding_crud_and_rules(api):
    admin_headers = auth(token(api, "admin", "Admin_123456"))
    webhook = make_webhook_channel(api)
    app_channel = make_app_channel(api)
    # 群机器人不需要绑定。
    rejected = api.post(f"/api/admin/notification-channels/{webhook['id']}/bindings",
                        json={"user_id": api.users["sales_a"].id, "target": "sales_a"}, headers=admin_headers)
    assert rejected.status_code == 422
    # 应用消息渠道：绑定销售。
    created = api.post(f"/api/admin/notification-channels/{app_channel['id']}/bindings",
                       json={"user_id": api.users["sales_a"].id, "target": "sales_userid"}, headers=admin_headers)
    assert created.status_code == 201, created.text
    assert created.json()["username"] == "sales_a"
    # 重复绑定被拒。
    duplicate = api.post(f"/api/admin/notification-channels/{app_channel['id']}/bindings",
                         json={"user_id": api.users["sales_a"].id, "target": "sales_userid"}, headers=admin_headers)
    assert duplicate.status_code == 409
    listing = api.get(f"/api/admin/notification-channels/{app_channel['id']}/bindings", headers=admin_headers)
    assert listing.status_code == 200 and len(listing.json()) == 1
    deleted = api.delete(f"/api/admin/notification-channels/{app_channel['id']}/bindings/{created.json()['id']}",
                         headers=admin_headers)
    assert deleted.status_code == 204


def test_dispatch_delivers_once_and_is_idempotent(api, monkeypatch):
    app_channel = make_app_channel(api)
    api.db.expire_all()
    channel = api.db.get(NotificationChannel, app_channel["id"])
    api.db.add(NotificationChannelBinding(user_id=api.users["sales_a"].id, channel_id=channel.id, target="sales_userid"))
    add_notification(api.db, api.users["sales_a"].id, category="order")
    api.db.expire_all()

    calls = []
    monkeypatch.setattr(service, "send_to_channel",
                        lambda channel_obj, target, title, content: calls.append((target, title)))
    counts = service.dispatch_pending_notifications(api.db, utcnow())
    assert counts["created"] == 1 and counts["delivered"] == 1
    assert calls == [("sales_userid", "测试通知")]

    # 再跑一轮：幂等，不重发。
    counts2 = service.dispatch_pending_notifications(api.db, utcnow())
    assert counts2["created"] == 0 and counts2["delivered"] == 0
    assert len(calls) == 1
    delivery = api.db.scalar(select(NotificationDelivery))
    assert delivery.status == "sent" and delivery.attempts == 1


def test_dispatch_respects_categories_and_window(api, monkeypatch):
    channel_json = api.post("/api/admin/notification-channels", json={
        "channel_type": "wecom_webhook", "name": "只推订单",
        "config": {"webhook_url": "https://example.com/hook/x"},
        "push_categories": "order",
    }, headers=auth(token(api, "admin", "Admin_123456"))).json()
    api.db.expire_all()
    channel = api.db.get(NotificationChannel, channel_json["id"])
    add_notification(api.db, api.users["admin"].id, title="订单通知", category="order")
    add_notification(api.db, api.users["admin"].id, title="客户通知", category="customer")
    add_notification(api.db, api.users["admin"].id, title="陈年通知", category="order")
    # 25 小时前的陈年通知：不补发。
    api.db.expire_all()
    stale = api.db.scalar(select(Notification).where(Notification.title == "陈年通知"))
    from datetime import timedelta
    stale.created_at = utcnow() - timedelta(hours=25)
    api.db.commit()

    calls = []
    monkeypatch.setattr(service, "send_to_channel",
                        lambda channel_obj, target, title, content: calls.append(target))
    counts = service.dispatch_pending_notifications(api.db, utcnow())
    # 群机器人推群本体：只有 category=order 且窗口内的一条。
    assert counts["created"] == 1 and counts["delivered"] == 1
    assert calls == ["group"]


def test_failed_delivery_retries_up_to_limit_then_stops(api, monkeypatch):
    make_webhook_channel(api)
    api.db.expire_all()
    add_notification(api.db, api.users["admin"].id, category="order")
    api.db.expire_all()

    def broken(channel_obj, target, title, content):
        raise service.ChannelError("模拟渠道故障")

    monkeypatch.setattr(service, "send_to_channel", broken)
    for _ in range(3):
        counts = service.dispatch_pending_notifications(api.db, utcnow())
    api.db.expire_all()
    delivery = api.db.scalar(select(NotificationDelivery))
    assert delivery.status == "failed"
    assert delivery.attempts == 3
    assert "模拟渠道故障" in delivery.last_error
    # 第 4 轮不再重试。
    counts = service.dispatch_pending_notifications(api.db, utcnow())
    assert counts["failed"] == 0 and counts["created"] == 0


def test_dispatch_skips_its_own_alerts(api, monkeypatch):
    make_webhook_channel(api)
    api.db.expire_all()
    api.db.add(Notification(user_id=api.users["admin"].id, title="渠道告警",
                             content=f"渠道连续失败 {service.DISPATCH_MARKER}", category="system"))
    add_notification(api.db, api.users["admin"].id, title="普通通知", category="order")
    api.db.expire_all()
    calls = []
    monkeypatch.setattr(service, "send_to_channel",
                        lambda channel_obj, target, title, content: calls.append(title))
    service.dispatch_pending_notifications(api.db, utcnow())
    # 告警通知不进渠道，普通通知正常推。
    assert calls == ["普通通知"]


def test_user_sees_and_toggles_own_bindings(api):
    app_channel = make_app_channel(api)
    admin_headers = auth(token(api, "admin", "Admin_123456"))
    api.post(f"/api/admin/notification-channels/{app_channel['id']}/bindings",
             json={"user_id": api.users["sales_a"].id, "target": "sales_userid"}, headers=admin_headers)
    sales_headers = auth(token(api, "sales_a", "Sales_123456"))
    mine = api.get("/api/notifications/channels/me", headers=sales_headers)
    assert mine.status_code == 200 and len(mine.json()) == 1
    assert mine.json()[0]["channel_name"] == "企微应用通知"
    binding_id = mine.json()[0]["id"]
    toggled = api.post(f"/api/notifications/channels/me/{binding_id}/toggle", headers=sales_headers)
    assert toggled.status_code == 200 and toggled.json()["is_enabled"] is False
    # 停用绑定后分发不再给该用户生成投递。
    add_notification(api.db, api.users["sales_a"].id, category="order")
    api.db.expire_all()
    calls = []
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(service, "send_to_channel", lambda *a, **k: calls.append(a))
    counts = service.dispatch_pending_notifications(api.db, utcnow())
    monkeypatch.undo()
    assert counts["created"] == 0 and not calls
