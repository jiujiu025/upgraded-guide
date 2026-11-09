"""硬上限列表必须显式告知"被截断"（P2-3.8）。

/customers 上限 200、订单中心 1000、订阅 2000。之前超限数据静默消失，
界面看不出少了东西；现在多查一条，超限回 X-Result-Truncated 与上限值。
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import create_access_token, hash_password
from app.db import get_db
from app.main import app
from app.models import Customer, User
from app.models.base import Base
from app.services.session_service import create_session


@pytest.fixture()
def api():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()

    admin = User(username="admin", password_hash=hash_password("Admin_123456"), role="admin", display_name="管理员")
    db.add(admin)
    db.commit()
    session_row, _ = create_session(db, admin)
    db.commit()
    headers = {"Authorization": f"Bearer {create_access_token(admin.id, admin.role, session_row.id, admin.auth_version)}"}

    def override_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    yield {"client": TestClient(app), "headers": headers, "db": db, "Session": Session}
    app.dependency_overrides.clear()


def test_customer_list_marks_truncation(api):
    db = api["db"]
    db.add_all([Customer(name=f"客户{i}", email=f"c{i}@test.com") for i in range(202)])
    db.commit()

    response = api["client"].get("/api/customers", headers=api["headers"])
    assert response.status_code == 200
    assert len(response.json()) == 200
    assert response.headers["X-Result-Truncated"] == "true"
    assert response.headers["X-Result-Limit"] == "200"


def test_customer_list_has_no_flag_when_under_limit(api):
    api["db"].add(Customer(name="只有一个", email="one@test.com"))
    api["db"].commit()

    response = api["client"].get("/api/customers", headers=api["headers"])
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert "X-Result-Truncated" not in response.headers
