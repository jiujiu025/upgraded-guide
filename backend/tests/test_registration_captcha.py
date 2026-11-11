"""注册图形验证码：出题、判题、一次性、错误次数、过期与限流。

设计见 docs/注册图形验证码设计.md。这里锁住的几条底线：

- 题目答案不能出现在响应里（图形是 SVG 路径，不含任何文本语义）；
- 没做验证码 / 选错 / 拿旧 token 重放，都不能建出账号；
- 同一道题错 3 次作废，避免只靠一道题把 2^9 种组合试完；
- 出题接口有限流，否则题库等于免费。
"""
import base64
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import hash_password
from app.db import get_db
from app.main import app
from app.models import Customer, User
from app.models.base import Base
from app.services.captcha_service import TILE_COUNT, issue_challenge
from app.services.registration_service import registration_rate_limiter

REGISTER = "/api/public/register"


@pytest.fixture()
def api():
    registration_rate_limiter.clear()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()
    db.add(User(username="admin", password_hash=hash_password("Admin_123456"), role="admin", display_name="管理员"))
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
        "name": f"客户 {username}",
        "email": f"{username}@example.com",
        "phone": None,
    }
    base.update(overrides)
    return base


def solved(challenge) -> dict:
    return {"captcha_token": challenge.token, "captcha_selection": sorted(challenge.answers)}


def test_captcha_endpoint_returns_playable_grid(api):
    response = api.get("/api/public/captcha")
    assert response.status_code == 200, response.text
    data = response.json()
    assert len(data["tiles"]) == TILE_COUNT
    assert data["target"]
    assert 3 <= data["count"] <= 4
    assert data["expires_in"] > 0
    for index, tile in enumerate(data["tiles"]):
        assert tile["index"] == index
        assert tile["svg"].startswith("<svg")
        # 图形里不能有任何文字：有了文字，脚本读 DOM 就能抄答案。
        assert "<text" not in tile["svg"]


def test_captcha_token_does_not_leak_answers(api):
    data = api.get("/api/public/captcha").json()
    raw = data["token"].split(":", 1)[1]
    decoded = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    # 密文里不该出现答案明文的痕迹（JSON 片段或数字数组的 ASCII 形式）。
    assert b'"a"' not in decoded
    with pytest.raises(ValueError):
        json.loads(decoded[12:].decode("utf-8"))


def test_register_without_captcha_is_rejected(api):
    response = api.post(REGISTER, json=payload("no_captcha"))
    assert response.status_code == 400
    assert "验证码" in response.json()["detail"]
    api.db.expire_all()
    assert api.db.scalar(select(User.id).where(User.username == "no_captcha")) is None


def test_register_with_wrong_selection_is_rejected(api):
    challenge = issue_challenge()
    wrong = [index for index in range(TILE_COUNT) if index not in challenge.answers]
    response = api.post(REGISTER, json=payload("wrong_pick", captcha_token=challenge.token, captcha_selection=wrong))
    assert response.status_code == 400
    assert "验证码" in response.json()["detail"]
    api.db.expire_all()
    assert api.db.scalar(select(User.id).where(User.username == "wrong_pick")) is None


def test_register_with_solved_captcha_succeeds(api):
    challenge = issue_challenge()
    response = api.post(REGISTER, json=payload("solved_user", **solved(challenge)))
    assert response.status_code == 201, response.text
    assert response.json()["user"]["role"] == "customer"
    api.db.expire_all()
    user = api.db.scalar(select(User).where(User.username == "solved_user"))
    assert api.db.scalar(select(Customer).where(Customer.user_id == user.id)) is not None


def test_captcha_token_is_single_use(api):
    challenge = issue_challenge()
    assert api.post(REGISTER, json=payload("first_use", **solved(challenge))).status_code == 201
    replay = api.post(REGISTER, json=payload("second_use", **solved(challenge)))
    assert replay.status_code == 400
    assert "已使用" in replay.json()["detail"]
    api.db.expire_all()
    assert api.db.scalar(select(User.id).where(User.username == "second_use")) is None


def test_captcha_burns_after_three_wrong_picks(api):
    challenge = issue_challenge()
    wrong = [index for index in range(TILE_COUNT) if index not in challenge.answers]
    for attempt in range(3):
        response = api.post(REGISTER, json=payload(f"burn_{attempt}", captcha_token=challenge.token, captcha_selection=wrong[: 1 + attempt]))
        assert response.status_code == 400, response.text
    assert "次数过多" in response.json()["detail"]
    # 题已经作废：这时候就算全选对也没用，必须换题。
    late = api.post(REGISTER, json=payload("burn_late", **solved(challenge)))
    assert late.status_code == 400
    assert "已使用" in late.json()["detail"]
    # 换一道题照常能注册。
    fresh = issue_challenge()
    assert api.post(REGISTER, json=payload("burn_fresh", **solved(fresh))).status_code == 201


def test_captcha_expires(api):
    expired = issue_challenge(ttl_seconds=-1)
    response = api.post(REGISTER, json=payload("expired_user", **solved(expired)))
    assert response.status_code == 400
    assert "过期" in response.json()["detail"]


def test_tampered_token_is_rejected(api):
    challenge = issue_challenge()
    tampered = challenge.token[:-2] + ("aa" if challenge.token[-2:] != "aa" else "bb")
    response = api.post(REGISTER, json=payload("tampered_user", captcha_token=tampered,
                                               captcha_selection=sorted(challenge.answers)))
    assert response.status_code == 400
    assert "不正确" in response.json()["detail"]


def test_captcha_endpoint_is_rate_limited(api):
    from app.core.config import get_settings

    limit = get_settings().captcha_rate_limit
    for _ in range(limit):
        assert api.get("/api/public/captcha").status_code == 200
    assert api.get("/api/public/captcha").status_code == 429
