"""自动签到调度器。

策略（对应用户需求"每天的 21:00-22:30 之间的随机时间进行自动签到"）：

* 每天在签到窗口开始时唤醒一次，挑出当天的签到日；
* 任务按分钟粒度轮询（窗口只有 90 分钟，轮询开销可忽略），
  到点即执行，因此每位用户的实际签到时刻就是其 ``plan_sign_time``；
* 该时刻在绑定/随机化时于窗口内随机生成，保证互不相同且不可预测；
* 非学期内或假期自动跳过；
* 窗口结束前对失败记录做一次补试。
"""

from __future__ import annotations

import datetime as dt
import logging
import threading
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from .config import Settings
from .db import Database, parse_time
from .services import SignInService

log = logging.getLogger("autosinin.scheduler")

JOB_TICK = "autosinin.tick"
JOB_RETRY = "autosinin.retry"
JOB_SYNC = "autosinin.sync"


class SignScheduler:
    """把 ``SignInService`` 挂到 APScheduler 上。"""

    def __init__(self, db: Database, settings: Settings, service: SignInService) -> None:
        self.db = db
        self.settings = settings
        self.service = service
        self.scheduler = BackgroundScheduler(
            timezone=settings.timezone,
            job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 300},
        )
        self._run_lock = threading.Lock()
        self._last_tick: str = ""

    # ------------------------------------------------------------------ #

    def start(self) -> None:
        if not self.settings.scheduler_enabled:
            log.warning("调度器已通过配置关闭（SCHEDULER_DISABLED=1）")
            return
        if self.scheduler.running:
            return

        window_start = parse_time(self.settings.sign_window_start) or dt.time(21, 0)
        window_end = parse_time(self.settings.sign_window_end) or dt.time(22, 30)

        # 窗口内每分钟检查一次
        self.scheduler.add_job(
            self.tick,
            IntervalTrigger(minutes=1),
            id=JOB_TICK,
            replace_existing=True,
            next_run_time=dt.datetime.now() + dt.timedelta(seconds=5),
        )

        # 窗口结束前 10 分钟做一次失败补试
        retry_at = (
            dt.datetime.combine(dt.date.today(), window_end) - dt.timedelta(minutes=10)
        ).time()
        self.scheduler.add_job(
            self.retry_job,
            CronTrigger(hour=retry_at.hour, minute=retry_at.minute),
            id=JOB_RETRY,
            replace_existing=True,
        )

        # 每天 23:30 从服务端同步当天真实结果，校准日历
        self.scheduler.add_job(
            self.sync_job,
            CronTrigger(hour=23, minute=30),
            id=JOB_SYNC,
            replace_existing=True,
        )

        self.scheduler.start()
        log.info(
            "调度器已启动：窗口 %s-%s，时区 %s",
            window_start.strftime("%H:%M"),
            window_end.strftime("%H:%M"),
            self.settings.timezone,
        )
        self.db.log(
            "scheduler_start",
            f"调度器启动，签到窗口 {self.settings.sign_window_start}-{self.settings.sign_window_end}",
        )

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            log.info("调度器已停止")

    @property
    def running(self) -> bool:
        return bool(self.scheduler.running)

    def in_window(self, now: dt.datetime | None = None) -> bool:
        moment = (now or dt.datetime.now()).time()
        start = parse_time(self.settings.sign_window_start) or dt.time(21, 0)
        end = parse_time(self.settings.sign_window_end) or dt.time(22, 30)
        return start <= moment <= end

    # ------------------------------------------------------------------ #

    def tick(self) -> None:
        """每分钟检查是否有用户到点。"""
        now = dt.datetime.now()
        stamp = now.strftime("%Y-%m-%d %H:%M")
        if stamp == self._last_tick:
            return
        self._last_tick = stamp

        if not self.in_window(now):
            return
        if not self._run_lock.acquire(blocking=False):
            log.debug("上一轮签到仍在执行，跳过本次 tick")
            return
        try:
            due = self.service.due_users(now)
            if not due:
                return
            log.info("到点用户 %d 位：%s", len(due), [u.get("student_id") for u in due])
            for user in due:
                self.service.sign_for_user(user, day=now.date(), source="auto")
        except Exception:  # noqa: BLE001 - 调度线程必须存活
            log.exception("tick 执行失败")
        finally:
            self._run_lock.release()

    def retry_job(self) -> None:
        if not self.in_window():
            return
        try:
            result = self.service.retry_failed(days=1)
            if result["retried"]:
                log.info("补试完成，共 %d 条", result["retried"])
        except Exception:  # noqa: BLE001
            log.exception("补试任务失败")

    def sync_job(self) -> None:
        """从服务端同步当天结果，修正本地记录。"""
        today = dt.date.today()
        should, _ = self.service.calendar.should_sign(today)
        if not should:
            return
        users = self.db.list_signable_users()
        for user in users:
            student_id = str(user.get("student_id") or "")
            try:
                self.service.sync_month_from_server(student_id, today.year, today.month)
            except Exception as exc:  # noqa: BLE001
                log.warning("同步 %s 失败：%s", student_id, exc)

    # ------------------------------------------------------------------ #

    def jobs_status(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if not self.scheduler.running:
            return out
        for job in self.scheduler.get_jobs():
            out.append(
                {
                    "id": job.id,
                    "nextRun": job.next_run_time.strftime("%Y-%m-%d %H:%M:%S")
                    if job.next_run_time
                    else "",
                }
            )
        return out
