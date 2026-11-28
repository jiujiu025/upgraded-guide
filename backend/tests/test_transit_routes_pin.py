"""国家两级路由：先按区域组，再可选点名具体面板。

业务现状（2026-11-28 玖夜）：
- 东南亚（VN/TH/SG/PH/ID/MY/IN…）走 HK；
- 美国、欧洲走 US。

锁住的关键行为：
1. 不点名时组内按负载挑（现有行为不变）；
2. 点名后只用那一台，不偷偷换机——跑错片区比失败更难查；
3. 点名的机器不属于该组时拒绝保存（否则等于绕过区域映射）。
"""
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import hash_password
from app.db import get_db
from app.main import app
from app.models import Region, TransitRoute, TransitVps, User
from app.models.base import Base
from app.services.transit_service import pick_vps, route_for_country


@pytest.fixture()
def api():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()

    admin = User(username="root", password_hash=hash_password("Admin_123456"),
                 role="admin", display_name="管理员")
    db.add(admin)
    hk1 = TransitVps(name="HK-01", group_code="hk", base_url="https://hk1/x", connect_host="1.1.1.1",
                     port_start=52000, port_end=52999, max_inbounds=200, is_enabled=True)
    hk2 = TransitVps(name="HK-02", group_code="hk", base_url="https://hk2/x", connect_host="2.2.2.2",
                     port_start=52000, port_end=52999, max_inbounds=200, is_enabled=True)
    us1 = TransitVps(name="US-01", group_code="us", base_url="https://us1/x", connect_host="3.3.3.3",
                     port_start=53000, port_end=53999, max_inbounds=200, is_enabled=True)
    db.add_all([hk1, hk2, us1])
    db.commit()

    def override_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    yield {"client": client, "db": db, "admin": admin, "hk1": hk1, "hk2": hk2, "us1": us1}
    app.dependency_overrides.clear()
    db.close()
    engine.dispose()


def login(client) -> dict:
    response = client.post("/api/auth/login", json={"username": "root", "password": "Admin_123456"})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_vietnam_goes_hongkong_by_default(api):
    db = api["db"]
    route = route_for_country(db, "VN")
    assert route is not None
    assert route.group_code == "hk"
    assert route_for_country(db, "US").group_code == "us"


def test_group_picks_any_enabled_machine(api):
    db = api["db"]
    picked = pick_vps(db, "hk")
    assert picked.group_code == "hk"


def test_pinned_panel_is_respected(api):
    db = api["db"]
    route = route_for_country(db, "VN")
    route.pinned_vps_id = api["hk2"].id
    db.commit()

    picked = pick_vps(db, "hk", route.pinned_vps_id)
    assert picked.name == "HK-02"

    # 钉住的机器停用后不换机，而是返回空 → 由调用方停待配中转并说明原因
    api["hk2"].is_enabled = False
    db.commit()
    assert pick_vps(db, "hk", route.pinned_vps_id) is None
    # 没钉住时同组还有 HK-01 可用
    assert pick_vps(db, "hk").name == "HK-01"


def test_api_can_pin_and_list_panel(api):
    client = api["client"]
    headers = login(client)

    response = client.put("/api/admin/transit/routes/VN", json={
        "group_code": "hk", "pinned_vps_id": api["hk1"].id, "is_enabled": True,
        "note": "东南亚固定 HK-01",
    }, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["pinned_vps_name"] == "HK-01"

    rows = {item["country_code"]: item for item in
            client.get("/api/admin/transit/routes", headers=headers).json()}
    assert rows["VN"]["pinned_vps_id"] == api["hk1"].id
    assert rows["VN"]["pinned_vps_name"] == "HK-01"
    assert rows["US"]["pinned_vps_id"] is None


def test_cross_group_pin_is_rejected(api):
    client = api["client"]
    headers = login(client)
    # 把东南亚（hk 组）钉到美国机器上，等于绕过区域映射
    response = client.put("/api/admin/transit/routes/TH", json={
        "group_code": "hk", "pinned_vps_id": api["us1"].id,
    }, headers=headers)
    assert response.status_code == 422
    assert "不一致" in response.json()["detail"]


def test_switching_group_clears_pin(api):
    """换组时前端会清掉点名；后端也应能接受不带 pinned 的请求。"""
    client = api["client"]
    headers = login(client)
    client.put("/api/admin/transit/routes/VN", json={
        "group_code": "hk", "pinned_vps_id": api["hk1"].id,
    }, headers=headers)

    response = client.put("/api/admin/transit/routes/VN", json={
        "group_code": "us", "pinned_vps_id": None,
    }, headers=headers)
    assert response.status_code == 200
    assert response.json()["pinned_vps_id"] is None
