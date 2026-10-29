"""通过服务器交互终端恢复现有超级管理员账号。"""

import getpass
import re
import sys

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.core.security import hash_password
from app.db import SessionLocal, engine
from app.models import User
from app.services.audit_service import write_audit
from app.services.session_service import revoke_user_sessions


def _validate_password(password: str) -> None:
    if password.lower() in {"admin123", "password123", "change-this-in-production"}:
        raise ValueError("禁止使用默认密码")
    if not 12 <= len(password) or len(password.encode("utf-8")) > 72 or not all(
        re.search(pattern, password) for pattern in (r"[a-z]", r"[A-Z]", r"\d", r"[^A-Za-z0-9]")
    ):
        raise ValueError("密码必须至少 12 字符、至多 72 字节，包含大小写字母、数字和特殊字符")


def recover_super_admin(username: str, password: str) -> None:
    settings = get_settings()
    if settings.environment != "production" or engine.dialect.name != "postgresql":
        raise ValueError("恢复命令只允许在生产 PostgreSQL 环境执行")
    if settings.admin_password:
        raise ValueError("生产环境禁止通过 ADMIN_PASSWORD 注入密码")
    if not re.fullmatch(r"[a-z][a-z0-9_.-]{2,79}", username):
        raise ValueError("用户名格式无效")
    _validate_password(password)

    with SessionLocal() as db:
        with db.begin():
            db.execute(text("SELECT pg_advisory_xact_lock(99009902)"))
            user = db.scalar(select(User).where(User.username == username))
            if not user or user.role != "super_admin" or not user.is_active:
                raise ValueError("未找到启用中的超级管理员账号")

            user.password_hash = hash_password(password)
            user.must_change_password = True
            revoked = revoke_user_sessions(db, user, "super_admin_password_recovery")
            write_audit(
                db,
                user,
                "super_admin_password_recovery",
                "user",
                user.id,
                f"服务器控制台恢复；已撤销会话数={revoked}；下次登录必须修改密码",
            )


def main() -> int:
    try:
        if not sys.stdin.isatty():
            raise ValueError("必须通过交互终端运行，不接受管道或命令行密码")
        username = input("超级管理员用户名: ").strip().lower()
        password = getpass.getpass("新密码: ")
        confirmation = getpass.getpass("再次输入新密码: ")
        if password != confirmation:
            raise ValueError("两次密码不一致")
        recover_super_admin(username, password)
    except ValueError as exc:
        print(f"密码恢复失败: {exc}", file=sys.stderr)
        return 1
    except SQLAlchemyError:
        print("密码恢复失败: 数据库操作失败，请检查数据库连接", file=sys.stderr)
        return 1
    print("超级管理员密码已重置；下次登录必须修改密码。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
