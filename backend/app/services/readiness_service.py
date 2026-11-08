"""就绪探针：把"进程活着"和"现在能把活干好"分开。

``/healthz`` 只回答前者，编排层据此重启容器；``/readyz`` 回答后者，DB 打不通、
共享限流 Redis 打不通、数据库结构落后于代码，都必须让流量被摘走——这三种故障
重启容器都治不了，只会把故障时间拉长。

探针的价值在于"每 30 秒真敲一次"，所以 DB 和 Redis 每次都真连，不做缓存；
只有迁移版本要读磁盘上的脚本目录，属于部署期常量，才用 ``lru_cache`` 缓存。
"""

import logging
from functools import lru_cache
from pathlib import Path
from time import perf_counter

from alembic.config import Config
from alembic.script import ScriptDirectory
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.db import SessionLocal

logger = logging.getLogger(__name__)

# 探针结果只有这四种：ok 正常、warn 有问题但不该摘流量、fail 必须摘流量、
# skipped 该环境没有这项依赖（例如内存限流就没有 Redis）。
OK = "ok"
WARN = "warn"
FAIL = "fail"
SKIPPED = "skipped"


def _database_check(db: Session) -> dict:
    started = perf_counter()
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        # 只回异常类型：psycopg 的报错里带主机名和用户名，探针是公开端点。
        logger.warning("readyz 数据库检查失败: %s", type(exc).__name__)
        return {"status": FAIL, "detail": type(exc).__name__}
    return {"status": OK, "latency_ms": round((perf_counter() - started) * 1000, 1)}


def _redis_check() -> dict:
    settings = get_settings()
    if settings.rate_limit_backend != "redis":
        return {"status": SKIPPED, "detail": "限流使用内存后端，无 Redis 依赖"}
    started = perf_counter()
    client = Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)
    try:
        client.ping()
    except RedisError as exc:
        logger.warning("readyz Redis 检查失败: %s", type(exc).__name__)
        return {"status": FAIL, "detail": type(exc).__name__}
    finally:
        client.close()
    return {"status": OK, "latency_ms": round((perf_counter() - started) * 1000, 1)}


def _backend_root() -> Path:
    # app/services/readiness_service.py -> app/services -> app -> backend
    return Path(__file__).resolve().parents[2]


@lru_cache(maxsize=1)
def migration_heads() -> tuple[str, ...] | None:
    """返回脚本目录里的 head 版本号；镜像里没有 alembic 目录时返回 None。"""
    ini_path = _backend_root() / "alembic.ini"
    if not ini_path.exists():
        return None
    config = Config(str(ini_path))
    # alembic.ini 里 script_location 是相对路径，进程 cwd 不一定是 backend，
    # 这里显式覆盖成绝对路径，避免在容器里被解析到错误目录。
    config.set_main_option("script_location", str(ini_path.parent / "alembic"))
    try:
        return tuple(ScriptDirectory.from_config(config).get_heads())
    except Exception as exc:  # 脚本目录损坏不该让整个探针 500
        logger.warning("readyz 读取迁移脚本失败: %s", type(exc).__name__)
        return None


def _migration_check(db: Session) -> dict:
    heads = migration_heads()
    if heads is None:
        return {"status": WARN, "detail": "镜像内无迁移脚本，无法比对版本"}

    try:
        rows = db.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
    except SQLAlchemyError:
        db.rollback()
        # create_all 建出来的库没有版本表：开发/测试环境只提示，生产环境必须拦。
        blocking = get_settings().environment == "production"
        return {
            "status": FAIL if blocking else WARN,
            "detail": "数据库没有迁移版本表，可能未执行 alembic upgrade head",
            "head": heads[0] if heads else None,
        }

    current = sorted(rows)
    if current and set(current) == set(heads):
        return {"status": OK, "current": current[0] if len(current) == 1 else current, "head": heads[0] if heads else None}
    return {
        "status": FAIL,
        "detail": "数据库结构版本与代码不一致，先执行 alembic upgrade head",
        "current": current or None,
        "head": heads[0] if heads else None,
    }


def readiness_report(session_factory: sessionmaker | None = None) -> tuple[dict, bool]:
    """返回 (响应体, 是否就绪)。任何 fail 都视为不就绪。"""
    factory = session_factory or SessionLocal
    checks: dict[str, dict] = {}
    with factory() as db:
        checks["database"] = _database_check(db)
        # 数据库都打不通时不再查迁移版本，否则报错会盖掉真正的根因。
        checks["migrations"] = (
            {"status": SKIPPED, "detail": "数据库不可用"} if checks["database"]["status"] == FAIL else _migration_check(db)
        )
    checks["redis"] = _redis_check()

    ready = all(check["status"] != FAIL for check in checks.values())
    return {
        "status": "ready" if ready else "degraded",
        "service": "ops-platform",
        "checks": checks,
    }, ready
