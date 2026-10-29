from functools import lru_cache
from ipaddress import ip_network
from typing import Literal
from urllib.parse import urlsplit

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

INSECURE_JWT_SECRETS = {"change-this-in-production", "development-only-change-this-secret"}


class Settings(BaseSettings):
    database_url: str = "sqlite:///./data/ops_platform.db"
    jwt_secret_key: str = "development-only-change-this-secret"
    jwt_expire_minutes: int = 10
    refresh_session_days: int = 7
    session_token_hash_key: SecretStr | None = None
    admin_username: str = "admin"
    admin_password: str = ""
    frontend_origin: str = "http://localhost:5173"
    environment: Literal["development", "test", "production"] = "development"
    rate_limit_backend: Literal["memory", "redis"] = "memory"
    redis_url: str = "redis://localhost:6379/0"
    trusted_proxy_cidrs: str = "127.0.0.1/32,::1/128"
    login_rate_limit: int = 10
    registration_rate_limit: int = 5
    rate_limit_window_seconds: int = 60
    global_proxy_api_key: SecretStr | None = None
    supplier_secret_encryption_key: SecretStr | None = None
    global_proxy_base_url: str = "https://admin.quanqiudaili.com"
    supplier_http_timeout_seconds: float = 10.0
    supplier_read_retry_attempts: int = 3
    supplier_retry_backoff_seconds: float = 0.25
    supplier_retry_max_delay_seconds: float = 5.0
    supplier_auto_procurement_enabled: Literal[False] = False
    supplier_catalog_ttl_minutes: int = 1440
    supplier_inventory_ttl_minutes: int = 30
    supplier_quote_ttl_minutes: int = 1440
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    @model_validator(mode="after")
    def validate_production_settings(self):
        self._parse_trusted_proxy_networks()
        if self.login_rate_limit < 1 or self.registration_rate_limit < 1 or self.rate_limit_window_seconds < 1:
            raise ValueError("限流次数和时间窗口必须为正整数")
        if self.jwt_expire_minutes != 10 or self.refresh_session_days != 7:
            raise ValueError("访问令牌与刷新会话有效期必须分别为 10 分钟和 7 天")
        if self.supplier_http_timeout_seconds <= 0:
            raise ValueError("供应商 HTTP 超时时间必须大于 0")
        if self.supplier_read_retry_attempts < 1 or self.supplier_read_retry_attempts > 5:
            raise ValueError("供应商只读重试次数必须在 1 到 5 之间")
        if self.supplier_retry_backoff_seconds < 0 or self.supplier_retry_max_delay_seconds <= 0:
            raise ValueError("供应商重试退避参数无效")
        if min(self.supplier_catalog_ttl_minutes, self.supplier_inventory_ttl_minutes, self.supplier_quote_ttl_minutes) < 1:
            raise ValueError("供应商快照有效期必须为正整数")
        if self.global_proxy_base_url.rstrip("/") != "https://admin.quanqiudaili.com":
            raise ValueError("全球代理 API 地址必须使用官方 HTTPS 地址")
        if self.environment != "production":
            return self

        if "jwt_secret_key" in self.model_fields_set and self.jwt_secret_key in INSECURE_JWT_SECRETS:
            raise ValueError("生产环境禁止使用默认 JWT_SECRET_KEY")
        if "jwt_secret_key" in self.model_fields_set and (len(self.jwt_secret_key.encode("utf-8")) < 32 or len(set(self.jwt_secret_key)) < 12):
            raise ValueError("生产环境 JWT_SECRET_KEY 必须使用至少 32 字节的高熵随机值")

        required = {"database_url", "jwt_secret_key", "session_token_hash_key", "frontend_origin", "redis_url", "trusted_proxy_cidrs"}
        missing = sorted(required - self.model_fields_set)
        if missing:
            raise ValueError(f"生产环境缺少必需配置: {', '.join(missing)}")
        if not self.session_token_hash_key or len(self.session_token_hash_key.get_secret_value()) < 32:
            raise ValueError("生产环境必须配置独立的 SESSION_TOKEN_HASH_KEY")
        encryption_key = self.supplier_secret_encryption_key.get_secret_value() if self.supplier_secret_encryption_key else ""
        try:
            encryption_key_bytes = bytes.fromhex(encryption_key)
        except ValueError as exc:
            raise ValueError("生产环境必须配置 64 位十六进制 SUPPLIER_SECRET_ENCRYPTION_KEY") from exc
        if len(encryption_key_bytes) != 32 or len(set(encryption_key_bytes)) < 12:
            raise ValueError("生产环境必须配置 64 位十六进制 SUPPLIER_SECRET_ENCRYPTION_KEY")

        database_url = urlsplit(self.database_url.replace("postgresql+psycopg://", "postgresql://", 1))
        if database_url.scheme != "postgresql" or not database_url.hostname:
            raise ValueError("生产环境 DATABASE_URL 必须是显式配置的 PostgreSQL 地址")
        if database_url.hostname.lower() in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("生产环境 DATABASE_URL 禁止使用 localhost 或回环地址")

        if self.frontend_origin == "*" or "*" in self.frontend_origin:
            raise ValueError("生产环境 FRONTEND_ORIGIN 禁止使用通配符")
        frontend_origin = urlsplit(self.frontend_origin)
        if frontend_origin.scheme != "https" or not frontend_origin.hostname:
            raise ValueError("生产环境 FRONTEND_ORIGIN 必须是有效的 HTTPS 来源")
        if frontend_origin.hostname.lower() in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("生产环境 FRONTEND_ORIGIN 禁止使用 localhost 或回环地址")
        if frontend_origin.username or frontend_origin.password or frontend_origin.query or frontend_origin.fragment or frontend_origin.path not in {"", "/"}:
            raise ValueError("生产环境 FRONTEND_ORIGIN 必须只包含 HTTPS 来源，不得包含凭据、路径、查询或片段")

        if self.rate_limit_backend != "redis":
            raise ValueError("生产环境 RATE_LIMIT_BACKEND 必须为 redis")
        redis_url = urlsplit(self.redis_url)
        if redis_url.scheme not in {"redis", "rediss"} or not redis_url.hostname:
            raise ValueError("生产环境 REDIS_URL 必须是有效的 Redis 地址")
        if redis_url.hostname.lower() in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("生产环境 REDIS_URL 禁止使用 localhost 或回环地址")
        return self

    def _parse_trusted_proxy_networks(self):
        values = [item.strip() for item in self.trusted_proxy_cidrs.split(",") if item.strip()]
        if not values:
            raise ValueError("TRUSTED_PROXY_CIDRS 不能为空")
        try:
            networks = tuple(ip_network(item, strict=False) for item in values)
        except ValueError as exc:
            raise ValueError("TRUSTED_PROXY_CIDRS 包含无效 IP 或 CIDR") from exc
        if any(network.prefixlen == 0 for network in networks):
            raise ValueError("TRUSTED_PROXY_CIDRS 禁止信任全部地址")
        return networks

    @property
    def trusted_proxy_networks(self):
        return self._parse_trusted_proxy_networks()


@lru_cache
def get_settings() -> Settings:
    return Settings()
