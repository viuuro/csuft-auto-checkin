#!/usr/bin/env python3
"""部署「以学校任务期间为准」的判定改动。"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

_spec = importlib.util.spec_from_file_location(
    "deploy_remote", str(ROOT / "tools" / "deploy_remote.py")
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
Remote = _mod.Remote

HOST = os.environ.get("ASI_HOST", "")
USER = os.environ.get("ASI_SSH_USER", "root")
REMOTE = os.environ.get("ASI_REMOTE_DIR", "/opt/autosinin")
PASSWORD = os.environ.get("ASI_SSH_PASSWORD", "")

if not HOST or not PASSWORD:
    print("请先设置 ASI_HOST 与 ASI_SSH_PASSWORD")
    raise SystemExit(1)

FILES = [
    "app/flysource.py",
    "app/services.py",
    "app/calendar_rules.py",
    "app/static/admin.js",
    "tools/test_e2e.py",
    "tools/test_units.py",
    "tools/check_requirements.py",
    "tools/deploy_remote.py",
    "README.md",
    "docs/DEPLOY.md",
]

remote = Remote(HOST, USER, "", PASSWORD)
try:
    print("=" * 74)
    print("1. 上传改动")
    print("=" * 74)
    for rel in FILES:
        code, _, err = remote.scp(ROOT / rel, f"{REMOTE}/{rel}")
        print(f"  {'✓' if code == 0 else '✗'} {rel}" + (f"  {err[:120]}" if code else ""))

    print()
    print("=" * 74)
    print("2. 重启并验证")
    print("=" * 74)
    code, out, err = remote.ssh(
        f"cd {REMOTE} && chown -R autosinin:autosinin app tools && "
        f"systemctl restart autosinin && sleep 6 && systemctl is-active autosinin",
        timeout=300,
    )
    print(f"  服务状态: {(out or '').strip()}")
    if err.strip():
        print(f"  ! {err.strip()[:200]}")

    print()
    print("=" * 74)
    print("3. 用真实任务核对判定结果")
    print("=" * 74)
    script = f"""
import sys
sys.path.insert(0, "{REMOTE}")
sys.stdout.reconfigure(encoding="utf-8")

from datetime import date, timedelta

from app.config import get_settings
from app.db import Database
from app.flysource import WX_CLIENT_ID, WX_CLIENT_SECRET, FlySourceClient
from app.security import SecretBox
from app.services import SignInService

s = get_settings(refresh=True)
db = Database(s.db_path)
svc = SignInService(db, s)
vault = SecretBox(s.encryption_key)

# 取第一个可用凭据的用户做核对（不写死学号）
users = db.list_signable_users()
if not users:
    print("  （没有可签到的用户，跳过真实任务核对）")
    raise SystemExit(0)
user = db.get_user(str(users[0]["student_id"]))
print(f"  核对用户: {{user['student_id']}} {{user.get('name') or ''}}")
token = vault.decrypt(user["token_enc"])

client = FlySourceClient(client_id=WX_CLIENT_ID, client_secret=WX_CLIENT_SECRET)
client.access_token = token
try:
    task = client.get_task(user["task_id"])
    print(f"  任务: {{task.task_name}}")
    print(f"  学校打卡期间: {{task.task_start_date}} ~ {{task.task_end_date}}")
    print(f"  signWeek: {{task.sign_week[:40]}}")
    print(f"  taskStatus: {{task.task_status}}  isStaySchoolTask: {{task.is_stay_school_task}}")
    print()
    print("  关键日期判定（新逻辑：以学校任务为准）:")
    for d in (date(2026,9,5), date(2026,9,7), date(2026,10,9),
              date(2027,1,15), date(2027,1,16), date(2027,1,25),
              date(2027,2,4), date(2027,2,5)):
        ok, why = task.available_on(d)
        cal_ok, cal_why = svc.calendar.should_sign(d)
        mark = "OK " if ok else "跳过"
        note = ""
        if ok and not cal_ok:
            note = "  <-- 旧逻辑会漏签！"
        print(f"    {{d}}  任务判定={{mark:4s}} 本地校历={{'签' if cal_ok else '不签'}}"
              f"  {{why[:36]}}{{note}}")
finally:
    client.close()
"""
    code, out, err = remote.ssh(
        f"cd {REMOTE} && .venv/bin/python - <<'PYEOF'\n{script}\nPYEOF",
        timeout=600,
    )
    for line in (out or "").splitlines():
        print(f"  {line}")
    if err.strip():
        print(f"  ! {err.strip()[:400]}")

finally:
    remote.cleanup()

print()
print("=" * 74)
print("完成")
print("=" * 74)
