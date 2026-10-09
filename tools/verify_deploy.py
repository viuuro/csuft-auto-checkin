#!/usr/bin/env python3
"""远端配置体检：确认重新部署后管理员、校历、用户数据都还在。

在服务器上运行：
    cd /opt/autosinin && sudo -u autosinin .venv/bin/python tools/verify_deploy.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from app.config import get_settings  # noqa: E402
from app.db import Database  # noqa: E402

settings = get_settings(refresh=True)
db = Database(settings.db_path)

print("=== 配置 ===")
print(f"  站点      : {settings.site_name}")
print(f"  数据目录  : {settings.data_dir}")
print(f"  时区      : {settings.timezone}")
print(f"  签到窗口  : {settings.sign_window_start} - {settings.sign_window_end}")
print(f"  调度启用  : {settings.scheduler_enabled}")

print("\n=== 管理员 ===")
if settings.is_admin_configured():
    print(f"  ✓ 已配置，用户名 = {settings.admin_username}")
    print(f"  口令哈希前 12 位 = {settings.admin_password_hash[:12]}…")
else:
    print("  ✗ 未配置！需要重新设置")

print("\n=== 数据 ===")
terms = db.list_terms()
holidays = db.list_holidays()
users = db.list_users()
signable = db.list_signable_users()

print(f"  学期  : {len(terms)} 个")
for t in terms:
    print(f"          {t['name']}  {t['start_date']} ~ {t['end_date']}")
print(f"  假期  : {len(holidays)} 个")
for h in holidays:
    print(f"          {h['name']}  {h['start_date']} ~ {h['end_date']}  ({h.get('kind')})")
print(f"  用户  : {len(users)} 位（可自动签到 {len(signable)} 位）")
for u in users:
    print(
        f"          {u['student_id']}  {u.get('name') or '(未填)'}"
        f"  {u['approval_state']}  时刻={u.get('plan_sign_time') or '-'}"
    )

print("\n=== 今日判定 ===")
from app.calendar_rules import CalendarRules  # noqa: E402
import datetime as dt  # noqa: E402

should, reason = CalendarRules(db).should_sign(dt.date.today())
print(f"  {dt.date.today()} : {'会签到' if should else '不签到'} — {reason}")

print("\n=== 最近日志 ===")
for row in db.recent_logs(8):
    print(f"  {row['created_at']}  [{row['level']}]  {row['action']}  {row['message'][:60]}")
