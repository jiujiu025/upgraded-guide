"""在内存数据库中重测城市、库存和产品 11 报价。"""

import json
import os
from datetime import timedelta

os.environ["DATABASE_URL"] = "sqlite://"
os.environ["ENVIRONMENT"] = "test"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.supplier_routes import get_global_proxy_adapter
from app.core.config import get_settings
from app.core.security import create_access_token, hash_password
from app.db import get_db
from app.main import app
from app.models import Product, Region, SupplierInventorySnapshot, SupplierProduct, SupplierQuote, SupplierRegion, SupplierSyncRun, User
from app.models.base import Base, utcnow
from app.services.supplier_service import ensure_pool_one
from app.services.session_service import create_session
from app.suppliers.global_proxy import GlobalProxyReadOnlyAdapter


def main() -> int:
    settings = get_settings()
    if not settings.global_proxy_api_key or not settings.global_proxy_api_key.get_secret_value():
        print("configured_key_available=False; live_calls=0")
        return 2
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    db = session_factory()
    admin = User(username="readonly_check", password_hash=hash_password("isolated-only"),
                 role="admin", display_name="隔离联调")
    region = Region(name="隔离内部地区", country_code="ZZ")
    db.add_all([admin, region])
    db.flush()
    product = Product(name="隔离内部商品", product_type="测试", price=199, billing_days=30,
                      region_id=region.id, is_in_stock=False)
    db.add(product)
    supplier, _ = ensure_pool_one(db)
    db.add(SupplierProduct(
        supplier_id=supplier.id, external_product_ref="2", name_snapshot="本地产品 2 引用",
        suffix=None, currency="CNY", is_available=True, source_endpoint="local_test_fixture",
        last_synced_at=utcnow() - timedelta(days=2),
    ))
    session, _ = create_session(db, admin)
    db.commit()

    def isolated_db():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    adapter = GlobalProxyReadOnlyAdapter(
        api_key=settings.global_proxy_api_key.get_secret_value(),
        base_url=settings.global_proxy_base_url,
        timeout_seconds=settings.supplier_http_timeout_seconds,
        max_attempts=settings.supplier_read_retry_attempts,
        backoff_base_seconds=settings.supplier_retry_backoff_seconds,
        max_retry_delay_seconds=settings.supplier_retry_max_delay_seconds,
    )
    app.dependency_overrides[get_db] = isolated_db
    app.dependency_overrides[get_global_proxy_adapter] = lambda: adapter
    client = TestClient(app, raise_server_exceptions=False)
    headers = {"Authorization": f"Bearer {create_access_token(admin.id, admin.role, session.id, admin.auth_version)}"}

    def sync(name: str, path: str, payload: dict | None = None) -> tuple[int, SupplierSyncRun]:
        response = client.post(path, headers=headers, json=payload) if payload is not None else client.post(path, headers=headers)
        db.expire_all()
        run = db.scalar(select(SupplierSyncRun).order_by(SupplierSyncRun.id.desc()))
        print(f"{name}: http={response.status_code} status={run.status} code={run.error_code or '-'} count={run.items_changed}")
        return response.status_code, run

    try:
        print("database=memory; product_reference=local_fixture; live_product_calls=0")
        country_http, _ = sync("countries_lookup", "/api/admin/suppliers/sync/countries?product_type_id=2")
        candidates = []
        if country_http == 200:
            candidates = db.scalars(select(SupplierRegion).where(SupplierRegion.city_name.is_(None))).all()
            candidates = [item for item in candidates if item.external_country_ref.isdecimal()]
            candidates.sort(key=lambda item: (item.country_code not in ("US", "HK"), item.country_code != "US", item.id))
        city_count = 0
        inventory_status = "skipped_no_confirmed_city"
        for index, country in enumerate(candidates[:4], 1):
            city_http, city_run = sync(
                f"cities_candidate_{index}",
                f"/api/admin/suppliers/sync/cities?product_type_id=2&country_id={country.external_country_ref}",
            )
            matching_cities = db.scalars(select(SupplierRegion).where(
                SupplierRegion.city_name.is_not(None),
                SupplierRegion.external_country_ref == country.external_country_ref,
                SupplierRegion.country_code == country.country_code,
            )).all()
            if city_http == 200 and city_run.items_changed > 0 and len(matching_cities) == city_run.items_changed:
                city_count = len(matching_cities)
                inventory_http, inventory_run = sync(
                    "inventory", f"/api/admin/suppliers/sync/inventory?product_type_id=2&country_code={country.country_code}",
                )
                inventory_status = inventory_run.status if inventory_http == 200 else inventory_run.error_code
                break
        print(f"confirmed_city_count={city_count}; inventory_result={inventory_status}")
        quote_http, quote_run = sync("quote_product_11_first_purchase", "/api/admin/suppliers/sync/quotes", {
            "product_type_id": 11, "country": "US", "bandwidth_num": 100, "bill_timelen": 1,
        })
        if quote_http != 200:
            print("quote_response_shape=" + json.dumps(adapter.last_quote_response_shape or {"type": "unavailable"}, ensure_ascii=False))
        print("quote_mode=product_11_first_purchase_only; full_price_table=not_retested")

        overview = client.get("/api/admin/suppliers/overview", headers=headers)
        states = overview.json()["states"] if overview.status_code == 200 else {}
        print(f"admin_overview_http={overview.status_code}; admin_states=" + ",".join(f"{key}:{states.get(key, 'unavailable')}" for key in ("products", "regions", "inventory", "quotes")))
        city_snapshots = db.query(SupplierRegion).filter(SupplierRegion.city_name.is_not(None)).count()
        print(f"snapshots: cities={city_snapshots} inventory={db.query(SupplierInventorySnapshot).count()} quote={db.query(SupplierQuote).count()}")
        snapshots = db.scalars(select(SupplierInventorySnapshot)).all()
        if snapshots:
            for item in snapshots:
                item.checked_at = utcnow() - timedelta(minutes=settings.supplier_inventory_ttl_minutes + 1)
            db.commit()
            aged = client.get("/api/admin/suppliers/overview", headers=headers)
            print(f"expired_inventory_admin_state={aged.json()['states']['inventory']}")
        public = client.get("/api/public/products")
        unchanged = (public.status_code == 200 and len(public.json()) == 1
                     and public.json()[0]["price"] == 199 and public.json()[0]["is_in_stock"] is False
                     and not any(key in public.json()[0] for key in ("supplier", "unit_cost", "total_cost", "external_product_ref")))
        print(f"internal_catalog_unchanged={unchanged}")
        return 0 if country_http == 200 and city_count > 0 and inventory_status == "success" and quote_http == 200 and unchanged else 1
    finally:
        app.dependency_overrides.clear()
        client.close()
        adapter.close()
        db.close()
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
