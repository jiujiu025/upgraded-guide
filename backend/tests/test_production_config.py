import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine

from app.core.config import Settings
from app import seed as seed_module


VALID_SECRET = "R4nd0m_Production_Secret_2026!AbCdEfGh"
VALID_DATABASE_URL = "postgresql+psycopg://app:password@db.example.com/ops_platform"
VALID_FRONTEND_ORIGIN = "https://console.example.com"


def production_settings(**overrides) -> dict:
    values = {
        "environment": "production",
        "jwt_secret_key": VALID_SECRET,
        "session_token_hash_key": "Distinct_Refresh_Hash_Secret_2026_AbCdEfGh!",
        "database_url": VALID_DATABASE_URL,
        "frontend_origin": VALID_FRONTEND_ORIGIN,
        "rate_limit_backend": "redis",
        "redis_url": "redis://redis.internal.example.com:6379/0",
        "trusted_proxy_cidrs": "10.20.0.0/24,2001:db8:20::/64",
        "_env_file": None,
    }
    values.update(overrides)
    return values


def test_invalid_environment_name_is_rejected():
    with pytest.raises(ValidationError, match="environment"):
        Settings(environment="prod", _env_file=None)


def test_production_requires_explicit_security_settings():
    with pytest.raises(ValidationError, match="生产环境缺少必需配置"):
        Settings(environment="production", _env_file=None)


def test_production_rejects_default_or_low_entropy_secret_without_leaking_it():
    with pytest.raises(ValidationError, match="默认 JWT_SECRET_KEY"):
        Settings(**production_settings(jwt_secret_key="change-this-in-production"))

    weak_secret = "a" * 40
    with pytest.raises(ValidationError) as error:
        Settings(**production_settings(jwt_secret_key=weak_secret))
    assert "高熵随机值" in str(error.value)
    assert weak_secret not in str(error.value)


@pytest.mark.parametrize(
    ("database_url", "message"),
    [
        ("sqlite:///./production.db", "PostgreSQL"),
        ("postgresql+psycopg://app:password@localhost/ops_platform", "localhost"),
    ],
)
def test_production_rejects_unsafe_database_url(database_url, message):
    with pytest.raises(ValidationError, match=message):
        Settings(**production_settings(database_url=database_url))


@pytest.mark.parametrize(
    ("frontend_origin", "message"),
    [
        ("http://console.example.com", "HTTPS"),
        ("https://localhost", "localhost"),
        ("*", "通配符"),
    ],
)
def test_production_rejects_unsafe_frontend_origin(frontend_origin, message):
    with pytest.raises(ValidationError, match=message):
        Settings(**production_settings(frontend_origin=frontend_origin))


def test_valid_production_settings_and_postgresql_driver_can_load():
    settings = Settings(**production_settings())
    engine = create_engine(settings.database_url)
    try:
        assert engine.dialect.name == "postgresql"
        assert engine.dialect.driver == "psycopg"
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"rate_limit_backend": "memory"}, "RATE_LIMIT_BACKEND"),
        ({"redis_url": "redis://localhost:6379/0"}, "REDIS_URL"),
        ({"trusted_proxy_cidrs": ""}, "TRUSTED_PROXY_CIDRS"),
        ({"trusted_proxy_cidrs": "not-a-cidr"}, "TRUSTED_PROXY_CIDRS"),
        ({"trusted_proxy_cidrs": "0.0.0.0/0"}, "信任全部地址"),
    ],
)
def test_production_rejects_unsafe_rate_limit_and_proxy_settings(overrides, message):
    with pytest.raises(ValidationError, match=message):
        Settings(**production_settings(**overrides))


def test_seed_is_forbidden_in_production(monkeypatch):
    settings = Settings(**production_settings(admin_password="Unused_123456"))
    monkeypatch.setattr(seed_module, "get_settings", lambda: settings)
    with pytest.raises(ValueError, match="只允许在开发或测试环境"):
        seed_module.seed()
