#!/usr/bin/env python3
"""部署状态判定修复，并按服务端真实数据修正被误判的记录。"""

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

# 服务器地址从环境变量读取（避免把真实 IP 写进仓库）：
#     $env:ASI_HOST = "你的服务器地址"
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
print(f"目标：{USER}@{HOST}  安装目录 {REMOTE}")

remote = Remote(HOST, USER, "", PASSWORD)
try:
    print("=" * 74)
    print("1. 上传修复后的代码")
    print("=" * 74)
    for rel in ("app/flysource.py", "app/services.py", "tools/test_e2e.py"):
        code, _, err = remote.scp(ROOT / rel, f"{REMOTE}/{rel}")
        print(f"  {'✓' if code == 0 else '✗'} {rel}" + (f"  {err[:120]}" if code else ""))

    print()
    print("=" * 74)
    print("2. 重启服务")
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
    print("3. 从服务端重新同步，修正被误判的记录")
    print("=" * 74)
    sync_script = f"""
import sys
sys.path.insert(0, "{REMOTE}")
sys.stdout.reconfigure(encoding="utf-8")

from app.config import get_settings
from app.db import Database
from app.flysource import (
    PATH_RECORD_ONE, WX_CLIENT_ID, WX_CLIENT_SECRET, FlySourceClient,
)
from app.security import SecretBox
from app.services import already_signed, skip_reason

s = get_settings(refresh=True)
db = Database(s.db_path)
vault = SecretBox(s.encryption_key)

user = db.get_user("20240001")
token = vault.decrypt(user["token_enc"])
task_id = user["task_id"]

client = FlySourceClient(client_id=WX_CLIENT_ID, client_secret=WX_CLIENT_SECRET)
client.access_token = token
fixed = 0
try:
    print("  逐日核对（以服务端 getOne 为准）:")
    rows = db.query(
        "SELECT sign_date, status, sign_status, sign_time, message "
        "FROM sign_records ORDER BY sign_date"
    )
    for rec in rows:
        day = rec["sign_date"]
        try:
            raw = client._request("GET", PATH_RECORD_ONE,
                                  params={{"taskId": task_id, "signDate": day}})
            data = raw.get("data") if isinstance(raw, dict) else None
        except Exception as exc:
            print(f"    {{day}} 查询失败: {{exc}}")
            continue
        if not isinstance(data, dict):
            print(f"    {{day}} 服务端无记录")
            continue

        signed = already_signed(data)
        want = "success" if signed else ("failed" if day < "2026-10-09" else rec["status"])
        got = rec["status"]
        mark = "OK " if got == want else "修正"
        sname = data.get("signStatusName") or ""
        stime = data.get("signTime") or ""
        print(f"    {{day}}  服务端 signStatus={{data.get('signStatus')}} "
              f"name={{sname}} signTime={{stime!r}}  本地={{got}} -> {{want}}  [{{mark}}]")

        if got != want:
            db.upsert_record(
                "20240001", day, status=want,
                message=skip_reason(data) if signed else (sname or "未签到"),
                sign_status=data.get("signStatus"),
                sign_time=stime,
                source="resync",
            )
            db.log("record_repaired",
                   f"按服务端数据修正 {{day}}：{{got}} -> {{want}}（{{sname}} {{stime}}）",
                   student_id="20240001")
            fixed += 1
    print(f"\\n  共修正 {{fixed}} 条")
finally:
    client.close()
"""
    code, out, err = remote.ssh(
        f"cd {REMOTE} && .venv/bin/python - <<'PYEOF'\n{sync_script}\nPYEOF",
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
