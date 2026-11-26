from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jwt import InvalidTokenError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import decode_access_token
from app.db import get_db
from app.models import AuthSession, User
from app.services.impersonation_service import (
    BLOCKED_PATHS as IMPERSONATION_BLOCKED_PATHS,
    TOKEN_PREFIX as IMPERSONATION_PREFIX,
    resolve as resolve_impersonation,
)
from app.services.session_service import SessionError, validate_session

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def current_user(request: Request, token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    # 模拟登录走独立 token：身份按被模拟者算，审计按发起人记（见 impersonation_service）
    if token.startswith(IMPERSONATION_PREFIX):
        try:
            _record, target = resolve_impersonation(db, token)
        except SessionError as exc:
            raise HTTPException(status_code=401, detail="模拟登录已失效") from exc
        # 身份类操作照旧拦住：借客户的壳改掉他的密码，客户就再也登不进来了
        if request.url.path in IMPERSONATION_BLOCKED_PATHS:
            raise HTTPException(status_code=403, detail="模拟登录不能修改被模拟账号的密码或密保")
        return target
    try:
        payload = decode_access_token(token)
        user_id = int(payload["sub"])
        session_id = int(payload["sid"])
        if payload.get("token_type") != "access":
            raise ValueError("令牌类型无效")
    except (InvalidTokenError, KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效") from exc
    user = db.scalar(select(User).where(User.id == user_id, User.is_active.is_(True)))
    if not user:
        raise HTTPException(status_code=401, detail="账号不可用")
    session = db.get(AuthSession, session_id)
    try:
        if not session or session.user_id != user.id or payload.get("av") != user.auth_version:
            raise SessionError("登录已失效")
        validate_session(session, user)
    except SessionError as exc:
        raise HTTPException(status_code=401, detail="登录已失效") from exc
    # 管理员重置密码 / 首次登录：除认证类接口外一律拦住，必须先改密。
    # 只放行 /api/auth/*（改密、登出、会话、密保找回都在这个命名空间下），
    # 否则用户可以拿着临时密码继续做业务操作，"强制改密"就是一句空话。
    if user.must_change_password and not request.url.path.startswith("/api/auth/"):
        raise HTTPException(status_code=428, detail="首次登录或密码已被重置，请先修改密码")
    return user


def require_roles(*roles: str):
    def dependency(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="没有执行此操作的权限")
        return user

    return dependency
