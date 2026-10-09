#!/usr/bin/env python3
"""【已废弃】修正被误判的签到记录 —— 请改用 ``tools/resync_records.py``。

为什么废弃
----------
本脚本按**本地记录里的消息文案**猜哪条是误判（只认「已完成签到」这类提示），
于是漏掉了 2026-10-07 —— 那天的本地消息是「签到失败，请重试！」，
但它其实**签到成功了**（服务端 ``signTime=21:11:48``）。

正确的做法是以**服务端 ``getOne`` 的返回**为准逐日核对，也就是
``tools/resync_records.py`` 干的事：它用 ``already_signed()`` 判断
（看 ``signTime``，而不是猜文案），不会漏。

保留本文件仅供追溯，不要再用它修数据。
"""

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

# 服务器地址从环境变量读取（避免把真实 IP 写进仓库）。
# 注意：本脚本已废弃，改用 tools/resync_records.py。
HOST = os.environ.get("ASI_HOST", "")
USER = os.environ.get("ASI_SSH_USER", "root")
REMOTE = os.environ.get("ASI_REMOTE_DIR", "/opt/autosinin")
PASSWORD = os.environ.get("ASI_SSH_PASSWORD", "")

if not HOST:
    print("请先设置环境变量 ASI_HOST（服务器地址）")
    raise SystemExit(1)
if not PASSWORD:
    print("请先设置环境变量 ASI_SSH_PASSWORD")
    raise SystemExit(1)

FILES = ["app/services.py"]

remote = Remote(HOST, USER, "", PASSWORD)
try:
    print("=" * 72)
    print("1. 上传修复后的 services.py")
    print("=" * 72)
    for rel in FILES:
        code, _, err = remote.scp(ROOT / rel, f"{REMOTE}/{rel}")
        print(f"  {'✓' if code == 0 else '✗'} {rel}" + (f"  {err[:120]}" if code else ""))

    print()
    print("=" * 72)
    print("2. 重启服务")
    print("=" * 72)
    code, out, err = remote.ssh(
        f"cd {REMOTE} && chown -R autosinin:autosinin app && "
        f"systemctl restart autosinin && sleep 6 && systemctl is-active autosinin",
        timeout=300,
    )
    print(f"  服务状态: {(out or '').strip()}")
    if err.strip():
        print(f"  ! {err.strip()[:200]}")

    print()
    print("=" * 72)
    print("3. 修正被污染的记录（按服务端真实数据）")
    print("=" * 72)
    fix_script = f"""
import sys
sys.path.insert(0, "{REMOTE}")
sys.stdout.reconfigure(encoding="utf-8")

from app.config import get_settings
from app.db import Database
from app.flysource import (
    WX_CLIENT_ID, WX_CLIENT_SECRET, FlySourceClient,
    SIGN_STATUS_NAMES, jwt_claims,
)
from app.security import SecretBox

s = get_settings(refresh=True)
db = Database(s.db_path)
vault = SecretBox(s.encryption_key)

rows = db.query(
    "SELECT student_id, sign_date, status, message FROM sign_records "
    "WHERE status = 'failed' ORDER BY sign_date"
)
print(f"  待检查的失败记录: {{len(rows)}} 条")

fixed = 0
for rec in rows:
    sid = rec["student_id"]
    day = rec["sign_date"]
    msg = rec["message"] or ""
    # 只有「已完成签到」这类提示才说明其实成功了
    if not any(h in msg for h in ("已完成签到", "请勿重复", "已经签到", "已打卡")):
        print(f"    {{day}} {{sid}} 保持失败: {{msg[:40]}}")
        continue

    user = db.get_user(sid)
    token = ""
    if user.get("token_enc"):
        try:
            token = vault.decrypt(user["token_enc"])
        except Exception:
            pass
    # 复查服务端当月数据，确认那天确实已签
    confirmed = False
    detail = ""
    if token:
        client = FlySourceClient(client_id=WX_CLIENT_ID, client_secret=WX_CLIENT_SECRET)
        client.access_token = token
        try:
            tasks = client.list_tasks()
            if tasks:
                task = client.get_task(tasks[0].task_id)
                month = client.month_records(task.task_id, day[:7]) or {{}}
                # month_records 返回 {{日期: 记录}} 字典
                item = month.get(day) if isinstance(month, dict) else None
                if item is None and isinstance(month, list):
                    item = next(
                        (x for x in month if str(x.get("signDate")) == day), None
                    )
                if item:
                    st = item.get("signStatus")
                    try:
                        st_i = int(st)
                    except (TypeError, ValueError):
                        st_i = -1
                    if st_i in (1, 5):
                        confirmed = True
                        detail = f"signStatus={{st}} {{SIGN_STATUS_NAMES.get(st_i, '')}}"
                    else:
                        detail = f"signStatus={{st}} {{SIGN_STATUS_NAMES.get(st_i, '未签到')}}"
                else:
                    detail = "当月记录里没有该日"
        except Exception as exc:
            detail = f"查询失败: {{exc}}"
        finally:
            client.close()

    if confirmed:
        db.upsert_record(sid, day, status="success",
                         message=f"已签到（{{detail}}）", source="repair")
        db.log("record_repaired",
               f"修正 {{day}} 的误判失败记录：服务端确认已签到（{{detail}}）",
               student_id=sid)
        print(f"    {{day}} {{sid}} -> 修正为 success（{{detail}}）")
        fixed += 1
    else:
        print(f"    {{day}} {{sid}} 未获服务端确认（{{detail or '无 token'}}），保持原状")

print(f"\\n  共修正 {{fixed}} 条")
"""
    code, out, err = remote.ssh(
        f"cd {REMOTE} && .venv/bin/python - <<'PYEOF'\n{fix_script}\nPYEOF",
        timeout=600,
    )
    for line in (out or "").splitlines():
        print(f"  {line}")
    if err.strip():
        print(f"  ! {err.strip()[:400]}")

finally:
    remote.cleanup()

print()
print("=" * 72)
print("完成")
print("=" * 72)
