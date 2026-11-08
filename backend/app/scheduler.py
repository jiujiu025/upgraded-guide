"""调度进程入口。

    python -m app.scheduler            # 常驻，按注册表间隔执行
    python -m app.scheduler --once     # 立即执行全部任务一次后退出
    python -m app.scheduler --once --task payment_reconcile

容器里由 ``docker-compose.prod.yml`` 的 ``worker`` 服务拉起。
"""

import argparse
import logging
import time

from app.core.config import get_settings
from app.tasks.registry import SPECS, TASKS
from app.tasks.runner import execute

logger = logging.getLogger("app.scheduler")


def run_once(task_name: str | None = None) -> int:
    specs = [TASKS[task_name]] if task_name else list(SPECS)
    failed = 0
    for spec in specs:
        outcome = execute(spec.name, triggered_by="cli")
        if outcome.status != "success":
            failed += 1
    return 1 if failed else 0


def run_loop(poll_seconds: int) -> None:
    due = {spec.name: 0.0 for spec in SPECS}
    logger.info("调度进程启动，任务数=%s，轮询=%ss", len(SPECS), poll_seconds)
    while True:
        monotonic_now = time.monotonic()
        for spec in SPECS:
            if monotonic_now < due[spec.name]:
                continue
            execute(spec.name, triggered_by="scheduler")
            due[spec.name] = time.monotonic() + spec.interval_minutes * 60
        time.sleep(poll_seconds)


def main() -> int:
    parser = argparse.ArgumentParser(description="玖玖云定时任务调度进程")
    parser.add_argument("--once", action="store_true", help="执行一轮后退出")
    parser.add_argument("--task", choices=sorted(TASKS), help="只执行指定任务（配合 --once）")
    parser.add_argument("--poll", type=int, help="轮询间隔秒数，默认取 SCHEDULER_POLL_SECONDS")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = get_settings()

    if args.once:
        return run_once(args.task)

    if args.task:
        parser.error("--task 只能与 --once 一起使用")
    if not settings.scheduler_enabled:
        logger.info("SCHEDULER_ENABLED=false，调度进程不启动")
        return 0

    try:
        run_loop(args.poll or settings.scheduler_poll_seconds)
    except KeyboardInterrupt:
        logger.info("调度进程收到中断，退出")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
