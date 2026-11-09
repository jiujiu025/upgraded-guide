"""P0 生产加固：关闭接口文档、多来源 CORS、公开只读接口限流。

设计见 docs/对外API与集成设计.md §2。三条都是上线前必须成立的边界：
1. 生产环境 /docs 与 /openapi.json 必须不存在（否则接口全貌挂公网）；
2. 跨域白名单支持多来源，但仍只认自家域名、禁止通配符与回环地址；
3. /public/* 不需要登录，必须按 IP 限流，不能任人刷算价。
"""
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db import get_db
from app.main import api_docs_paths, app
from app.models.base import Base
from app.services.registration_service import rate_limiter

VALID_SECRET = "R4nd0m_Production_Secret_2026!AbCdEfGh"


def production_settings(**overrides) -> dict:
    values = {
        "environment": "production",
        "jwt_secret_key": VALID_SECRET,
        "session_token_hash_key": "Distinct_Refresh_Hash_Secret_2026_AbCdEfGh!",
        "supplier_secret_encryption_key": "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff",
        "payment_secret_encryption_key": "ffeeddccbbaa99887766554433221100ffeeddccbbaa99887766554433221100",
        "database_url": "postgresql+psycopg://app:password@db.example.com/ops_platform",
        "frontend_origin": "https://console.example.com",
        "rate_limit_backend": "redis",
        "redis_url": "redis://redis.internal.example.com:6379/0",
        "trusted_proxy_cidrs": "10.20.0.0/24",
        "_env_file": None,
    }
    values.update(overrides)
    return values


def test_production_disables_interactive_docs():
    assert api_docs_paths("production") == {"docs_url": None, "redoc_url": None, "openapi_url": None}
    assert api_docs_paths("development") == {
        "docs_url": "/docs", "redoc_url": "/redoc", "openapi_url": "/openapi.json",
    }
    assert api_docs_paths("test")["openapi_url"] == "/openapi.json"


def test_cors_origins_merges_primary_and_extra_without_duplicates():
    settings = Settings(**production_settings(frontend_origins="https://www.example.com, https://console.example.com/"))
    assert settings.cors_origins == ("https://console.example.com", "https://www.example.com")


def test_cors_origins_rejects_wildcard_in_any_environment():
    with pytest.raises(ValidationError, match="通配符"):
        Settings(environment="test", frontend_origins="*", _env_file=None)


def test_cors_origins_rejects_http_extra_origin_in_production():
    with pytest.raises(ValidationError, match="HTTPS"):
        Settings(**production_settings(frontend_origins="http://www.example.com"))


def test_cors_origins_rejects_localhost_extra_origin_in_production():
    with pytest.raises(ValidationError, match="localhost"):
        Settings(**production_settings(frontend_origins="https://localhost:8443"))


def test_cors_origins_rejects_path_or_credentials():
    with pytest.raises(ValidationError, match="凭据|路径"):
        Settings(environment="test", frontend_origins="https://www.example.com/app", _env_file=None)


@pytest.fixture()
def public_client():
    rate_limiter.clear()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    def override_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    yield TestClient(app)
    app.dependency_overrides.clear()
    rate_limiter.clear()


def test_public_read_endpoints_are_rate_limited(public_client):
    limit = Settings().public_rate_limit
    for _ in range(limit):
        assert public_client.get("/api/public/products").status_code == 200
    blocked = public_client.get("/api/public/products")
    assert blocked.status_code == 429
    assert blocked.json()["detail"] == "请求过于频繁，请稍后再试"


def test_preview_requires_login_because_it_returns_prices(public_client):
    """算价接口是价格的一手来源，未登录必须 401（价格登录可见）。"""
    payload = {"product_id": 1, "region_id": 1, "quantity": 1, "billing_days": 30}
    assert public_client.post("/api/public/orders/preview", json=payload).status_code == 401


def test_authenticated_endpoints_are_not_affected_by_public_limit(public_client):
    """公开限额只作用于 /public/*，不能把登录用户一起卡掉。"""
    limit = Settings().public_rate_limit
    for _ in range(limit + 5):
        public_client.get("/api/public/regions")
    # /api/auth/me 未登录返回 401，说明请求确实进入了业务路由，不是被限流拦下。
    assert public_client.get("/api/auth/me").status_code == 401
