import os

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker

from app import bootstrap_admin
from app.models import AuditLog, User


pytestmark = pytest.mark.postgres


def test_bootstrap_requires_production_postgresql(monkeypatch):
    monkeypatch.setattr(bootstrap_admin, "get_settings", lambda: type("Settings", (), {"environment": "test"})())
    with pytest.raises(ValueError, match="生产 PostgreSQL"):
        bootstrap_admin.initialize_admin("chief", "Strong_Bootstrap_2026!")


def test_postgresql_bootstrap_once_and_audited(monkeypatch):
    database_url = os.getenv("TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("仅在显式配置 TEST_POSTGRES_URL 的隔离环境运行")
    engine = create_engine(database_url)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(bootstrap_admin, "engine", engine)
    monkeypatch.setattr(bootstrap_admin, "SessionLocal", factory)
    monkeypatch.setattr(bootstrap_admin, "get_settings", lambda: type("Settings", (), {
        "environment": "production", "admin_password": "",
    })())
    try:
        with engine.begin() as connection:
            connection.execute(text("TRUNCATE TABLE users RESTART IDENTITY CASCADE"))
        with pytest.raises(ValueError, match="默认密码"):
            bootstrap_admin.initialize_admin("chief", "Password123")
        bootstrap_admin.initialize_admin("chief", "Strong_Bootstrap_2026!")
        with pytest.raises(ValueError, match="重复初始化"):
            bootstrap_admin.initialize_admin("second", "Another_Strong_2026!")
        with factory() as db:
            user = db.scalar(select(User))
            assert user.role == "super_admin"
            assert user.must_change_password is True
            assert db.scalar(select(func.count()).select_from(User)) == 1
            audit = db.scalar(select(AuditLog))
            assert audit.action == "bootstrap_super_admin" and audit.actor_id == user.id
            assert "Strong_Bootstrap" not in (audit.detail or "")
    finally:
        with engine.begin() as connection:
            connection.execute(text("TRUNCATE TABLE users RESTART IDENTITY CASCADE"))
        engine.dispose()
