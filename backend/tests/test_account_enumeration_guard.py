"""账号枚举防线：公开接口不能变成"这个用户名/邮箱/账号存在吗"的查询机。

背景：注册、登录、邀请码校验都是匿名可达的。任何一处响应差异（状态码、文案、
字段有无）都会变成探测信号——撞库前先用它筛出有效账号，成本能降一个数量级。

这个文件锁的是现状里已经成立、但没人用测试钉住的性质：

1. 注册接口的验证码校验在业务校验之前，没解题就拿着已存在的用户名/邮箱去试，
   拿到的是"请先完成图形验证码"，不是冲突提示；
2. 用户名冲突用通稿，不确认"这个用户名已被占用"；
3. 登录失败对"用户不存在"和"密码错误"完全同构（同一个状态码、同一句话）；
4. 邀请码校验对有效/无效码返回同构结构，不用 404 区分，不给码空间扫描留信号。
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import hash_password
from app.db import get_db
from app.main import app
from app.models import Customer, User
from app.models.base import Base
from app.services.captcha_service import issue_challenge
from app.services.invite_code_service import create_invite_code
from app.services.registration_service import registration_rate_limiter

REGISTER = "/api/public/register"
LOGIN = "/api/auth/login"
INVITE_CHECK = "/api/public/invite-codes/"

EXISTING_USERNAME = "customer_a"
EXISTING_EMAIL = "a@example.com"


@pytest.fixture()
def api():
    registration_rate_limiter.clear()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()
    customer_user = User(username=EXISTING_USERNAME, password_hash=hash_password("Customer_123456"),
                         role="customer", display_name="客户 A")
    sales_user = User(username="sales_a", password_hash=hash_password("Sales_123456"),
                      role="sales", display_name="销售 A")
    db.add_all([customer_user, sales_user])
    db.flush()
    db.add(Customer(name="客户 A", email=EXISTING_EMAIL, user_id=customer_user.id))
    db.commit()

    def override_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    client.db = db
    yield client
    app.dependency_overrides.clear()
    db.close()


def payload(username: str, **overrides) -> dict:
    base = {
        "username": username,
        "password": "StrongPass_123!",
        "name": "探针",
        "email": f"{username}@example.com",
        "phone": None,
    }
    base.update(overrides)
    return base


def test_register_without_captcha_hides_username_conflict(api):
    """已存在的用户名 + 没解验证码 → 只能是验证码错误。"""
    response = api.post(REGISTER, json=payload(EXISTING_USERNAME))
    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert "验证码" in detail
    assert "存在" not in detail and "占用" not in detail


def test_register_without_captcha_hides_email_conflict(api):
    """已存在的邮箱 + 没解验证码 → 同样只返回验证码错误，不确认邮箱在库。"""
    response = api.post(REGISTER, json=payload("probe_email", email=EXISTING_EMAIL))
    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert "验证码" in detail
    assert "邮箱" not in detail


def test_username_conflict_message_is_generic_even_after_captcha(api):
    """解了验证码也不确认"这个用户名已被占用"，只给通稿。"""
    challenge = issue_challenge()
    response = api.post(REGISTER, json=payload(
        EXISTING_USERNAME,
        captcha_token=challenge.token,
        captcha_selection=sorted(challenge.answers),
    ))
    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert "存在" not in detail and "占用" not in detail
    assert detail == "无法完成注册，请检查提交信息"


def test_login_failure_is_identical_for_unknown_user(api):
    """用户不存在与密码错误：状态码、文案、字段全一致。"""
    unknown = api.post(LOGIN, json={"username": "no_such_user", "password": "WrongPass_123!"})
    existing = api.post(LOGIN, json={"username": EXISTING_USERNAME, "password": "WrongPass_123!"})
    assert unknown.status_code == existing.status_code == 401
    assert unknown.json() == existing.json()


def test_invite_code_check_has_no_shape_difference(api):
    """有效码与无效码返回同一结构，且都用 200——不用 404 告诉扫描器"这个码不存在"。"""
    code = create_invite_code(api.db, api.db.query(User).filter_by(username="sales_a").one(), note="测试")
    api.db.commit()

    valid = api.get(f"{INVITE_CHECK}{code.code}")
    invalid = api.get(f"{INVITE_CHECK}ZZZZZZZZ")

    assert valid.status_code == invalid.status_code == 200
    assert set(valid.json()) == set(invalid.json()) == {"valid", "inviter_name"}
    assert valid.json()["valid"] is True
    assert invalid.json() == {"valid": False, "inviter_name": None}
