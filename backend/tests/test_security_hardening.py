import os
from ipaddress import ip_network
from uuid import uuid4

import fakeredis
import pytest
from fastapi.testclient import TestClient
from redis import Redis
from redis.exceptions import ConnectionError

from app.core.client_ip import resolve_client_ip
from app.main import app
from app.services import registration_service
from app.services.registration_service import (
    RateLimitExceeded,
    RateLimitUnavailable,
    RedisRateLimitBackend,
    SharedRateLimiter,
)


def test_security_headers_are_set_by_application():
    with TestClient(app) as client:
        response = client.get("/healthz")
    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
    assert "strict-transport-security" not in response.headers


def test_untrusted_peer_cannot_spoof_forwarded_client_ip():
    networks = (ip_network("10.20.0.0/24"),)
    assert resolve_client_ip("198.51.100.20", "203.0.113.10", networks) == "198.51.100.20"


def test_trusted_proxy_chain_resolves_first_untrusted_client():
    networks = (ip_network("10.20.0.0/24"), ip_network("192.0.2.0/24"))
    assert resolve_client_ip("10.20.0.8", "203.0.113.10, 192.0.2.15", networks) == "203.0.113.10"
    assert resolve_client_ip("10.20.0.8", "invalid-value", networks) == "10.20.0.8"


def test_redis_rate_limit_is_shared_across_limiter_instances():
    server = fakeredis.FakeServer()
    first = SharedRateLimiter(RedisRateLimitBackend(fakeredis.FakeRedis(server=server)), prefix="test:shared")
    second = SharedRateLimiter(RedisRateLimitBackend(fakeredis.FakeRedis(server=server)), prefix="test:shared")
    first.check("login", "203.0.113.1", 3, 60)
    second.check("login", "203.0.113.1", 3, 60)
    first.check("login", "203.0.113.1", 3, 60)
    with pytest.raises(RateLimitExceeded):
        second.check("login", "203.0.113.1", 3, 60)


class UnavailableRedis:
    def ping(self):
        raise ConnectionError("测试连接失败")

    def eval(self, *args):
        raise ConnectionError("测试连接失败")


def test_redis_failure_is_fail_closed(monkeypatch):
    limiter = SharedRateLimiter(RedisRateLimitBackend(UnavailableRedis()))
    with pytest.raises(RateLimitUnavailable):
        limiter.check("login", "203.0.113.2", 3, 60)
    monkeypatch.setattr(registration_service, "rate_limiter", limiter)
    with pytest.raises(RuntimeError, match="拒绝启动"):
        registration_service.verify_rate_limit_backend()


@pytest.mark.redis
def test_real_redis_shares_limits_between_clients():
    redis_url = os.getenv("TEST_REDIS_URL")
    if not redis_url:
        pytest.skip("仅在显式配置 TEST_REDIS_URL 的隔离环境运行")
    prefix = f"acceptance:{uuid4().hex}"
    first_client = Redis.from_url(redis_url)
    second_client = Redis.from_url(redis_url)
    first = SharedRateLimiter(RedisRateLimitBackend(first_client), prefix=prefix)
    second = SharedRateLimiter(RedisRateLimitBackend(second_client), prefix=prefix)
    try:
        first.ensure_available()
        first.check("register", "198.51.100.1", 2, 60)
        second.check("register", "198.51.100.1", 2, 60)
        with pytest.raises(RateLimitExceeded):
            first.check("register", "198.51.100.1", 2, 60)
    finally:
        for key in first_client.scan_iter(f"{prefix}:*"):
            first_client.delete(key)
