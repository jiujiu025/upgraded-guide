from collections import defaultdict, deque
from hashlib import sha256
from threading import Lock
from time import monotonic

from fastapi import HTTPException, Request
from redis import Redis
from redis.exceptions import RedisError

from app.core.client_ip import get_client_ip
from app.core.config import Settings, get_settings


class RateLimitExceeded(Exception):
    pass


class RateLimitUnavailable(Exception):
    pass


class MemoryRateLimitBackend:
    """仅供开发和单元测试使用的进程内限流后端。"""

    def __init__(self):
        self._attempts: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def increment(self, key: str, window_seconds: int) -> int:
        now = monotonic()
        with self._lock:
            attempts = self._attempts[key]
            while attempts and attempts[0] <= now - window_seconds:
                attempts.popleft()
            attempts.append(now)
            return len(attempts)

    def ping(self) -> None:
        return None

    def clear(self) -> None:
        with self._lock:
            self._attempts.clear()


class RedisRateLimitBackend:
    """使用 Redis 原子脚本实现多实例共享的固定窗口限流。"""

    _INCREMENT_SCRIPT = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return current
"""

    def __init__(self, client):
        self.client = client

    @classmethod
    def from_url(cls, url: str):
        return cls(Redis.from_url(url, decode_responses=True, socket_connect_timeout=2, socket_timeout=2))

    def increment(self, key: str, window_seconds: int) -> int:
        try:
            return int(self.client.eval(self._INCREMENT_SCRIPT, 1, key, window_seconds))
        except RedisError as exc:
            raise RateLimitUnavailable from exc

    def ping(self) -> None:
        try:
            self.client.ping()
        except RedisError as exc:
            raise RateLimitUnavailable from exc

    def clear(self) -> None:
        return None


class SharedRateLimiter:
    def __init__(self, backend, prefix: str = "ops:rate"):
        self.backend = backend
        self.prefix = prefix

    def check(self, scope: str, client_key: str, limit: int, window_seconds: int) -> None:
        count = self.backend.increment(f"{self.prefix}:{scope}:{client_key}", window_seconds)
        if count > limit:
            raise RateLimitExceeded

    def ensure_available(self) -> None:
        self.backend.ping()

    def clear(self) -> None:
        self.backend.clear()


def build_rate_limiter(settings: Settings) -> SharedRateLimiter:
    if settings.rate_limit_backend == "redis":
        return SharedRateLimiter(RedisRateLimitBackend.from_url(settings.redis_url))
    return SharedRateLimiter(MemoryRateLimitBackend())


settings = get_settings()
rate_limiter = build_rate_limiter(settings)
# 保留名称兼容现有测试和调用方。
registration_rate_limiter = rate_limiter


def verify_rate_limit_backend() -> None:
    try:
        rate_limiter.ensure_available()
    except RateLimitUnavailable:
        raise RuntimeError("共享限流 Redis 不可用，应用拒绝启动") from None


def _enforce(request: Request, scope: str, limit: int) -> None:
    _check_rate_limit(scope, get_client_ip(request), limit, settings.rate_limit_window_seconds)


def _check_rate_limit(scope: str, client_key: str, limit: int, window_seconds: int) -> None:
    try:
        rate_limiter.check(scope, client_key, limit, window_seconds)
    except RateLimitExceeded as exc:
        raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试") from exc
    except RateLimitUnavailable as exc:
        raise HTTPException(status_code=503, detail="安全服务暂时不可用，请稍后再试") from exc


def enforce_registration_rate_limit(request: Request) -> None:
    _enforce(request, "register", settings.registration_rate_limit)


def enforce_login_rate_limit(request: Request) -> None:
    _enforce(request, "login", settings.login_rate_limit)


def enforce_password_recovery_rate_limit(request: Request, username: str) -> None:
    _check_rate_limit("password_recovery_ip", get_client_ip(request), 5, 900)
    account_key = sha256(username.strip().lower().encode("utf-8")).hexdigest()
    _check_rate_limit("password_recovery_account", account_key, 10, 3600)
