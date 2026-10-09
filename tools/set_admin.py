#!/usr/bin/env python3
"""命令行运维工具。

用法::

    python tools/set_admin.py set-password --username admin       # 交互式改密码
    python tools/set_admin.py hash --password 'xxx'               # 只生成哈希（填到 .env）
    python tools/set_admin.py list-users
    python tools/set_admin.py approve 20230101
    python tools/set_admin.py run-daily [YYYY-MM-DD]
    python tools/set_admin.py stats
"""

from __future__ import annotations

import argparse
import datetime as dt
import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from app.config import get_settings, persist  # noqa: E402
from app.db import Database, parse_date  # noqa: E402
from app.security import hash_password  # noqa: E402
from app.services import SignInService  # noqa: E402


def cmd_hash(args: argparse.Namespace) -> int:
    print(hash_password(args.password))
    return 0


def cmd_set_password(args: argparse.Namespace) -> int:
    settings = get_settings(refresh=True)
    username = args.username or settings.admin_username or "admin"
    password = args.password or getpass.getpass("新密码（不回显）: ")
    if len(password) < 8:
        print("密码至少 8 位", file=sys.stderr)
        return 1
    if not args.password:
        again = getpass.getpass("再输入一次: ")
        if again != password:
            print("两次输入不一致", file=sys.stderr)
            return 1

    persist("admin_username", username)
    persist("admin_password_hash", hash_password(password))
    print(f"已设置管理员：{username}")
    print("提示：请重启服务以使其生效（或直接使用 .env 中的 ADMIN_* 变量）。")
    return 0


def cmd_list_users(args: argparse.Namespace) -> int:
    db = Database(get_settings(refresh=True).db_path)
    users = db.list_users(args.state)
    if not users:
        print("暂无用户")
        return 0
    print(f"{'学号':<14}{'姓名':<10}{'状态':<10}{'启用':<6}{'签到时刻':<10}{'宿舍':<20}最近错误")
    print("-" * 100)
    for u in users:
        dorm = f"{u.get('dorm_name') or '-'} {u.get('room_no') or ''}".strip()
        print(
            f"{u.get('student_id',''):<14}"
            f"{(u.get('name') or '-'):<10}"
            f"{u.get('approval_state',''):<10}"
            f"{'是' if u.get('enabled') else '否':<6}"
            f"{(u.get('plan_sign_time') or '-'):<10}"
            f"{dorm:<20}"
            f"{(u.get('last_error') or '')[:30]}"
        )
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    settings = get_settings(refresh=True)
    db = Database(settings.db_path)
    service = SignInService(db, settings)
    service.approve(args.student_id, "cli")
    print(f"已批准 {args.student_id}")
    return 0


def cmd_run_daily(args: argparse.Namespace) -> int:
    settings = get_settings(refresh=True)
    db = Database(settings.db_path)
    service = SignInService(db, settings)
    day = parse_date(args.date) if args.date else dt.date.today()
    result = service.run_daily(day, force=args.force)
    if result.get("skipped"):
        print(f"{result['date']} 跳过：{result['reason']}")
        return 0
    print(f"{result['date']} 完成：成功 {result['success']}/{result['total']}")
    for row in result["results"]:
        mark = "OK " if row["status"] == "success" else "-- "
        print(f"  {mark}{row['studentId']}  {row['message']}")
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    settings = get_settings(refresh=True)
    db = Database(settings.db_path)
    stats = db.stats(dt.date.today())
    print(f"站点：{settings.site_name}")
    print(f"签到窗口：{settings.sign_window_start} - {settings.sign_window_end}（{settings.timezone}）")
    print(f"成员总数：{stats['totalUsers']}")
    print(f"已批准：{stats['approvedUsers']}")
    print(f"待审批：{stats['pendingUsers']}")
    print(f"今日成功：{stats['successToday']}")
    print(f"今日失败：{stats['failedToday']}")
    print(f"数据库：{settings.db_path}")

    from app.calendar_rules import CalendarRules

    should, reason = CalendarRules(db).should_sign(dt.date.today())
    print(f"今日是否自动签到：{'是' if should else '否'} — {reason}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="自动签到服务运维工具")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("hash", help="生成口令哈希")
    p.add_argument("--password", required=True)
    p.set_defaults(func=cmd_hash)

    p = sub.add_parser("set-password", help="设置/重置管理员密码")
    p.add_argument("--username", default="")
    p.add_argument("--password", default="")
    p.set_defaults(func=cmd_set_password)

    p = sub.add_parser("list-users", help="列出用户")
    p.add_argument("--state", default="", choices=["", "pending", "approved", "rejected"])
    p.set_defaults(func=cmd_list_users)

    p = sub.add_parser("approve", help="批准某个学号")
    p.add_argument("student_id")
    p.set_defaults(func=cmd_approve)

    p = sub.add_parser("run-daily", help="手动执行一轮签到")
    p.add_argument("date", nargs="?", default="")
    p.add_argument("--force", action="store_true", help="忽略校历判定")
    p.set_defaults(func=cmd_run_daily)

    p = sub.add_parser("stats", help="查看统计")
    p.set_defaults(func=cmd_stats)

    args = parser.parse_args()
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
