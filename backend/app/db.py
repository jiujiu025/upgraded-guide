from collections.abc import Generator
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

settings = get_settings()


def build_engine_kwargs(database_url: str) -> dict[str, Any]:
    """SQLite 与 PostgreSQL 用两套完全不同的连接参数。

    生产用 PostgreSQL 时这里有三个必须显式配置的项：

    - ``pool_pre_ping``：数据库重启、云厂商掐空闲连接后，池里的连接已经死了但
      SQLAlchemy 不知道，**下一个请求必然失败一次**。pre_ping 在出池时先探活，
      代价是一次极轻的往返，换来不会有"偶发 500"。
    - ``pool_recycle``：小于中间设备（LB / pgbouncer / 云数据库）的空闲超时，
      主动换掉老连接，避免被对端直接 RST。
    - ``connect_args={"connect_timeout": ...}``：不加的话数据库不可达时连接会挂到
      内核 TCP 超时（分钟级），请求线程全被占死，健康检查也跟着超时。

    SQLite 是单文件，没有连接失效问题，传这些参数反而会报错。
    """
    if database_url.startswith("sqlite"):
        return {"connect_args": {"check_same_thread": False}, "future": True}
    return {
        "future": True,
        "pool_pre_ping": True,
        "pool_size": settings.db_pool_size,
        "max_overflow": settings.db_max_overflow,
        "pool_recycle": settings.db_pool_recycle_seconds,
        "connect_args": {"connect_timeout": settings.db_connect_timeout_seconds},
    }


engine = create_engine(settings.database_url, **build_engine_kwargs(settings.database_url))
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
