import hashlib
import hmac
import secrets
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import AuthRefreshToken, AuthSession, User
from app.models.base import utcnow


class SessionError(ValueError):
    pass


def _key() -> bytes:
    settings = get_settings()
    secret = settings.session_token_hash_key.get_secret_value() if settings.session_token_hash_key else settings.jwt_secret_key
    return ("refresh:" + secret).encode("utf-8")


def _hash(value: str) -> str:
    return hmac.new(_key(), value.encode("utf-8"), hashlib.sha256).hexdigest()


def csrf_token(session: AuthSession, generation: int | None = None) -> str:
    value = f"csrf:{session.id}:{session.generation if generation is None else generation}"
    return hmac.new(_key(), value.encode("ascii"), hashlib.sha256).hexdigest()


def create_session(db: Session, user: User) -> tuple[AuthSession, str]:
    session = AuthSession(user_id=user.id, auth_version=user.auth_version,
                          generation=0, expires_at=utcnow() + timedelta(days=7))
    db.add(session)
    db.flush()
    raw = secrets.token_urlsafe(32)
    db.add(AuthRefreshToken(session_id=session.id, generation=0, token_hash=_hash(raw)))
    db.flush()
    return session, raw


def find_refresh(db: Session, raw: str | None) -> tuple[AuthSession, AuthRefreshToken, User]:
    if not raw or len(raw) > 256:
        raise SessionError("登录已失效")
    token = db.scalar(select(AuthRefreshToken).where(AuthRefreshToken.token_hash == _hash(raw)))
    if not token:
        raise SessionError("登录已失效")
    session = db.scalar(select(AuthSession).where(AuthSession.id == token.session_id).with_for_update())
    user = db.get(User, session.user_id) if session else None
    if not session or not user:
        raise SessionError("登录已失效")
    return session, token, user


def validate_session(session: AuthSession, user: User) -> None:
    if (session.revoked_at or session.expires_at <= utcnow() or not user.is_active
            or session.auth_version != user.auth_version):
        raise SessionError("登录已失效")


def rotate_session(db: Session, session: AuthSession, token: AuthRefreshToken, user: User) -> str:
    if token.consumed_at or token.generation != session.generation:
        session.revoked_at = utcnow()
        session.revoke_reason = "refresh_replay"
        db.commit()
        raise SessionError("登录已失效")
    validate_session(session, user)
    claimed = db.execute(update(AuthSession).where(AuthSession.id == session.id,
        AuthSession.generation == token.generation, AuthSession.revoked_at.is_(None))
        .values(generation=token.generation + 1, last_used_at=utcnow()))
    if claimed.rowcount != 1:
        session.revoked_at = utcnow()
        session.revoke_reason = "refresh_replay"
        db.commit()
        raise SessionError("登录已失效")
    token.consumed_at = utcnow()
    db.refresh(session)
    raw = secrets.token_urlsafe(32)
    db.add(AuthRefreshToken(session_id=session.id, generation=session.generation, token_hash=_hash(raw)))
    db.commit()
    return raw


def revoke_session(session: AuthSession, reason: str) -> None:
    if not session.revoked_at:
        session.revoked_at = utcnow()
        session.revoke_reason = reason


def revoke_user_sessions(db: Session, user: User, reason: str) -> int:
    result = db.execute(update(AuthSession).where(AuthSession.user_id == user.id,
        AuthSession.revoked_at.is_(None)).values(revoked_at=utcnow(), revoke_reason=reason))
    user.auth_version += 1
    return result.rowcount
