"""线上收款的开启门槛：没验收证据不许开，只有本地验收就限额。

背景：`online_enabled` 原来是写死 False，谁也没法开；现在改成"有验收记录才允许开"。
这组用例盯的是三件容易退化的地方：

1. 没验收时确实开不了（不能因为改个字段就绕过去）；
2. 验收记录绑商户号、密钥、支付方式，任何一个变了就失效、必须重验；
3. 只有 simulated 验收时单笔金额被沙箱上限卡住，避免"验过本地链路"就收大额。
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.security import hash_password
from app.db import get_db
from app.main import app
from app.models import PaymentChannelAcceptance, PaymentChannelConfig, User
from app.models.base import Base
from app.services.payment_acceptance_service import record_acceptance
from app.services.payment_gateway import encrypt_secret

SECRET = "merchant-secret-v1"
NEW_SECRET = "merchant-secret-v2"
ENCRYPTION_KEY = "ffeeddccbbaa99887766554433221100ffeeddccbbaa99887766554433221100"


@pytest.fixture()
def api(monkeypatch):
    fake = Settings(_env_file=None, payment_secret_encryption_key=ENCRYPTION_KEY,
                    frontend_origin="https://console.example.com")
    monkeypatch.setattr("app.services.payment_gateway.get_settings", lambda: fake)
    monkeypatch.setattr("app.services.payment_acceptance_service.get_settings", lambda: fake)

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()
    admin = User(username="root", password_hash=hash_password("Root_123456"), role="super_admin", display_name="超管")
    db.add(admin)
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
    client.session_factory = Session
    response = client.post("/api/auth/login", json={"username": "root", "password": "Root_123456"})
    assert response.status_code == 200, response.text
    client.headers.update({"Authorization": f"Bearer {response.json()['access_token']}"})
    yield client

    app.dependency_overrides.clear()
    db.close()
    engine.dispose()


def configure(client, *, secret=SECRET, types=("alipay", "wxpay"), online_enabled=False):
    return client.put("/api/admin/finance/payment-config", json={
        "merchant_id": "10001", "secret": secret, "channel_types": list(types),
        "min_amount_cents": 100, "max_amount_cents": 1000000, "online_enabled": online_enabled,
    })


def accept(client, *, mode="simulated", merchant_secret=SECRET, types=("alipay", "wxpay")):
    """直接写一条验收记录，模拟脚本跑完的样子。"""
    db = client.session_factory()
    try:
        config = db.get(PaymentChannelConfig, 1)
        config.merchant_id = "10001"
        config.encrypted_secret = encrypt_secret(merchant_secret)
        config.channel_types = list(types)
        db.commit()
        record_acceptance(db, config=config, mode=mode,
                          checks=[{"name": "callback_accepted", "ok": True, "detail": "ok"}],
                          amount_cents=100)
        db.commit()
    finally:
        db.close()


def config_json(client):
    response = client.get("/api/admin/finance/payment-config")
    assert response.status_code == 200, response.text
    return response.json()


def test_cannot_enable_without_acceptance(api):
    assert configure(api).status_code == 200
    response = configure(api, online_enabled=True)
    assert response.status_code == 409
    assert "payment_selftest" in response.json()["detail"]
    assert config_json(api)["online_enabled"] is False


def test_enabling_requires_merchant_and_secret(api):
    response = configure(api, online_enabled=True)
    # 配置还没填全，先报配置缺失，而不是"缺验收"
    assert response.status_code == 409
    assert "商户号" in response.json()["detail"]


def test_can_enable_after_acceptance(api):
    configure(api)
    accept(api)
    response = configure(api, online_enabled=True)
    assert response.status_code == 200, response.text
    body = config_json(api)
    assert body["online_enabled"] is True
    assert body["acceptance"]["accepted"] is True
    assert body["acceptance"]["acceptance_mode"] == "simulated"
    assert body["acceptance"]["sandbox_limited"] is True
    assert body["acceptance"]["online_limit_cents"] == 10000


def test_changing_secret_invalidates_acceptance(api):
    configure(api)
    accept(api)
    assert configure(api, online_enabled=True).status_code == 200

    # 换密钥 → 配置照常保存，但旧验收失效，收款立刻关闭
    response = configure(api, secret=NEW_SECRET, online_enabled=True)
    assert response.status_code == 409
    assert "已保存" in response.json()["detail"]
    body = config_json(api)
    assert body["online_enabled"] is False
    assert body["acceptance"]["accepted"] is False


def test_adding_channel_type_invalidates_acceptance(api):
    configure(api)
    accept(api)
    assert configure(api, online_enabled=True).status_code == 200

    response = configure(api, types=("alipay", "wxpay", "qqpay"), online_enabled=True)
    assert response.status_code == 409


def test_disabling_is_always_allowed(api):
    configure(api)
    accept(api)
    assert configure(api, online_enabled=True).status_code == 200
    response = configure(api, online_enabled=False)
    assert response.status_code == 200
    assert config_json(api)["online_enabled"] is False


def test_live_acceptance_lifts_sandbox_limit(api):
    configure(api)
    accept(api, mode="live")
    assert configure(api, online_enabled=True).status_code == 200
    acceptance = config_json(api)["acceptance"]
    assert acceptance["sandbox_limited"] is False
    assert acceptance["online_limit_cents"] == 1000000


def test_sandbox_limit_caps_online_recharge(api, monkeypatch):
    configure(api)
    accept(api)
    assert configure(api, online_enabled=True).status_code == 200
    monkeypatch.setattr("app.api.finance_routes.create_provider_payment",
                        lambda *args, **kwargs: "https://pay.example.com/selftest")

    db = api.session_factory()
    try:
        db.add(User(username="buyer", password_hash=hash_password("Buyer_123456"),
                    role="customer", display_name="买家"))
        db.commit()
    finally:
        db.close()
    login = api.post("/api/auth/login", json={"username": "buyer", "password": "Buyer_123456"})
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    # 沙箱上限 100 元：200 元必须被拦，100 元可以下单
    over = api.post("/api/wallet/recharges", headers=headers, json={
        "amount_cents": 20000, "payment_type": "alipay", "idempotency_key": "sandbox-over-limit-0001"})
    assert over.status_code == 422
    assert "100.00" in over.json()["detail"]

    ok = api.post("/api/wallet/recharges", headers=headers, json={
        "amount_cents": 10000, "payment_type": "alipay", "idempotency_key": "sandbox-at-limit-0002"})
    assert ok.status_code == 201, ok.text
    assert ok.json()["payment_url"] == "https://pay.example.com/selftest"


def test_acceptance_history_is_recorded(api):
    configure(api)
    accept(api)
    body = config_json(api)
    assert len(body["acceptance_history"]) == 1
    assert body["acceptance_history"][0]["mode"] == "simulated"
    assert api.db.scalars(select(PaymentChannelAcceptance)).all()
