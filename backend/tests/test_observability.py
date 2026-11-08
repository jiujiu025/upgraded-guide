"""访问日志与连接池参数：这两样都是"出事时才发现没配"的东西，用测试钉住。"""
import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import main as main_module
from app.core.request_logging import RequestLoggingMiddleware, sanitized_request_id
from app.db import build_engine_kwargs


def _client() -> TestClient:
    app = FastAPI()
    app.add_middleware(RequestLoggingMiddleware)

    @app.get("/ok")
    def ok():
        return {"ok": True}

    @app.get("/boom")
    def boom():
        raise RuntimeError("数据库炸了")

    return TestClient(app, raise_server_exceptions=False)


def test_upstream_request_id_is_reused_and_echoed():
    response = _client().get("/ok", headers={"X-Request-ID": "abc12345-abc"})

    assert response.headers["x-request-id"] == "abc12345-abc"


def test_missing_request_id_is_generated():
    response = _client().get("/ok")

    request_id = response.headers["x-request-id"]
    assert len(request_id) == 32
    assert request_id.isalnum()


@pytest.mark.parametrize("bad", ["short", "with space", "bad\nvalue", "x" * 65, "<script>alert(1)</script>"])
def test_malformed_request_id_is_replaced(bad):
    response = _client().get("/ok", headers={"X-Request-ID": bad})

    assert response.headers["x-request-id"] != bad
    assert len(response.headers["x-request-id"]) == 32


def test_request_id_accepts_uuid_and_trace_ids():
    assert sanitized_request_id("7f3a9c1e2b4d4a8f9e0c1d2b3a4f5e6d") == "7f3a9c1e2b4d4a8f9e0c1d2b3a4f5e6d"
    assert sanitized_request_id("trace_id.001-abc") == "trace_id.001-abc"


def test_access_log_carries_status_duration_and_request_id(caplog):
    caplog.set_level(logging.INFO, logger="app.request")

    _client().get("/ok", headers={"X-Request-ID": "req-00000001"})

    line = caplog.records[-1].getMessage()
    assert "method=GET" in line
    assert "path=/ok" in line
    assert "status=200" in line
    assert "duration_ms=" in line
    assert "request_id=req-00000001" in line


def test_server_error_is_logged_as_warning(caplog):
    caplog.set_level(logging.INFO, logger="app.request")

    response = _client().get("/boom")

    assert response.status_code == 500
    assert caplog.records[-1].levelno == logging.WARNING
    assert "status=500" in caplog.records[-1].getMessage()


def test_probe_paths_are_logged_at_debug_only(caplog):
    caplog.set_level(logging.DEBUG, logger="app.request")

    TestClient(main_module.app).get("/healthz")

    health_records = [record for record in caplog.records if "path=/healthz" in record.getMessage()]
    assert health_records
    assert all(record.levelno == logging.DEBUG for record in health_records)


def test_postgres_engine_gets_pre_ping_and_pool_limits():
    kwargs = build_engine_kwargs("postgresql+psycopg://user:pw@db:5432/ops")

    assert kwargs["pool_pre_ping"] is True
    assert kwargs["pool_size"] >= 1
    assert kwargs["max_overflow"] >= 0
    assert kwargs["pool_recycle"] >= 60
    assert kwargs["connect_args"]["connect_timeout"] >= 1


def test_sqlite_engine_keeps_single_file_settings():
    kwargs = build_engine_kwargs("sqlite:///./data/ops_platform.db")

    assert kwargs == {"connect_args": {"check_same_thread": False}, "future": True}
