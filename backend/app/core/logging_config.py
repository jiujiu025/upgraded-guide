"""统一的应用日志初始化。

uvicorn 只配置它自己的 logger，应用模块的 ``logging.getLogger(__name__)`` 默认
走 root 的 lastResort（WARNING 以上才输出），INFO 级别的业务日志会直接消失。
这里在启动时补一次 root 配置，让 ``app.*`` 的日志在容器里真的能看到。
"""

import logging

from app.core.config import get_settings

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def configure_logging() -> None:
    settings = get_settings()
    # 生产环境只留 INFO，避免 DEBUG 里带出请求体或凭据；其余环境放开便于本地排查。
    level = logging.INFO if settings.environment == "production" else logging.DEBUG
    # basicConfig 在 root 已有 handler 时是空操作，不会和 uvicorn 或 pytest 抢输出。
    logging.basicConfig(level=level, format=LOG_FORMAT)
    logging.getLogger().setLevel(level)
