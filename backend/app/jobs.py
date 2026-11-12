"""进程内任务队列。

单机 MVP 足够；换成 RQ/Celery 时，只要把 submit 换成入队、把 runner.run 注册成任务函数，
其余代码（状态机、事件、产物路径）不用动。
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from . import config, runner

_executor = ThreadPoolExecutor(max_workers=config.MAX_JOBS, thread_name_prefix="pipeline")
_lock = threading.Lock()
_running: set[str] = set()


def submit(project_id: str) -> bool:
    with _lock:
        if project_id in _running:
            return False
        _running.add(project_id)
    _executor.submit(_run, project_id)
    return True


def _run(project_id: str) -> None:
    try:
        runner.run(project_id)
    finally:
        with _lock:
            _running.discard(project_id)


def running() -> set[str]:
    with _lock:
        return set(_running)
