"""就绪探针：必须能分出"库连不上"和"迁移没跑"两种故障，且不能把连接串泄出去。"""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main as main_module
from app.models.base import Base
from app.services import readiness_service
from app.services.readiness_service import migration_heads, readiness_report


@pytest.fixture()
def session_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


def _stamp_head(factory) -> None:
    head = migration_heads()[0]
    with factory() as db:
        db.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        db.execute(text("INSERT INTO alembic_version (version_num) VALUES (:v)"), {"v": head})
        db.commit()


def test_ready_when_database_and_migrations_match_code(session_factory):
    _stamp_head(session_factory)

    payload, ready = readiness_report(session_factory)

    assert ready is True
    assert payload["status"] == "ready"
    assert payload["checks"]["database"]["status"] == "ok"
    assert payload["checks"]["migrations"]["status"] == "ok"
    # 测试环境是内存限流，Redis 检查应显式跳过而不是报红。
    assert payload["checks"]["redis"]["status"] == "skipped"


def test_drifted_migration_blocks_readiness(session_factory):
    with session_factory() as db:
        db.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        db.execute(text("INSERT INTO alembic_version (version_num) VALUES ('0000_stale')"))
        db.commit()

    payload, ready = readiness_report(session_factory)

    assert ready is False
    assert payload["checks"]["migrations"]["status"] == "fail"
    assert payload["checks"]["migrations"]["current"] == ["0000_stale"]


def test_unversioned_database_is_only_a_warning_outside_production(session_factory, monkeypatch):
    monkeypatch.setattr(
        readiness_service,
        "get_settings",
        lambda: SimpleNamespace(environment="development", rate_limit_backend="memory"),
    )

    payload, ready = readiness_report(session_factory)

    assert ready is True
    assert payload["checks"]["migrations"]["status"] == "warn"


def test_unversioned_database_blocks_production_traffic(session_factory, monkeypatch):
    monkeypatch.setattr(
        readiness_service,
        "get_settings",
        lambda: SimpleNamespace(environment="production", rate_limit_backend="memory"),
    )

    payload, ready = readiness_report(session_factory)

    assert ready is False
    assert payload["checks"]["migrations"]["status"] == "fail"


def test_dead_database_reports_type_not_connection_string(monkeypatch):
    monkeypatch.setattr(
        readiness_service,
        "get_settings",
        lambda: SimpleNamespace(environment="production", rate_limit_backend="memory", redis_url="redis://localhost:6379/0"),
    )
    dead = create_engine("sqlite:////nonexistent-dir-99ip/ops.db")

    payload, ready = readiness_report(sessionmaker(bind=dead))

    assert ready is False
    assert payload["checks"]["database"]["status"] == "fail"
    assert payload["checks"]["database"]["detail"] == "OperationalError"
    # 数据库不可用时不再查迁移版本，避免次要错误盖掉根因。
    assert payload["checks"]["migrations"]["status"] == "skipped"
    assert "nonexistent-dir-99ip" not in str(payload)


def test_redis_backend_without_redis_fails_readiness(session_factory, monkeypatch):
    _stamp_head(session_factory)
    monkeypatch.setattr(
        readiness_service,
        "get_settings",
        lambda: SimpleNamespace(
            environment="production",
            rate_limit_backend="redis",
            redis_url="redis://127.0.0.1:1/0",
        ),
    )

    payload, ready = readiness_report(session_factory)

    assert ready is False
    assert payload["checks"]["redis"]["status"] == "fail"


def test_readyz_returns_503_body_when_degraded(monkeypatch):
    monkeypatch.setattr(
        main_module,
        "readiness_report",
        lambda: ({"status": "degraded", "checks": {"database": {"status": "fail"}}}, False),
    )

    response = TestClient(main_module.app).get("/readyz")

    assert response.status_code == 503
    assert response.json()["status"] == "degraded"


def test_readyz_returns_200_when_ready(monkeypatch):
    monkeypatch.setattr(
        main_module,
        "readiness_report",
        lambda: ({"status": "ready", "checks": {}}, True),
    )

    response = TestClient(main_module.app).get("/readyz")

    assert response.status_code == 200
