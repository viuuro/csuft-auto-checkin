#!/usr/bin/env python3
"""把本次抓到的 token 直接写入本机数据库（供 import_token 使用）。

因为抓包结果在 tools/capture/tokens.json，而 import_token.py 从数据库读，
这里做一次桥接，把抓到的凭据落到本机库里。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from app.config import get_settings  # noqa: E402
from app.db import Database  # noqa: E402
from app.flysource import (  # noqa: E402
    WX_CLIENT_ID,
    WX_CLIENT_SECRET,
    FlySourceClient,
    jwt_claims,
    token_days_left,
)
from app.security import SecretBox, generate_password, hash_password  # noqa: E402

settings = get_settings(refresh=True)
db = Database(settings.db_path)
vault = SecretBox(settings.encryption_key)

data = json.loads((ROOT / "tools" / "capture" / "tokens.json").read_text(encoding="utf-8"))
token = data.get("access_token", "")
refresh = data.get("refresh_token", "")

if not token:
    print("抓包结果里没有 access_token")
    raise SystemExit(1)

claims = jwt_claims(token)
student_id = str(claims.get("accountNo") or "").strip()
name = str(claims.get("userName") or "").strip()
if not student_id:
    print("无法从 token 里读出学号")
    raise SystemExit(1)

print(f"学号: {student_id}   姓名: {name}")
print(f"access_token 剩余: {token_days_left(token):.1f} 天")
print(f"refresh_token: {'有' if refresh else '无'}")

# 校验可用
client = FlySourceClient(client_id=WX_CLIENT_ID, client_secret=WX_CLIENT_SECRET)
client.access_token = token
try:
    tasks = client.list_tasks()
    if not tasks:
        print("✗ 拉不到任务，放弃")
        raise SystemExit(1)
    task = client.get_task(tasks[0].task_id)
    print(f"✓ 任务: {task.task_name}")
    print(f"  时段: {task.sign_start_time} ~ {task.sign_end_time}")
    print(f"  宿舍: {task.dorm_name} {task.room_no}")
finally:
    client.close()

# 落库
existing = db.get_user(student_id)
if existing is None:
    db.create_user(
        student_id, name=name, approval_state="approved", trust_state="trusted"
    )
    print("已创建用户记录")

query_password = generate_password()
db.update_user(
    student_id,
    name=name or (existing or {}).get("name") or "",
    token_enc=vault.encrypt(token),
    refresh_token_enc=vault.encrypt(refresh) if refresh else "",
    credential_enc="",
    credential_ok=1,
    trust_state="trusted",
    approval_state="approved",
    enabled=1,
    last_error="",
    query_password_hash=hash_password(query_password),
    task_id=task.task_id,
    task_name=task.task_name,
    dorm_name=task.dorm_name,
    room_no=task.room_no,
    location_lat=task.location_lat,
    location_lng=task.location_lng,
    location_accuracy=task.location_accuracy or settings.location_accuracy,
)
db.log("bridge_import", f"把抓包凭据写入本机库：{student_id}", student_id=student_id)

final = db.get_user(student_id) or {}
print(f"\n✓ 已写入本机数据库")
print(f"  查询口令: {query_password}")
print(f"  签到时刻: {final.get('plan_sign_time') or '(待分配)'}")
print(f"  可自动续期: {'是' if final.get('refresh_token_enc') else '否'}")
print("\n下一步：python tools/import_token.py --host <你的服务器地址> --user root --student " + student_id)
