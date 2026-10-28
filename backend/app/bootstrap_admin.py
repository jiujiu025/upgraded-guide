"""生产环境的一次性超级管理员初始化入口。"""

import getpass
import re
import sys

from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.core.security import hash_password
from app.db import SessionLocal, engine
from app.models import User
from app.services.audit_service import write_audit


def initialize_admin(username: str, password: str) -> int:
    settings = get_settings()
    if settings.environment != "production" or engine.dialect.name != "postgresql":
        raise ValueError("初始化只允许在生产 PostgreSQL 环境执行")
    if settings.admin_password:
        raise ValueError("生产环境禁止通过 ADMIN_PASSWORD 注入初始密码")
    if not re.fullmatch(r"[a-z][a-z0-9_.-]{2,79}", username):
        raise ValueError("用户名格式无效")
    if not 12 <= len(password) or len(password.encode("utf-8")) > 72 or not all(re.search(pattern, password) for pattern in
                                                 (r"[a-z]", r"[A-Z]", r"\d", r"[^A-Za-z0-9]")):
        raise ValueError("密码必须至少 12 字符、至多 72 字节，包含大小写字母、数字和特殊字符")
    if password.lower() in {"admin123", "password123", "change-this-in-production"}:
        raise ValueError("禁止使用默认密码")

    with SessionLocal() as db:
        with db.begin():
            # 事务级锁让并发初始化串行化，失败时数据库自动回滚。
            db.execute(text("SELECT pg_advisory_xact_lock(99009901)"))
            if db.scalar(select(func.count()).select_from(User)):
                raise ValueError("用户表非空，拒绝重复初始化")
            actor = User(username=username, password_hash=hash_password(password), role="super_admin",
                         display_name="超级管理员", is_active=True, must_change_password=True)
            db.add(actor)
            db.flush()
            write_audit(db, actor, "bootstrap_super_admin", "user", actor.id, "首次登录必须修改密码")
            return actor.id


def main() -> int:
    try:
        settings = get_settings()
        if settings.environment != "production" or engine.dialect.name != "postgresql":
            raise ValueError("初始化只允许在生产 PostgreSQL 环境执行")
        if not sys.stdin.isatty():
            raise ValueError("必须通过交互终端输入，不接受管道或命令行密码")
        username = input("超级管理员用户名: ").strip().lower()
        password = getpass.getpass("初始密码: ")
        confirmation = getpass.getpass("再次输入初始密码: ")
        if password != confirmation:
            raise ValueError("两次密码不一致")
        initialize_admin(username, password)
    except ValueError as exc:
        print(f"初始化失败: {exc}", file=sys.stderr)
        return 1
    except SQLAlchemyError:
        print("初始化失败: 数据库操作失败，请检查迁移与连接状态", file=sys.stderr)
        return 1
    print("超级管理员已初始化；首次登录必须修改密码。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
