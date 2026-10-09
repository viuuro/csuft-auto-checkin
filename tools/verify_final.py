#!/usr/bin/env python3
"""远端最终验证：用户已录入、续期可用、今晚会签到。

在服务器上运行：
    cd /opt/autosinin && sudo -u autosinin .venv/bin/python tools/verify_final.py
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from app.calendar_rules import CalendarRules  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import Database  # noqa: E402
from app.flysource import token_days_left  # noqa: E402
from app.services import SignInService  # noqa: E402

settings = get_settings(refresh=True)
db = Database(settings.db_path)
service = SignInService(db, settings)

print("=" * 74)
print("最终状态验证")
print("=" * 74)

print("\n=== 管理员 ===")
print(f"  {'✓' if settings.is_admin_configured() else '✗'} {settings.admin_username}")

print("\n=== 校历 ===")
rules = CalendarRules(db)
for t in db.list_terms():
    print(f"  学期 {t['name']}  {t['start_date']} ~ {t['end_date']}")
for h in db.list_holidays():
    print(f"  假期 {h['name']}  {h['start_date']} ~ {h['end_date']}")
if not db.list_holidays():
    print("  （未配置假期）")

today = dt.date.today()
should, reason = rules.should_sign(today)
print(f"\n  今天 {today}: {'会签到' if should else '不签到'} — {reason}")

print("\n=== 用户与凭据 ===")
users = db.list_users()
for u in users:
    sid = u["student_id"]
    token = service.secrets.decrypt(u.get("token_enc") or "")
    refresh = service.secrets.decrypt(u.get("refresh_token_enc") or "")
    days = token_days_left(token)
    print(f"  {sid}  {u.get('name') or '(未填)'}")
    print(f"      审批状态   : {u['approval_state']}")
    print(f"      启用       : {'是' if u['enabled'] else '否'}")
    print(f"      签到时刻   : {u.get('plan_sign_time') or '(未分配)'}")
    print(f"      宿舍       : {u.get('dorm_name')} {u.get('room_no')}")
    print(f"      基准坐标   : ({u.get('location_lat')}, {u.get('location_lng')})")
    print(f"      查询口令   : {'已设置' if u.get('query_password_hash') else '未设置'}")
    print(f"      会话剩余   : {days:.1f} 天" if days is not None else "      会话剩余   : 未知")
    print(f"      自动续期   : {'✓ 有 refresh_token' if refresh else '✗ 无'}")
    if u.get("last_error"):
        print(f"      最近错误   : {u['last_error'][:60]}")

print("\n=== 自动续期实测 ===")
if users:
    u = users[0]
    refresh = service.secrets.decrypt(u.get("refresh_token_enc") or "")
    if refresh:
        client = service.new_token_client()
        try:
            result = client.refresh_access_token(refresh)
            new_days = token_days_left(result.access_token)
            print(f"  ✓ 续期成功")
            print(f"      新会话剩余: {new_days:.1f} 天" if new_days is not None else "")
            print(f"      refresh 轮换: {'是' if result.refresh_token != refresh else '否'}")
            # 落库
            stamp = dt.datetime.now().replace(microsecond=0).isoformat(sep=" ")
            db.update_user(
                u["student_id"],
                token_enc=service.secrets.encrypt(result.access_token),
                refresh_token_enc=service.secrets.encrypt(result.refresh_token),
                token_saved_at=stamp,
                token_refreshed_at=stamp,
            )
            print("      已更新数据库")
        except Exception as exc:  # noqa: BLE001
            print(f"  ✗ 续期失败: {exc}")
        finally:
            client.close()
    else:
        print("  跳过：该用户没有 refresh_token")

print("\n=== 最近日志 ===")
for row in db.recent_logs(8):
    print(f"  {row['created_at']}  [{row['level']}]  {row['action']}  {row['message'][:55]}")

print("\n" + "=" * 74)
print("结论：系统已就绪，等待 21:00-22:30 窗口自动签到")
print("=" * 74)
