"""校历判定：决定某一天是否需要自动签到。

规则（按用户需求）：仅在学校校历的学期内、且不属于寒暑假等假期区间时执行，
并且跳过周末之外的其它非上课日由管理员通过假期区间自行控制。
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
        """返回 ``(是否应签到, 原因说明)``。

        判定顺序：先看是否有"调休上课"（优先级最高，表示当天照常签到），
        再判断是否在学期内，最后排除假期区间。
        """
        if not self.has_terms():
            return False, "未配置学期，请管理员在“校历管理”中添加学期区间"

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
