"""任务互斥锁。

多副本部署时同一个任务可能被多个调度进程同时触发，用 Redis 的 ``SET NX EX``
抢锁；抢不到就跳过本轮。Redis 不可用时**也跳过**——宁可少跑一轮，也不要重复
执行扣款/入账类逻辑。

开发环境（``RATE_LIMIT_BACKEND=memory``，即没有 Redis）不加锁，直接执行。
"""

from contextlib import contextmanager
from uuid import uuid4

from redis import Redis
from redis.exceptions import RedisError

from app.core.config import get_settings

_RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""


@contextmanager
def task_lock(name: str, ttl_seconds: int):
    settings = get_settings()
    if settings.rate_limit_backend != "redis":
        yield True
        return

    key = f"lock:sched:{name}"
    token = uuid4().hex
    try:
        client = Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)
        acquired = bool(client.set(key, token, nx=True, ex=ttl_seconds))
    except RedisError:
        yield False
        return

    if not acquired:
        yield False
        return

    try:
        yield True
    finally:
        try:
            client.eval(_RELEASE_SCRIPT, 1, key, token)
        except RedisError:
            pass
