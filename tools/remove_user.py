#!/usr/bin/env python3
"""从服务器移除指定的签到用户（保留管理员与校历）。

在服务器上运行：
    cd /opt/autosinin && sudo -u autosinin .venv/bin/python tools/remove_user.py 20240001
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from app.config import get_settings  # noqa: E402
from app.db import Database  # noqa: E402
from app.flysource import token_days_left  # noqa: E402
from app.services import SignInService  # noqa: E402

if len(sys.argv) < 2:
    print("用法: remove_user.py <学号> [<学号> ...]")
    raise SystemExit(2)

settings = get_settings(refresh=True)
db = Database(settings.db_path)
service = SignInService(db, settings)

print("=" * 70)
print("移除签到用户")
print("=" * 70)

for student_id in sys.argv[1:]:
    user = db.get_user(student_id)
    if not user:
        print(f"\n  {student_id}：不存在，跳过")
        continue

    token = service.secrets.decrypt(user.get("token_enc") or "")
    days = token_days_left(token)
    print(f"\n  {student_id}  {user.get('name') or '(未填姓名)'}")
    print(f"      签到时刻 : {user.get('plan_sign_time') or '-'}")
    print(f"      会话剩余 : {days:.1f} 天" if days is not None else "      会话剩余 : 未知")

    records = db.query(
        "SELECT COUNT(*) AS n FROM sign_records WHERE student_id = ?", (student_id,)
    )
    n_records = int(records[0]["n"]) if records else 0
    print(f"      签到记录 : {n_records} 条")

    # 删除凭据（避免残留可用的会话）
    db.update_user(
        student_id,
        token_enc="",
        refresh_token_enc="",
        credential_enc="",
        credential_ok=0,
    )
    print("      ✓ 凭据已清除（会话不再可用）")

    # 删除用户与其签到记录
    db.delete_user(student_id)
    print(f"      ✓ 已移除用户及 {n_records} 条签到记录")

    db.log("user_removed", f"按管理员要求移除签到用户 {student_id}", level="warn")

print("\n" + "=" * 70)
remaining = db.list_signable_users()
print(f"剩余可自动签到用户：{len(remaining)} 位")
for u in remaining:
    print(f"  {u['student_id']}  {u.get('name') or '(未填)'}  {u.get('plan_sign_time')}")
if not remaining:
    print("  （无 —— 今晚不会执行任何签到）")
print("=" * 70)
