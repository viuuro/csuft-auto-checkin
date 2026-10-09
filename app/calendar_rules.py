"""校历判定：标记某一天在**本地校历**上是否属于学期内。

⚠️ 重要：本模块**不再**决定是否执行自动签到。

是否要打卡由**学校任务**的 ``taskStartDate`` / ``taskEndDate`` /
``signWeek`` 决定（见 ``SignTask.available_on()``）。原因是学校任务的
打卡期间会覆盖寒暑假（寒假留校学生仍需打卡），而本地学期区间通常只到
学期末 —— 早先版本把本地学期当硬门禁，导致那段时间**静默漏签**。

本地校历现在的用途：

* 日历看板的着色与「假期无需签到」提示
* 管理端的运行观察
* 假期里学校没有任务时，``_resolve_task`` 本就返回 None，不会误签

判定规则保持不变：调休上课优先，其次学期内，最后排除假期区间。
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from .db import Database, parse_date


class CalendarRules:
    """基于 ``terms`` / ``holidays`` 两张表的判定器。"""

    def __init__(self, db: Database) -> None:
        self.db = db

    def term_for(self, day: date) -> dict[str, Any] | None:
        rows = self.db.terms_on(day)
        return rows[0] if rows else None

    def holiday_for(self, day: date) -> dict[str, Any] | None:
        """返回命中的假期区间；``kind='makeup'``（调休上课）不算假期。"""
        for row in self.db.holidays_on(day):
            if str(row.get("kind") or "vacation") == "makeup":
                continue
            return row
        return None

    def makeup_for(self, day: date) -> dict[str, Any] | None:
        for row in self.db.holidays_on(day):
            if str(row.get("kind") or "") == "makeup":
                return row
        return None

    def has_terms(self) -> bool:
        row = self.db.query_one("SELECT COUNT(*) AS n FROM terms")
        return bool(row and int(row["n"]) > 0)

    def should_sign(self, day: date) -> tuple[bool, str]:
        """返回 ``(本地校历上是否为学期内, 原因说明)``。

        ⚠️ 这个结果**只用于展示与观察**，不再作为自动签到的门禁。
        真正的判定依据是学校任务的打卡期间。

        判定顺序：先看是否有"调休上课"（优先级最高，表示当天照常签到），
        再判断是否在学期内，最后排除假期区间。
        """
        if not self.has_terms():
            return False, "未配置学期（不影响自动签到，仅日历着色）"

        term = self.term_for(day)
        if term is None:
            return False, "不在已配置的学期区间内（寒/暑假或学期外）"

        makeup = self.makeup_for(day)
        if makeup is not None:
            return True, f"调休上课：{makeup.get('name') or '调休'}"

        holiday = self.holiday_for(day)
        if holiday is not None:
            return False, f"假期中：{holiday.get('name') or '假期'}"

        return True, f"学期内：{term.get('name') or '学期'}"

    def describe_month(self, year: int, month: int) -> dict[str, str]:
        """返回该月每天的判定结果，供日历着色。"""
        first = date(year, month, 1)
        if month == 12:
            last = date(year, 12, 31)
        else:
            last = date(year, month + 1, 1) - timedelta(days=1)

        result: dict[str, str] = {}
        day = first
        while day <= last:
            should, _reason = self.should_sign(day)
            if not should:
                should = False
            result[day.isoformat()] = "sign" if should else "skip"
            day += timedelta(days=1)
        return result

    def next_signable_day(self, start: date, horizon_days: int = 60) -> date | None:
        day = start
        for _ in range(horizon_days):
            should, _ = self.should_sign(day)
            if should:
                return day
            day += timedelta(days=1)
        return None


def summarize_terms(db: Database) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in db.list_terms():
        item = dict(row)
        start = parse_date(row.get("start_date", ""))
        end = parse_date(row.get("end_date", ""))
        item["days"] = (end - start).days + 1 if start and end else 0
        out.append(item)
    return out


def summarize_holidays(db: Database) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in db.list_holidays():
        item = dict(row)
        start = parse_date(row.get("start_date", ""))
        end = parse_date(row.get("end_date", ""))
        item["days"] = (end - start).days + 1 if start and end else 0
        out.append(item)
    return out
