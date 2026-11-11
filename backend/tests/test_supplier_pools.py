"""双上游资源池骨架：两个池都要在后台可见，且初始化是幂等的。

对应 docs/双上游资源池设计.md 的阶段 0。这里不涉及路由与采购，只锁三件事：
1. `ensure_supplier` / `ensure_pool` 可重复调用，不会造出第二个"全球代理"；
2. `/admin/suppliers/pools` 同时返回资源池 1 与资源池 2，含成员与能力标签；
3. 非管理员看不到池配置。
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import create_access_token, hash_password
from app.db import get_db
from app.main import app
from app.models import ResourcePool, ResourcePoolSupplier, Supplier, User
from app.models.base import Base
from app.services.session_service import create_session
from app.services.supplier_service import (
    ensure_pool, ensure_pool_one, ensure_supplier, link_pool_supplier,
)


@pytest.fixture()
def api():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()

    admin = User(username="pool_admin", password_hash=hash_password("Admin_123456"),
                 role="super_admin", display_name="管理员")
    customer = User(username="pool_customer", password_hash=hash_password("Customer_123456"),
                    role="customer", display_name="客户")
    db.add_all([admin, customer])
    db.commit()

    ensure_pool_one(db)
    supplier_two = ensure_supplier(db, "985proxy")
    pool_two = ensure_pool(db, "global_proxy_pool_2", name="资源池 2")
    link_pool_supplier(db, pool_two, supplier_two, priority=1)
    db.commit()

    headers = {}
    for key, user in (("admin", admin), ("customer", customer)):
        session, _ = create_session(db, user)
        headers[key] = {"Authorization": f"Bearer {create_access_token(user.id, user.role, session.id, user.auth_version)}"}
    db.commit()

    def override_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    state = {"client": TestClient(app), "db": db, "headers": headers}
    yield state
    app.dependency_overrides.clear()
    db.close()
    engine.dispose()


def test_ensure_supplier_and_pool_are_idempotent(api):
    db = api["db"]
    ensure_supplier(db)
    ensure_supplier(db, "985proxy")
    ensure_pool(db, "global_proxy_pool_2", name="资源池 2")
    db.commit()

    assert db.scalar(select(func.count()).select_from(Supplier)) == 2
    assert db.scalar(select(func.count()).select_from(ResourcePool)) == 2
    assert db.scalar(select(func.count()).select_from(ResourcePoolSupplier)) == 2


def test_pools_endpoint_lists_both_pools_with_members(api):
    response = api["client"].get("/api/admin/suppliers/pools", headers=api["headers"]["admin"])
    assert response.status_code == 200, response.text
    body = response.json()

    names = [item["name"] for item in body["items"]]
    assert names == ["资源池 1", "资源池 2"]
    codes = {member["code"] for item in body["items"] for member in item["suppliers"]}
    assert codes == {"quanqiudaili", "985proxy"}

    second = next(item for item in body["items"] if item["code"] == "global_proxy_pool_2")
    member = second["suppliers"][0]
    assert member["adapter_type"] == "nine_eight_five"
    assert member["api_key_configured"] is False
    assert member["priority"] == 1
    assert member["counts"] == {"products": 0, "countries": 0, "cities": 0}
    assert body["min_margin_percent"] == 20
    assert body["purchase_enabled"] is False


def test_pools_endpoint_rejects_non_admin(api):
    assert api["client"].get("/api/admin/suppliers/pools",
                             headers=api["headers"]["customer"]).status_code == 403
    assert api["client"].get("/api/admin/suppliers/pools").status_code == 401
