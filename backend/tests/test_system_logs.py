"""系统日志：采购/中转/模拟的运行记录，管理员可筛选查看。

锁住三件事：
1. 写日志失败不能影响业务（这里是单元级：write_log 遇异常不抛）；
2. 筛选是服务端做的，计数不随级别筛选收缩；
3. 模拟登录写日志时，actor 记的是发起的管理员，不是被模拟的人。
"""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import hash_password
from app.db import get_db
from app.main import app
from app.models import SystemLog, User
from app.models.base import Base, utcnow
from app.services.impersonation_service import ImpersonationContext
from app.services.log_service import write_log


@pytest.fixture()
def api():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()

    admin = User(username="root", password_hash=hash_password("Admin_123456"), role="admin", display_name="管理员")
    buyer = User(username="buyer", password_hash=hash_password("Customer_123456"), role="customer", display_name="客户乙")
    db.add_all([admin, buyer])
    db.flush()

    now = utcnow()
    db.add_all([
        SystemLog(level="error", category="transit", action="transit_provision_failed",
                  message="订单 ORD-1 中转未完成：TransitProvisionError", detail="上游单号=UP-1\n面板超时",
                  order_id=7, created_at=now),
        SystemLog(level="warning", category="impersonation", action="impersonation_start",
                  message="管理员 开始模拟 客户乙", detail="目标角色=customer", actor_id=admin.id, created_at=now),
        SystemLog(level="info", category="procurement", action="procurement_succeeded",
                  message="订单 ORD-2 已采购并交付 1 条线路", order_id=8, created_at=now - timedelta(days=2)),
        SystemLog(level="error", category="procurement", action="procurement_failed",
                  message="订单 ORD-3 采购失败：BALANCE_LOW", detail="上游余额不足",
                  created_at=now - timedelta(days=20)),
    ])
    db.commit()

    def override_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    yield {"client": client, "db": db, "admin": admin, "buyer": buyer}
    app.dependency_overrides.clear()
    db.close()
    engine.dispose()


def login(client) -> dict:
    response = client.post("/api/auth/login", json={"username": "root", "password": "Admin_123456"})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_list_logs_default_window_and_pagination(api):
    headers = login(api["client"])
    response = api["client"].get("/api/admin/logs", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    # 默认 7 天：20 天前那条不该出现
    assert body["total"] == 3
    assert body["items"][0]["action"] == "impersonation_start"
    assert body["counts"]["error"] >= 1


def test_filter_by_level_and_category(api):
    headers = login(api["client"])
    errors = api["client"].get("/api/admin/logs", params={"level": "error"}, headers=headers).json()
    assert {item["level"] for item in errors["items"]} == {"error"}

    transit = api["client"].get("/api/admin/logs", params={"category": "transit"}, headers=headers).json()
    assert transit["total"] == 1
    assert "中转" in transit["items"][0]["message"]


def test_keyword_searches_detail_too(api):
    headers = login(api["client"])
    body = api["client"].get("/api/admin/logs", params={"keyword": "余额不足"}, headers=headers).json()
    # 20 天前那条落在默认窗口外，放宽到 30 天才应该搜到
    assert body["total"] == 0

    wide = api["client"].get("/api/admin/logs", params={"keyword": "余额不足", "days": 30}, headers=headers).json()
    assert wide["total"] == 1
    assert wide["items"][0]["action"] == "procurement_failed"


def test_counts_do_not_shrink_with_level_filter(api):
    headers = login(api["client"])
    body = api["client"].get("/api/admin/logs", params={"level": "error"}, headers=headers).json()
    # 选中"错误"后，警告与信息的计数仍然在，否则 chip 会只剩一个可点
    assert body["counts"]["warning"] >= 1
    assert body["counts"]["info"] >= 1


def test_write_log_never_raises(api):
    db = api["db"]
    # 传一个不存在的分类/超长消息也不应该抛，业务不能被日志拖垮
    write_log(db, "error", "x" * 80, "y" * 100, "z" * 900)
    db.commit()
    assert db.scalar(select(SystemLog).where(SystemLog.action == "y" * 60)) is not None


def test_impersonation_log_uses_actor(api):
    db = api["db"]
    db.info["impersonation"] = ImpersonationContext(
        record_id=1, actor_id=api["admin"].id, actor_name="管理员",
        target_id=api["buyer"].id, target_name="客户乙",
    )
    write_log(db, "warning", "impersonation", "impersonation_start", "开始模拟", actor=api["buyer"])
    db.commit()

    log = db.scalar(select(SystemLog).where(SystemLog.action == "impersonation_start"))
    assert log.actor_id == api["admin"].id


def test_purge_only_removes_expired(api):
    from app.services.log_service import purge_old

    db = api["db"]
    # 20 天前那条仍在 30 天保留期内
    assert purge_old(db, days=30) == 0
    # 2 天前 + 20 天前两条被删，24 小时内的两条保留
    assert purge_old(db, days=1) == 2
