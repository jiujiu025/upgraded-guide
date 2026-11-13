"""客户可选城市码 / IP 段：后台维护、客户查询、下单校验。

对应 docs/城市与IP段选择设计.md。核心断言是两条：
1. 客户只能看到"本商品所在国家 + 本资源类型"下已启用的城市；
2. 下单时选了什么就落什么快照，不可售的选择一律 422，绝不静默替换。
"""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import create_access_token, hash_password
from app.db import get_db
from app.main import app
from app.models import Customer, Order, Product, Region, RetailPriceRule, User
from app.models.base import Base
from app.services.city_option_service import normalize_segments
from app.services.session_service import create_session


@pytest.fixture()
def api():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()

    us = Region(name="美国", country_code="US")
    jp = Region(name="日本", country_code="JP")
    db.add_all([us, jp])
    db.flush()

    us_product = Product(name="美国 静态住宅IP 30天", product_type="静态住宅IP", price=60, billing_days=30,
                         region_id=us.id, resource_category="static_residential", is_active=True, is_in_stock=True)
    jp_product = Product(name="日本 静态住宅IP 30天", product_type="静态住宅IP", price=85, billing_days=30,
                         region_id=jp.id, resource_category="static_residential", is_active=True, is_in_stock=True)
    db.add_all([us_product, jp_product])
    # 定价服务按「分类 + 国家」的月价派生，缺价会直接拒绝下单，和城市校验无关。
    for code in ("US", "JP"):
        db.add(RetailPriceRule(resource_category="static_residential", country_code=code,
                               billing_days=30, unit_price=Decimal("60"), source_version="test"))

    admin = User(username="city_admin", password_hash=hash_password("Admin_123456"),
                 role="super_admin", display_name="管理员")
    sales = User(username="city_sales", password_hash=hash_password("Sales_123456"),
                 role="sales", display_name="销售")
    customer_user = User(username="city_customer", password_hash=hash_password("Customer_123456"),
                         role="customer", display_name="客户")
    db.add_all([admin, sales, customer_user])
    db.flush()
    customer = Customer(name="客户一", email="city@test.com", user_id=customer_user.id, sales_id=sales.id)
    db.add(customer)
    db.commit()

    sessions = {}
    for key, user in (("admin", admin), ("sales", sales), ("customer", customer_user)):
        session, _ = create_session(db, user)
        sessions[key] = {"Authorization": f"Bearer {create_access_token(user.id, user.role, session.id, user.auth_version)}"}
    db.commit()

    def override_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    state = {
        "client": TestClient(app), "db": db, "headers": sessions,
        "products": {"US": us_product.id, "JP": jp_product.id}, "customer_id": customer.id,
    }
    yield state
    app.dependency_overrides.clear()
    db.close()
    engine.dispose()


def create_city(api, country_code="US", city_code="LAX", city_name="洛杉矶",
                segments="", category="static_residential"):
    return api["client"].post("/api/admin/catalog-management/cities", headers=api["headers"]["admin"], json={
        "resource_category": category,
        "country_code": country_code,
        "city_code": city_code,
        "city_name": city_name,
        "ip_segments": segments,
    })


def city_options(api, product_key="US", headers=None):
    response = api["client"].get("/api/catalog/cities", params={"product_id": api["products"][product_key]},
                                 headers=headers or api["headers"]["customer"])
    assert response.status_code == 200, response.text
    return response.json()


def order_on_behalf(api, **overrides):
    payload = {
        "customer_id": api["customer_id"],
        "product_id": api["products"]["US"],
        "quantity": 1,
        "billing_days": 30,
        "payment_method": "contact_manager",
        "idempotency_key": "00000000-0000-4000-8000-0000000000%02d" % (order_on_behalf.seq,),
    }
    order_on_behalf.seq += 1
    payload.update(overrides)
    return api["client"].post("/api/orders/on-behalf", headers=api["headers"]["sales"], json=payload)


order_on_behalf.seq = 1


def test_segment_text_is_normalized_and_deduplicated():
    assert normalize_segments("1.1.1, 1.1.2，1.1.1  1.1.3") == ["1.1.1", "1.1.2", "1.1.3"]
    assert normalize_segments("") == []
    with pytest.raises(ValueError):
        normalize_segments("not a segment!")


def test_city_options_only_list_enabled_cities_of_the_same_country(api):
    assert create_city(api, city_code="LAX", city_name="洛杉矶", segments="1.1.1,1.1.2").status_code == 201
    assert create_city(api, city_code="NYC", city_name="纽约").status_code == 201
    assert create_city(api, country_code="JP", city_code="TYO", city_name="东京").status_code == 201

    us = city_options(api, "US")
    assert us["supported"] is True
    assert [item["city_code"] for item in us["items"]] == ["LAX", "NYC"]
    assert us["items"][0]["segments"] == ["1.1.1", "1.1.2"]
    assert city_options(api, "JP")["items"][0]["city_name"] == "东京"

    listed = api["client"].get("/api/admin/catalog-management/cities", headers=api["headers"]["admin"]).json()
    nyc = next(item for item in listed["items"] if item["city_code"] == "NYC")
    assert api["client"].patch(f"/api/admin/catalog-management/cities/{nyc['id']}",
                               headers=api["headers"]["admin"], json={"is_enabled": False}).status_code == 200
    assert [item["city_code"] for item in city_options(api, "US")["items"]] == ["LAX"]


def test_order_snapshots_the_chosen_city_and_segment(api):
    create_city(api, city_code="LAX", city_name="洛杉矶", segments="1.1.1,1.1.2")

    response = order_on_behalf(api, city_code="LAX", ip_segment="1.1.2")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["requested_city_code"] == "LAX"
    assert body["requested_city_name"] == "洛杉矶"
    assert body["requested_ip_segment"] == "1.1.2"

    stored = api["db"].get(Order, body["id"])
    assert (stored.requested_city_code, stored.requested_ip_segment) == ("LAX", "1.1.2")


def test_segment_alone_resolves_its_city(api):
    create_city(api, city_code="LAX", city_name="洛杉矶", segments="1.1.1")
    create_city(api, city_code="NYC", city_name="纽约", segments="2.2.2")

    response = order_on_behalf(api, ip_segment="2.2.2")
    assert response.status_code == 200, response.text
    assert response.json()["requested_city_code"] == "NYC"


def test_unsellable_city_or_segment_is_rejected(api):
    create_city(api, city_code="LAX", city_name="洛杉矶", segments="1.1.1")

    unknown = order_on_behalf(api, city_code="XXX")
    assert unknown.status_code == 422
    assert "暂不可售" in unknown.json()["detail"]

    wrong_segment = order_on_behalf(api, city_code="LAX", ip_segment="9.9.9")
    assert wrong_segment.status_code == 422
    assert "不属于城市" in wrong_segment.json()["detail"]

    assert order_on_behalf(api, ip_segment="9.9.9").status_code == 422

    # 城市被停用后，历史选择不再可下单
    listed = api["client"].get("/api/admin/catalog-management/cities", headers=api["headers"]["admin"]).json()
    city_id = listed["items"][0]["id"]
    api["client"].patch(f"/api/admin/catalog-management/cities/{city_id}",
                        headers=api["headers"]["admin"], json={"is_enabled": False})
    assert order_on_behalf(api, city_code="LAX").status_code == 422


def test_order_without_city_keeps_auto_assignment(api):
    create_city(api, city_code="LAX", city_name="洛杉矶")
    response = order_on_behalf(api)
    assert response.status_code == 200, response.text
    assert response.json()["requested_city_code"] is None
    assert response.json()["requested_ip_segment"] is None


def test_city_maintenance_requires_admin_and_is_unique(api):
    assert create_city(api).status_code == 201
    duplicate = create_city(api)
    assert duplicate.status_code == 409

    forbidden = api["client"].post("/api/admin/catalog-management/cities", headers=api["headers"]["customer"], json={
        "resource_category": "static_residential", "country_code": "US",
        "city_code": "BOS", "city_name": "波士顿",
    })
    assert forbidden.status_code == 403

    listed = api["client"].get("/api/admin/catalog-management/cities", headers=api["headers"]["admin"]).json()
    assert listed["total"] == 1
    city_id = listed["items"][0]["id"]
    assert api["client"].delete(f"/api/admin/catalog-management/cities/{city_id}",
                                headers=api["headers"]["admin"]).status_code == 204
    assert api["client"].get("/api/admin/catalog-management/cities",
                            headers=api["headers"]["admin"]).json()["total"] == 0


def test_city_options_require_login(api):
    assert api["client"].get("/api/catalog/cities", params={"product_id": api["products"]["US"]}).status_code == 401


def _add_social_product(api, country_code="US"):
    region = api["db"].scalar(select(Region).where(Region.country_code == country_code))
    product = Product(name=f"{country_code} 社媒运营专线 30天", product_type="社媒运营专线", price=95,
                      billing_days=30, region_id=region.id, resource_category="social_media",
                      is_active=True, is_in_stock=True)
    api["db"].add(product)
    api["db"].add(RetailPriceRule(resource_category="social_media", country_code=country_code,
                                  billing_days=30, unit_price=Decimal("95"), source_version="test"))
    api["db"].commit()
    return product.id


def test_social_line_reuses_residential_cities_without_segments(api):
    """社媒没有自己的城市清单时，借住宅的位置，但不给号段。"""
    create_city(api, "US", "LAX", "洛杉矶", "149.52")
    social_id = _add_social_product(api)
    body = api["client"].get("/api/catalog/cities", params={"product_id": social_id},
                             headers=api["headers"]["customer"]).json()
    assert body["shared"] is True
    assert [item["city_name"] for item in body["items"]] == ["洛杉矶"]
    assert body["items"][0]["segments"] == []


def test_social_line_can_pick_city(api):
    create_city(api, "US", "LAX", "洛杉矶", "149.52")
    social_id = _add_social_product(api)
    response = order_on_behalf(api, product_id=social_id, city_code="LAX")
    assert response.status_code == 200, response.text
    assert response.json()["requested_city_code"] == "LAX"


def test_social_line_rejects_ip_segment(api):
    """住宅号段跟自有线路落地无关，选了必须拒绝而不是静默忽略。"""
    create_city(api, "US", "LAX", "洛杉矶", "149.52")
    social_id = _add_social_product(api)
    response = order_on_behalf(api, product_id=social_id, city_code="LAX", ip_segment="149.52")
    assert response.status_code == 422
    assert "自有线路" in response.text
