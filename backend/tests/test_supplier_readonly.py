import logging
from datetime import timedelta
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.supplier_routes import get_global_proxy_adapter
from app.core.config import Settings
from app.core.security import create_access_token, hash_password
from app.db import get_db
from app.main import app
from app.models import (
    AuditLog, CatalogPublication, Customer, Product, ProductSupplyRule, Region, Supplier, SupplierInventorySnapshot, SupplierProduct, SupplierQuote,
    SupplierRegion, SupplierSyncRun, User,
)
from app.models.base import Base, utcnow
from app.services.session_service import create_session
from app.suppliers.base import (
    InventoryItem, QuoteItem, SupplierConfigurationError, SupplierInvalidResponseError,
    SupplierProductItem, SupplierRateLimitError, SupplierRegionItem, SupplierTimeoutError,
    SupplierUnavailableError,
)
from app.suppliers.global_proxy import GlobalProxyReadOnlyAdapter


def mock_adapter(handler, api_key="test-secret-key", **kwargs):
    return GlobalProxyReadOnlyAdapter(
        api_key=api_key,
        transport=httpx.MockTransport(handler),
        sleep=kwargs.pop("sleep", lambda _: None),
        **kwargs,
    )


def response(payload, status=200, headers=None):
    body = {"msg": "ok", "time": "1790491344", **payload}
    return httpx.Response(status, json=body, headers=headers)


def test_global_proxy_documented_queries_parse_successfully():
    def handler(request: httpx.Request):
        assert request.headers["token"] == "test-secret-key"
        if request.url.path.endswith("getProductList"):
            return response({"code": 1, "msg": "ok", "data": [{"id": 2, "name": "独享静态住宅（普通）", "suffix": "srtisp", "native_text": None}]})
        if request.url.path.endswith("countryList"):
            assert request.url.params["product_type_id"] == "2"
            return response({"code": 1, "msg": "ok", "data": {"countrys": [{"id": 11, "name": "美国", "code": "US"}]}})
        if request.url.path.endswith("cityList"):
            assert request.url.params.get("country_id") in (None, "11")
            return response({"code": 1, "msg": "ok", "data": [{"id": 22, "name": "Los Angeles", "code": "Los Angeles", "country_id": 11, "country_code": "US"}]})
        if request.url.path.endswith("getBuyProductSku"):
            return response({"code": 1, "msg": "ok", "data": [{"country_code": "US", "city_name": "Los Angeles", "sku_status": 0}]})
        if request.url.path.endswith("getProductUnitPrice"):
            return response({"code": 1, "msg": "ok", "data": {"2": {"30": {"originUnitPrice": 60, "userUnitPrice": 15, "isCustom": True}}, "customPriceBalance": 100}})
        raise AssertionError(request.url.path)

    adapter = mock_adapter(handler)
    try:
        assert adapter.list_products()[0].external_ref == "2"
        assert adapter.list_countries(2)[0].country_code == "US"
        assert adapter.list_cities(2, "us")[0].city_name == "Los Angeles"
        assert adapter.list_cities(2, country_id="11")[0].external_country_ref == "11"
        assert adapter.get_inventory(2, "us")[0].availability == "available"
        quote = adapter.get_quotes()[0]
        assert quote.unit_cost == Decimal("15")
        assert quote.duration_days == 30
    finally:
        adapter.close()


def test_product_11_quote_contract_and_structure_only_failure():
    def handler(request: httpx.Request):
        assert dict(request.url.params) == {"product_type_id": "11", "country": "US", "bandwidth_num": "100", "bill_timelen": "1"}
        return response({"code": 1, "data": {
            "product_type_id": 11, "userUnitPrice": 2.5, "realPrice": 250,
            "days": 30, "currency": "CNY", "area": "US", "bandwidth_num": 100,
        }})

    adapter = mock_adapter(handler)
    try:
        quote = adapter.get_quotes(product_type_id=11, country="US", bandwidth_num=100, bill_timelen=1)[0]
        assert quote.external_product_ref == "11" and quote.duration_days == 30
    finally:
        adapter.close()

    secret = "never-show-sensitive-value"
    adapter = mock_adapter(lambda _: response({"code": 1, "data": {"unknown": {"password": secret}}}))
    try:
        with pytest.raises(SupplierInvalidResponseError):
            adapter.get_quotes(product_type_id=11, country="US", bandwidth_num=100, bill_timelen=1)
        assert secret not in str(adapter.last_quote_response_shape)
        assert adapter.last_quote_response_shape["fields"]["data"]["fields"]["unknown"]["fields"]["password"] == {"type": "str"}
    finally:
        adapter.close()


def test_supplier_timeout_rate_limit_and_invalid_response():
    attempts = 0

    def timeout_handler(_: httpx.Request):
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("timeout")

    adapter = mock_adapter(timeout_handler)
    with pytest.raises(SupplierTimeoutError):
        adapter.list_products()
    adapter.close()
    assert attempts == 3

    adapter = mock_adapter(lambda _: response({"code": 0, "msg": "too many"}, 429))
    with pytest.raises(SupplierRateLimitError):
        adapter.list_products()
    adapter.close()


def test_supplier_retries_429_and_unavailable_with_bounded_backoff():
    attempts = 0
    delays = []

    def limited_then_success(_: httpx.Request):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return response({"code": 0}, 429, {"Retry-After": "60"})
        return response({"code": 1, "data": [{"id": 2, "name": "静态住宅", "suffix": "srtisp"}]})

    adapter = mock_adapter(limited_then_success, sleep=delays.append, max_retry_delay_seconds=2)
    try:
        assert adapter.list_products()[0].external_ref == "2"
    finally:
        adapter.close()
    assert attempts == 2
    assert delays == [2]

    unavailable_attempts = 0

    def unavailable(_: httpx.Request):
        nonlocal unavailable_attempts
        unavailable_attempts += 1
        return response({"code": 0}, 503)

    adapter = mock_adapter(unavailable, max_attempts=2)
    with pytest.raises(SupplierUnavailableError):
        adapter.list_products()
    adapter.close()
    assert unavailable_attempts == 2


@pytest.mark.parametrize("payload", [
    {"code": True, "msg": "ok", "time": "1", "data": []},
    {"code": 1, "msg": "ok", "data": []},
    {"code": 1, "msg": "ok", "time": "1", "data": [{"id": "2", "name": "错误类型"}]},
])
def test_supplier_rejects_invalid_envelope_and_fields(payload):
    adapter = mock_adapter(lambda _: httpx.Response(200, json=payload), max_attempts=1)
    with pytest.raises(SupplierInvalidResponseError):
        adapter.list_products()
    adapter.close()

    adapter = mock_adapter(lambda _: httpx.Response(200, text="not-json"))
    with pytest.raises(SupplierInvalidResponseError):
        adapter.list_products()
    adapter.close()


def test_missing_key_and_sensitive_response_are_not_logged(caplog):
    adapter = mock_adapter(lambda _: response({"code": 1, "data": []}), api_key=None)
    with pytest.raises(SupplierConfigurationError):
        adapter.list_products()
    adapter.close()

    secret = "test-key-never-log-this"
    sensitive = "upstream-password-never-log"
    adapter = mock_adapter(lambda _: response({"code": 9, "msg": sensitive, "data": {"password": sensitive}}), api_key=secret)
    with caplog.at_level(logging.INFO):
        with pytest.raises(SupplierInvalidResponseError):
            adapter.list_products()
    adapter.close()
    assert secret not in caplog.text
    assert sensitive not in caplog.text


def test_no_stock_status_is_returned_by_documented_inventory_check():
    def handler(request: httpx.Request):
        assert request.method == "POST"
        assert request.url.path.endswith("checkInventory")
        assert b"product_type_id=2" in request.content
        return response({"code": 1, "msg": "ok", "data": {"is_sufficient": False, "segments": []}})

    adapter = mock_adapter(handler)
    try:
        result = adapter.check_inventory(2, 3, "US", 1)
        assert result.is_sufficient is False
    finally:
        adapter.close()


class FakeSupplierAdapter:
    def list_products(self):
        return [SupplierProductItem("2", "独享静态住宅（普通）", "srtisp")]

    def list_countries(self, product_type_id):
        assert product_type_id == 2
        return [SupplierRegionItem("11", "US", "美国")]

    def list_cities(self, product_type_id, country_code=None, country_id=None):
        assert country_id in (None, "11")
        return [SupplierRegionItem("11", "US", "美国", "22", "Los Angeles")]

    def get_inventory(self, product_type_id, country_code):
        return [InventoryItem("US", "Los Angeles", "tight")]

    def check_inventory(self, product_type_id, quantity, country_code, duration_code=None):
        raise AssertionError("此测试未调用库存检测")

    def get_quotes(self, **parameters):
        return [QuoteItem("2", Decimal("15.00"), duration_days=30, currency="CNY", is_custom=True)]


@pytest.fixture()
def supplier_api():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()
    admin = User(username="supplier_admin", password_hash=hash_password("Admin_123456"), role="admin", display_name="供应商管理员")
    customer = User(username="supplier_customer", password_hash=hash_password("Customer_123456"), role="customer", display_name="客户")
    sales = User(username="supplier_sales", password_hash=hash_password("Sales_123456"), role="sales", display_name="销售")
    super_admin = User(username="supplier_super_admin", password_hash=hash_password("Super_123456"), role="super_admin", display_name="超级管理员")
    region = Region(name="美国", country_code="US")
    db.add_all([admin, customer, sales, super_admin, region])
    db.flush()
    product = Product(name="玖玖云美国线路", product_type="直播线路", price=199, billing_days=30, region_id=region.id)
    db.add(product)
    db.commit()

    def override_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_global_proxy_adapter] = lambda: FakeSupplierAdapter()
    client = TestClient(app)
    client.db = db
    admin_session, _ = create_session(db, admin)
    customer_session, _ = create_session(db, customer)
    sales_session, _ = create_session(db, sales)
    super_admin_session, _ = create_session(db, super_admin)
    db.commit()
    client.admin_token = create_access_token(admin.id, admin.role, admin_session.id, admin.auth_version)
    client.customer_token = create_access_token(customer.id, customer.role, customer_session.id, customer.auth_version)
    client.sales_token = create_access_token(sales.id, sales.role, sales_session.id, sales.auth_version)
    client.super_admin_token = create_access_token(super_admin.id, super_admin.role, super_admin_session.id, super_admin.auth_version)
    client.product = product
    client.region = region
    yield client
    app.dependency_overrides.clear()
    db.close()
    engine.dispose()


def auth(value):
    return {"Authorization": f"Bearer {value}"}


def test_admin_sync_persists_snapshots_without_overwriting_storefront(supplier_api):
    headers = auth(supplier_api.admin_token)
    assert supplier_api.post("/api/admin/suppliers/sync/products", headers=headers).status_code == 200
    assert supplier_api.post("/api/admin/suppliers/sync/countries?product_type_id=2", headers=headers).status_code == 200
    assert supplier_api.post("/api/admin/suppliers/sync/cities?product_type_id=2&country_id=11", headers=headers).status_code == 200
    inventory = supplier_api.post("/api/admin/suppliers/sync/inventory?product_type_id=2&country_code=US", headers=headers)
    assert inventory.status_code == 200
    assert inventory.json()["items"][0]["availability"] == "tight"
    assert inventory.json()["items"][0]["available_quantity"] is None
    assert supplier_api.post("/api/admin/suppliers/sync/quotes", json={}, headers=headers).status_code == 200

    supplier_api.db.expire_all()
    assert supplier_api.db.get(Product, supplier_api.product.id).price == 199
    assert supplier_api.db.scalar(select(SupplierProduct)).external_product_ref == "2"
    assert supplier_api.db.scalar(select(SupplierQuote)).unit_cost == Decimal("15.0000")
    assert supplier_api.db.scalar(select(SupplierInventorySnapshot)).availability == "tight"
    public_products = supplier_api.get("/api/public/products").json()
    assert public_products[0]["price"] == 199
    assert "supplier" not in public_products[0]

    supplier_product = supplier_api.db.scalar(select(SupplierProduct))
    supplier_region = supplier_api.db.scalar(select(SupplierRegion).where(SupplierRegion.city_name == "Los Angeles"))
    assert supplier_api.patch(
        f"/api/admin/suppliers/products/{supplier_product.id}/mapping",
        json={"local_product_id": supplier_api.product.id},
        headers=headers,
    ).status_code == 200
    assert supplier_api.patch(
        f"/api/admin/suppliers/regions/{supplier_region.id}/mapping",
        json={"local_region_id": supplier_api.region.id},
        headers=headers,
    ).status_code == 200
    rule_response = supplier_api.post("/api/admin/suppliers/supply-rules", json={
        "product_id": supplier_api.product.id,
        "region_id": supplier_api.region.id,
        "supplier_product_id": supplier_product.id,
        "supplier_region_id": supplier_region.id,
        "is_enabled": True,
    }, headers=headers)
    assert rule_response.status_code == 200
    supplier_api.db.expire_all()
    rule = supplier_api.db.scalar(select(ProductSupplyRule))
    assert rule.mode == "manual"
    assert rule.is_enabled is True


def test_supplier_access_is_admin_only_and_procurement_is_hard_disabled(supplier_api):
    customer_headers = auth(supplier_api.customer_token)
    assert supplier_api.get("/api/admin/suppliers", headers=customer_headers).status_code == 403
    sales_headers = auth(supplier_api.sales_token)
    protected_paths = [
        "/api/admin/suppliers/overview",
        "/api/admin/suppliers/products",
        "/api/admin/suppliers/regions",
        "/api/admin/suppliers/inventory",
        "/api/admin/suppliers/quotes",
        "/api/admin/suppliers/supply-rules",
        "/api/admin/suppliers/sync-runs",
    ]
    for path in protected_paths:
        assert supplier_api.get(path, headers=sales_headers).status_code == 403
        assert supplier_api.get(path, headers=customer_headers).status_code == 403
    write_requests = [
        ("patch", "/api/admin/suppliers/products/999/mapping", {"local_product_id": supplier_api.product.id}),
        ("patch", "/api/admin/suppliers/regions/999/mapping", {"local_region_id": supplier_api.region.id}),
        ("post", "/api/admin/suppliers/supply-rules", {
            "product_id": supplier_api.product.id,
            "region_id": supplier_api.region.id,
            "supplier_product_id": 999,
            "supplier_region_id": 999,
            "is_enabled": True,
        }),
    ]
    for method, path, payload in write_requests:
        for headers in (sales_headers, customer_headers):
            assert getattr(supplier_api, method)(path, json=payload, headers=headers).status_code == 403
    admin_headers = auth(supplier_api.admin_token)
    supplier_response = supplier_api.get("/api/admin/suppliers", headers=admin_headers)
    assert supplier_response.status_code == 200
    assert "secret_ref" not in supplier_response.json()[0]
    assert "api_key" not in supplier_response.json()[0]
    for action in ("procure", "renew", "cancel", "refund"):
        response = supplier_api.post(f"/api/admin/suppliers/{action}", headers=admin_headers)
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "SUPPLIER_PROCUREMENT_DISABLED"
    with pytest.raises(ValidationError, match="supplier_auto_procurement_enabled"):
        Settings(supplier_auto_procurement_enabled=True, _env_file=None)


def test_freshness_out_of_stock_failed_sync_and_audit(supplier_api):
    headers = auth(supplier_api.admin_token)
    assert supplier_api.post("/api/admin/suppliers/sync/products", headers=headers).status_code == 200
    assert supplier_api.post("/api/admin/suppliers/sync/quotes", json={}, headers=headers).status_code == 200
    product = supplier_api.db.scalar(select(SupplierProduct))
    product.last_synced_at = utcnow() - timedelta(days=3)
    quote = supplier_api.db.scalar(select(SupplierQuote))
    quote.quoted_at = utcnow() - timedelta(days=3)
    unavailable = SupplierInventorySnapshot(
        supplier_id=product.supplier_id,
        supplier_product_id=product.id,
        country_code="US",
        city_name=None,
        availability="unavailable",
        available_quantity=None,
        source_endpoint="/externalapi/product_order/checkInventory",
        checked_at=utcnow(),
    )
    supplier_api.db.add(unavailable)
    supplier_api.db.commit()

    assert supplier_api.get("/api/admin/suppliers/products", headers=headers).json()[0]["data_state"] == "stale"
    assert supplier_api.get("/api/admin/suppliers/quotes", headers=headers).json()[0]["data_state"] == "stale"
    inventory = supplier_api.get("/api/admin/suppliers/inventory", headers=headers).json()
    assert inventory[0]["data_state"] == "out_of_stock"
    assert inventory[0]["is_realtime_available"] is False

    app.dependency_overrides[get_global_proxy_adapter] = lambda: GlobalProxyReadOnlyAdapter(api_key=None)
    failed = supplier_api.post("/api/admin/suppliers/sync/products", headers=headers)
    assert failed.status_code == 503
    supplier_api.db.expire_all()
    run = supplier_api.db.scalar(select(SupplierSyncRun).order_by(SupplierSyncRun.id.desc()))
    assert run.status == "failed"
    assert run.error_code == "SUPPLIER_CONFIGURATION_ERROR"
    assert run.error_message == "供应商 API Key 未配置"
    overview = supplier_api.get("/api/admin/suppliers/overview", headers=headers).json()
    assert overview["states"]["products"] == "query_failed"
    assert overview["supplier"]["api_key_configured"] is False
    audits = supplier_api.db.scalars(select(AuditLog).where(AuditLog.resource_type == "supplier_sync_run")).all()
    assert any(item.action == "sync_start" for item in audits)
    assert any(item.action == "sync_finish" for item in audits)


def test_grouped_region_state_does_not_hide_failed_subtask(supplier_api):
    headers = auth(supplier_api.admin_token)
    assert supplier_api.post("/api/admin/suppliers/sync/products", headers=headers).status_code == 200
    assert supplier_api.post("/api/admin/suppliers/sync/countries?product_type_id=2", headers=headers).status_code == 200
    app.dependency_overrides[get_global_proxy_adapter] = lambda: GlobalProxyReadOnlyAdapter(api_key=None)
    assert supplier_api.post("/api/admin/suppliers/sync/countries?product_type_id=2", headers=headers).status_code == 503
    app.dependency_overrides[get_global_proxy_adapter] = lambda: FakeSupplierAdapter()
    assert supplier_api.post("/api/admin/suppliers/sync/cities?product_type_id=2&country_code=US", headers=headers).status_code == 200

    overview = supplier_api.get("/api/admin/suppliers/overview", headers=headers).json()
    assert overview["states"]["regions"] == "query_failed"
    regions = supplier_api.get("/api/admin/suppliers/regions", headers=headers).json()
    country = next(item for item in regions if item["city_name"] is None)
    city = next(item for item in regions if item["city_name"] == "Los Angeles")
    assert country["data_state"] == "query_failed"
    assert city["data_state"] == "fresh"


def test_repeated_sync_keeps_storefront_unchanged_and_records_each_run(supplier_api):
    headers = auth(supplier_api.admin_token)
    for _ in range(2):
        assert supplier_api.post("/api/admin/suppliers/sync/products", headers=headers).status_code == 200
        assert supplier_api.post("/api/admin/suppliers/sync/quotes", json={}, headers=headers).status_code == 200

    supplier_api.db.expire_all()
    assert len(supplier_api.db.scalars(select(SupplierProduct)).all()) == 1
    assert supplier_api.db.get(Product, supplier_api.product.id).price == 199
    assert supplier_api.db.get(Product, supplier_api.product.id).is_in_stock is True
    runs = supplier_api.db.scalars(select(SupplierSyncRun).order_by(SupplierSyncRun.id)).all()
    assert [run.status for run in runs] == ["success", "success", "success", "success"]
    assert len(supplier_api.db.scalars(select(SupplierQuote)).all()) == 2


def test_unavailable_supplier_writes_sanitized_failed_sync_run(supplier_api):
    secret = "unavailable-secret-never-store"
    adapter = mock_adapter(lambda _: response({"code": 0}, 503), api_key=secret, max_attempts=2)
    app.dependency_overrides[get_global_proxy_adapter] = lambda: adapter
    response_data = supplier_api.post(
        "/api/admin/suppliers/sync/products",
        headers=auth(supplier_api.admin_token),
    )
    adapter.close()

    assert response_data.status_code == 503
    assert response_data.json()["detail"] == {
        "code": "SUPPLIER_UNAVAILABLE",
        "message": "供应商服务不可用",
    }
    supplier_api.db.expire_all()
    run = supplier_api.db.scalar(select(SupplierSyncRun).order_by(SupplierSyncRun.id.desc()))
    assert run.status == "failed"
    assert run.error_code == "SUPPLIER_UNAVAILABLE"
    assert run.error_message == "供应商服务不可用"
    assert secret not in run.error_message


def prepare_publication(supplier_api):
    headers = auth(supplier_api.admin_token)
    for path in (
        "/api/admin/suppliers/sync/products",
        "/api/admin/suppliers/sync/countries?product_type_id=2",
        "/api/admin/suppliers/sync/cities?product_type_id=2&country_code=US",
        "/api/admin/suppliers/sync/inventory?product_type_id=2&country_code=US",
    ):
        assert supplier_api.post(path, headers=headers).status_code == 200
    assert supplier_api.post("/api/admin/suppliers/sync/quotes", json={}, headers=headers).status_code == 200
    product = supplier_api.db.scalar(select(SupplierProduct))
    region = supplier_api.db.scalar(select(SupplierRegion).where(SupplierRegion.city_name == "Los Angeles"))
    inventory = supplier_api.db.scalar(select(SupplierInventorySnapshot).where(SupplierInventorySnapshot.supplier_region_id == region.id))
    quote = supplier_api.db.scalar(select(SupplierQuote))
    inventory.availability = "available"
    supplier_api.db.commit()
    assert supplier_api.patch(f"/api/admin/suppliers/products/{product.id}/mapping", json={"local_product_id": supplier_api.product.id}, headers=headers).status_code == 200
    assert supplier_api.patch(f"/api/admin/suppliers/regions/{region.id}/mapping", json={"local_region_id": supplier_api.region.id}, headers=headers).status_code == 200
    return {"product_id": supplier_api.product.id, "region_id": supplier_api.region.id,
            "inventory_snapshot_id": inventory.id, "quote_snapshot_id": quote.id, "price": 229}


def test_manual_publication_audit_duplicate_and_withdraw(supplier_api):
    payload = prepare_publication(supplier_api)
    headers = auth(supplier_api.admin_token)
    published = supplier_api.post("/api/admin/suppliers/publications", json=payload, headers=headers)
    assert published.status_code == 200, published.text
    assert published.json()["version"] == 1
    assert supplier_api.post("/api/admin/suppliers/publications", json=payload, headers=headers).status_code == 409
    public = supplier_api.get("/api/public/products").json()[0]
    assert public["price"] == 229
    assert not any(key in str(public).lower() for key in ("supplier", "cost", "quote", "api_error"))
    publication_id = published.json()["id"]
    assert supplier_api.post(f"/api/admin/suppliers/publications/{publication_id}/withdraw", headers=headers).status_code == 200
    assert supplier_api.get("/api/public/products").json()[0]["price"] == 199
    assert supplier_api.post(f"/api/admin/suppliers/publications/{publication_id}/withdraw", headers=headers).status_code == 409
    logs = supplier_api.db.scalars(select(AuditLog).where(AuditLog.resource_type == "catalog_publication")).all()
    assert [item.action for item in logs] == ["publish", "withdraw"]
    assert all('"before"' in item.detail and '"after"' in item.detail and '"snapshot_version"' in item.detail for item in logs)
    assert supplier_api.db.scalar(select(CatalogPublication)).withdrawn_by is not None


def test_publication_blocks_stale_failed_stock_and_invalid_mapping(supplier_api):
    payload = prepare_publication(supplier_api)
    headers = auth(supplier_api.admin_token)
    inventory = supplier_api.db.get(SupplierInventorySnapshot, payload["inventory_snapshot_id"])
    inventory.availability = "unavailable"
    supplier_api.db.commit()
    assert supplier_api.post("/api/admin/suppliers/publications", json=payload, headers=headers).status_code == 409
    inventory.availability = "available"
    inventory.checked_at = utcnow() - timedelta(days=2)
    supplier_api.db.commit()
    assert supplier_api.post("/api/admin/suppliers/publications", json=payload, headers=headers).status_code == 409
    inventory.checked_at = utcnow()
    supplier_api.db.commit()
    invalid = {**payload, "region_id": 999}
    assert supplier_api.post("/api/admin/suppliers/publications", json=invalid, headers=headers).status_code == 409
    run = supplier_api.db.scalar(select(SupplierSyncRun).where(SupplierSyncRun.sync_type == "inventory"))
    run.status = "failed"
    supplier_api.db.commit()
    assert supplier_api.post("/api/admin/suppliers/publications", json=payload, headers=headers).status_code == 409
    assert supplier_api.get("/api/public/products").json()[0]["price"] == 199


def test_publication_permissions_and_supplier_data_isolation(supplier_api):
    payload = prepare_publication(supplier_api)
    for token in (supplier_api.customer_token, supplier_api.sales_token):
        headers = auth(token)
        assert supplier_api.get("/api/admin/suppliers/publications", headers=headers).status_code == 403
        assert supplier_api.post("/api/admin/suppliers/publications", json=payload, headers=headers).status_code == 403
        assert supplier_api.post("/api/admin/suppliers/publications/1/withdraw", headers=headers).status_code == 403
    assert supplier_api.get("/api/public/products").status_code == 200
    assert supplier_api.get("/api/admin/suppliers/quotes", headers=auth(supplier_api.customer_token)).status_code == 403


def test_withdraw_restores_previous_published_version(supplier_api):
    payload = prepare_publication(supplier_api)
    headers = auth(supplier_api.admin_token)
    first = supplier_api.post("/api/admin/suppliers/publications", json=payload, headers=headers)
    assert first.status_code == 200
    assert supplier_api.post("/api/admin/suppliers/sync/quotes", json={}, headers=headers).status_code == 200
    latest_quote = supplier_api.db.scalar(select(SupplierQuote).order_by(SupplierQuote.id.desc()))
    second = supplier_api.post("/api/admin/suppliers/publications", json={
        **payload, "quote_snapshot_id": latest_quote.id, "price": 249,
    }, headers=headers)
    assert second.status_code == 200, second.text
    assert second.json()["version"] == 2
    assert supplier_api.post(f"/api/admin/suppliers/publications/{first.json()['id']}/withdraw", headers=headers).status_code == 409
    assert supplier_api.post(f"/api/admin/suppliers/publications/{second.json()['id']}/withdraw", headers=headers).status_code == 200
    assert supplier_api.get("/api/public/products").json()[0]["price"] == 229


def test_publication_rejects_stale_and_incomplete_quote(supplier_api):
    payload = prepare_publication(supplier_api)
    headers = auth(supplier_api.admin_token)
    quote = supplier_api.db.get(SupplierQuote, payload["quote_snapshot_id"])
    quote.quoted_at = utcnow() - timedelta(days=2)
    supplier_api.db.commit()
    assert supplier_api.post("/api/admin/suppliers/publications", json=payload, headers=headers).status_code == 409
    quote.quoted_at = utcnow()
    quote.currency = ""
    supplier_api.db.commit()
    assert supplier_api.post("/api/admin/suppliers/publications", json=payload, headers=headers).status_code == 409
    assert supplier_api.db.scalar(select(CatalogPublication)) is None


def test_failed_sync_does_not_unpublish_catalog(supplier_api):
    payload = prepare_publication(supplier_api)
    headers = auth(supplier_api.admin_token)
    published = supplier_api.post("/api/admin/suppliers/publications", json=payload, headers=headers)
    assert published.status_code == 200
    app.dependency_overrides[get_global_proxy_adapter] = lambda: GlobalProxyReadOnlyAdapter(api_key=None)
    assert supplier_api.post("/api/admin/suppliers/sync/products", headers=headers).status_code == 503
    assert supplier_api.get("/api/public/products").json()[0]["price"] == 229
    assert supplier_api.post("/api/admin/suppliers/publications", json={**payload, "price": 239}, headers=headers).status_code == 409


def test_withdraw_restores_original_stock_and_customer_reads_internal_catalog(supplier_api):
    payload = prepare_publication(supplier_api)
    supplier_api.product.is_in_stock = False
    customer_user = supplier_api.db.scalar(select(User).where(User.username == "supplier_customer"))
    supplier_api.db.add(Customer(name="隔离测试客户", email="isolated@example.test", user_id=customer_user.id))
    supplier_api.db.commit()
    headers = auth(supplier_api.admin_token)
    published = supplier_api.post("/api/admin/suppliers/publications", json=payload, headers=headers)
    assert published.status_code == 200, published.text
    public = supplier_api.get("/api/public/products").json()[0]
    assert public["price"] == 229 and public["is_in_stock"] is True
    order = supplier_api.post("/api/customer/orders", json={
        "product_id": supplier_api.product.id, "region_id": supplier_api.region.id,
        "quantity": 1, "billing_days": 30,
    }, headers=auth(supplier_api.customer_token))
    assert order.status_code == 200, order.text
    customer_order = supplier_api.get("/api/customer/orders", headers=auth(supplier_api.customer_token)).json()[0]
    assert customer_order["product_name"] == supplier_api.product.name
    assert customer_order["unit_price"] == 229
    for response_data in (public, order.json(), customer_order):
        assert not any(key in str(response_data).lower() for key in ("supplier", "unit_cost", "total_cost", "external_product_ref", "api_error"))
    withdrawn = supplier_api.post(f"/api/admin/suppliers/publications/{published.json()['id']}/withdraw", headers=headers)
    assert withdrawn.status_code == 200, withdrawn.text
    restored = supplier_api.get("/api/public/products").json()[0]
    assert restored["price"] == 199 and restored["is_in_stock"] is False


def test_supplier_credential_is_encrypted_super_admin_only_and_never_returned(supplier_api, monkeypatch):
    from app.core.config import Settings
    from app.api import supplier_routes
    from app.services import supplier_credentials

    master_key = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"
    settings = Settings(supplier_secret_encryption_key=master_key, _env_file=None)
    monkeypatch.setattr(supplier_credentials, "get_settings", lambda: settings)
    monkeypatch.setattr(supplier_routes, "get_settings", lambda: settings)
    super_headers = auth(supplier_api.super_admin_token)
    supplier_api.get("/api/admin/suppliers/overview", headers=super_headers)
    api_key = "supplier-secret-never-return-or-log"

    denied = supplier_api.put("/api/admin/suppliers/credential", json={"api_key": api_key}, headers=auth(supplier_api.admin_token))
    assert denied.status_code == 403

    saved = supplier_api.put("/api/admin/suppliers/credential", json={"api_key": api_key}, headers=super_headers)
    assert saved.status_code == 200
    assert saved.json() == {"configured": True, "source": "admin"}
    supplier_api.db.expire_all()
    supplier = supplier_api.db.scalar(select(Supplier))
    assert supplier.encrypted_api_key != api_key
    assert supplier_credentials.decrypt_api_key(supplier.encrypted_api_key) == api_key
    assert supplier_credentials.resolve_api_key(supplier_api.db) == api_key

    overview = supplier_api.get("/api/admin/suppliers/overview", headers=super_headers)
    assert overview.status_code == 200
    assert overview.json()["supplier"]["credential_source"] == "admin"
    assert api_key not in overview.text and api_key not in saved.text
    audit = supplier_api.db.scalar(select(AuditLog).where(AuditLog.resource_type == "supplier_credential"))
    assert audit and api_key not in (audit.detail or "")

    oversized_key = "sensitive-input-" + ("X" * 4096)
    invalid = supplier_api.put("/api/admin/suppliers/credential", json={"api_key": oversized_key}, headers=super_headers)
    assert invalid.status_code == 422
    assert oversized_key not in invalid.text

    replacement_key = "replacement-secret-value"
    replaced = supplier_api.put("/api/admin/suppliers/credential", json={"api_key": replacement_key}, headers=super_headers)
    assert replaced.status_code == 200
    supplier_api.db.expire_all()
    supplier = supplier_api.db.scalar(select(Supplier))
    assert supplier_credentials.decrypt_api_key(supplier.encrypted_api_key) == replacement_key

    cleared = supplier_api.delete("/api/admin/suppliers/credential", headers=super_headers)
    assert cleared.status_code == 200
    assert cleared.json() == {"configured": False, "source": "none"}
    supplier_api.db.expire_all()
    assert supplier_api.db.scalar(select(Supplier)).encrypted_api_key is None


def test_supplier_credential_clear_falls_back_to_environment(supplier_api, monkeypatch):
    from app.core.config import Settings
    from app.api import supplier_routes
    from app.services import supplier_credentials

    settings = Settings(
        supplier_secret_encryption_key="00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff",
        global_proxy_api_key="environment-only-secret",
        _env_file=None,
    )
    monkeypatch.setattr(supplier_credentials, "get_settings", lambda: settings)
    monkeypatch.setattr(supplier_routes, "get_settings", lambda: settings)
    headers = auth(supplier_api.super_admin_token)
    supplier_api.get("/api/admin/suppliers/overview", headers=headers)
    saved = supplier_api.put("/api/admin/suppliers/credential", json={"api_key": "admin-secret-value"}, headers=headers)
    assert saved.status_code == 200
    response = supplier_api.delete("/api/admin/suppliers/credential", headers=headers)
    assert response.status_code == 200
    assert response.json() == {"configured": True, "source": "environment"}
    assert supplier_credentials.credential_source(supplier_api.db) == "environment"
    assert supplier_credentials.resolve_api_key(supplier_api.db) == "environment-only-secret"


def test_empty_environment_supplier_key_is_not_configured(supplier_api, monkeypatch):
    from app.core.config import Settings
    from app.api import supplier_routes
    from app.services import supplier_credentials

    settings = Settings(global_proxy_api_key="", _env_file=None)
    monkeypatch.setattr(supplier_credentials, "get_settings", lambda: settings)
    monkeypatch.setattr(supplier_routes, "get_settings", lambda: settings)
    supplier_api.get("/api/admin/suppliers/overview", headers=auth(supplier_api.super_admin_token))
    overview = supplier_api.get("/api/admin/suppliers/overview", headers=auth(supplier_api.super_admin_token))
    assert overview.json()["supplier"]["api_key_configured"] is False
    assert overview.json()["supplier"]["credential_source"] == "none"
