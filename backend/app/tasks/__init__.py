"""定时任务包。

调度进程（``python -m app.scheduler``）按间隔执行 ``registry.TASKS`` 中的任务；
接口层只读取执行流水，不直接跑任务（手动触发除外）。
"""
