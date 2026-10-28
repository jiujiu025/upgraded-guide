from datetime import datetime, timedelta, timezone

import jwt
import bcrypt

from app.core.config import get_settings

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def create_access_token(user_id: int, role: str, session_id: int | None = None, auth_version: int = 0) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=settings.jwt_expire_minutes)
    from uuid import uuid4
    return jwt.encode({"sub": str(user_id), "sid": session_id, "av": auth_version, "jti": uuid4().hex,
                       "iat": now, "exp": expires, "iss": "jiujiuyun", "aud": "jiujiuyun-api",
                       "token_type": "access"}, settings.jwt_secret_key, algorithm="HS256")


def decode_access_token(token: str) -> dict:
    settings = get_settings()
    return jwt.decode(token, settings.jwt_secret_key, algorithms=["HS256"],
                      audience="jiujiuyun-api", issuer="jiujiuyun")
