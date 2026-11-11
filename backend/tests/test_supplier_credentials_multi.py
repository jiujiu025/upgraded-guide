"""两个资源池各自的 API Key：后台手动录入、互不覆盖、可立即用于同步。

约束：
- 密文按供应商隔离（AAD 带 supplier code），A 家的密文贴到 B 家解不开；
- `/admin/suppliers/{id}/credential` 按 id 录入，旧 `/credential` 仍指资源池 1；
- 列表/池总览里每家的 Key 状态必须各自独立。
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import supplier_routes
from app.core.config import Settings
from app.core.security import create_access_token, hash_password
from app.db import get_db
from app.main import app
from app.models import User
from app.models.base import Base
from app.services import supplier_credentials
from app.services.session_service import create_session
from app.services.supplier_service import ensure_pool, ensure_pool_one, ensure_supplier, link_pool_supplier

MASTER_KEY = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"


@pytest.fixture()
def api(monkeypatch):
    settings = Settings(
        supplier_secret_encryption_key=MASTER_KEY,
        nineeightfive_api_key=None,
        global_proxy_api_key=None,
        _env_file=None,
    )
    monkeypatch.setattr(supplier_credentials, "get_settings", lambda: settings)
    monkeypatch.setattr(supplier_routes, "get_settings", lambda: settings)

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()

    supplier_one, _pool_one = ensure_pool_one(db)
    supplier_two = ensure_supplier(db, "985proxy")
    link_pool_supplier(db, ensure_pool(db, "global_proxy_pool_2", name="资源池 2"), supplier_two)
    super_admin = User(username="cred_super", password_hash=hash_password("Super_123456"),
                       role="super_admin", display_name="超管")
    admin = User(username="cred_admin", password_hash=hash_password("Admin_123456"),
                 role="admin", display_name="管理员")
    db.add_all([super_admin, admin])
    db.commit()

    headers = {}
    for key, user in (("super", super_admin), ("admin", admin)):
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
    state = {
        "client": TestClient(app), "db": db, "headers": headers,
        "one": supplier_one, "two": supplier_two,
    }
    yield state
    app.dependency_overrides.clear()
    db.close()
    engine.dispose()


def test_credentials_are_isolated_per_supplier(api):
    db = api["db"]
    supplier_one, supplier_two = api["one"], api["two"]
    actor = db.scalar(select(User).where(User.username == "cred_super"))

    supplier_credentials.save_api_key(db, actor, "key-for-985", supplier=supplier_two)
    db.commit()

    assert supplier_credentials.resolve_api_key(db, supplier_two) == "key-for-985"
    assert supplier_credentials.resolve_api_key(db, supplier_one) is None
    assert supplier_two.encrypted_api_key != "key-for-985"

    # 同一段密文换一个 supplier code 解不开（AAD 隔离）
    with pytest.raises(supplier_credentials.SupplierCredentialError):
        supplier_credentials.decrypt_api_key(supplier_two.encrypted_api_key, supplier_code="quanqiudaili")
    assert supplier_credentials.decrypt_api_key(
        supplier_two.encrypted_api_key, supplier_code="985proxy") == "key-for-985"


def test_save_key_by_supplier_id_and_pool_status(api):
    two_id = api["two"].id
    saved = api["client"].put(f"/api/admin/suppliers/{two_id}/credential",
                              json={"api_key": "pool2-secret"}, headers=api["headers"]["super"])
    assert saved.status_code == 200, saved.text
    assert saved.json() == {"configured": True, "source": "admin"}

    pools = api["client"].get("/api/admin/suppliers/pools", headers=api["headers"]["super"]).json()
    members = {m["code"]: m for pool in pools["items"] for m in pool["suppliers"]}
    assert members["985proxy"]["api_key_configured"] is True
    assert members["985proxy"]["credential_source"] == "admin"
    assert members["quanqiudaili"]["api_key_configured"] is False

    status = api["client"].get(f"/api/admin/suppliers/{two_id}/credential",
                               headers=api["headers"]["super"]).json()
    assert status == {"supplier_id": two_id, "configured": True, "source": "admin", "encryption_ready": True}


def test_legacy_endpoint_only_touches_pool_one(api):
    db = api["db"]
    super_headers = api["headers"]["super"]
    api["client"].put(f"/api/admin/suppliers/{api['two'].id}/credential",
                      json={"api_key": "pool2-secret"}, headers=super_headers)
    assert api["client"].put("/api/admin/suppliers/credential",
                             json={"api_key": "pool1-secret"}, headers=super_headers).status_code == 200
    db.expire_all()

    assert supplier_credentials.resolve_api_key(db, api["one"]) == "pool1-secret"
    assert supplier_credentials.resolve_api_key(db, api["two"]) == "pool2-secret"

    cleared = api["client"].delete("/api/admin/suppliers/credential", headers=super_headers)
    assert cleared.status_code == 200
    db.expire_all()
    assert supplier_credentials.resolve_api_key(db, api["one"]) is None
    assert supplier_credentials.resolve_api_key(db, api["two"]) == "pool2-secret"


def test_supplier_credential_requires_super_admin(api):
    url = f"/api/admin/suppliers/{api['two'].id}/credential"
    assert api["client"].put(url, json={"api_key": "x"}, headers=api["headers"]["admin"]).status_code == 403
    assert api["client"].delete(url, headers=api["headers"]["admin"]).status_code == 403
    assert api["client"].get(url, headers=api["headers"]["admin"]).status_code == 200
    assert api["client"].put("/api/admin/suppliers/9999/credential", json={"api_key": "x"},
                             headers=api["headers"]["super"]).status_code == 404
