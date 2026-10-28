import multiprocessing
import os
from uuid import uuid4

import pytest
from redis import Redis


pytestmark = pytest.mark.redis


def _request_in_process(endpoint: str, identity: str, result_queue) -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    payload = ({"username": "missing-acceptance", "password": "Invalid_123!"}
               if endpoint == "login" else {
                   "username": identity, "password": "Register_123!",
                   "name": "隔离验收客户", "email": f"{identity}@example.invalid",
               })
    path = "/api/auth/login" if endpoint == "login" else "/api/public/register"
    with TestClient(app) as client:
        result_queue.put(client.post(path, json=payload).status_code)


def _run_request(endpoint: str, identity: str) -> int:
    context = multiprocessing.get_context("spawn")
    result_queue = context.Queue()
    process = context.Process(target=_request_in_process, args=(endpoint, identity, result_queue))
    process.start()
    process.join(timeout=30)
    if process.is_alive():
        process.terminate()
        process.join()
        pytest.fail("隔离认证进程超时")
    assert process.exitcode == 0
    return result_queue.get(timeout=2)


@pytest.mark.parametrize("endpoint,first_status", [("login", 401), ("register", 201)])
def test_real_redis_limit_shared_across_auth_processes(endpoint, first_status):
    url = os.getenv("TEST_REDIS_URL")
    if not url:
        pytest.skip("需要隔离环境的真实 Redis")
    redis = Redis.from_url(url)
    key = f"ops:rate:{endpoint}:unknown"
    identity = f"redis-{uuid4().hex[:12]}"
    redis.delete(key)
    try:
        assert _run_request(endpoint, identity) == first_status
        assert _run_request(endpoint, identity) == 429
    finally:
        redis.delete(key)
