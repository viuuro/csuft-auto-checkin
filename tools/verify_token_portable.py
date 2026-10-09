#!/usr/bin/env python3
"""从服务器端验证：别处抓到的 token 能否直接使用。

这决定了"同学在自己电脑上抓包 → 把 token 发给管理员"这条路是否成立。

在服务器上运行：
    cd /opt/autosinin && sudo -u autosinin .venv/bin/python tools/verify_token_portable.py <token>
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from app.config import get_settings  # noqa: E402
from app.flysource import (  # noqa: E402
    SIGN_STATUS_NAMES,
    WX_CLIENT_ID,
    WX_CLIENT_SECRET,
    FlySourceClient,
    jwt_claims,
    token_days_left,
)

if len(sys.argv) < 2:
    print("用法: verify_token_portable.py <access_token>")
    raise SystemExit(2)

token = sys.argv[1].strip()
settings = get_settings(refresh=True)

print("=" * 72)
print("从服务器验证外部抓取的 token 是否可用")
print("=" * 72)

claims = jwt_claims(token)
print("\n=== token 声明 ===")
for key in ("accountNo", "userName", "schoolName", "client_id"):
    print(f"  {key:12s} = {claims.get(key)}")
days = token_days_left(token)
print(f"  剩余有效期   = {days:.1f} 天" if days is not None else "  剩余有效期   = 未知")
print(f"  token 长度   = {len(token)}")

print("\n=== 从本服务器调用学校接口 ===")
client = FlySourceClient(
    client_id=WX_CLIENT_ID,
    client_secret=WX_CLIENT_SECRET,
    timeout=settings.request_timeout,
    verify_tls=settings.verify_tls,
)
client.access_token = token
try:
    tasks = client.list_tasks()
    if not tasks:
        print("  ✗ 拉不到打卡任务")
        raise SystemExit(1)
    print(f"  ✓ 任务列表拉取成功，共 {len(tasks)} 个任务")
    for t in tasks:
        print(f"      {t.task_id}  {t.task_name}")
        print(f"        时段 {t.sign_start_time} ~ {t.sign_end_time}")

    task = client.get_task(tasks[0].task_id)
    print(f"  ✓ 任务详情拉取成功")
    print(f"      宿舍     : {task.dorm_name} {task.room_no}")
    print(f"      基准坐标 : ({task.location_lat}, {task.location_lng})")
    print(f"      需定位   : {task.open_locate}   需拍照: {task.open_take_photo}")

    one = client.today_record(task.task_id)
    st = one.get("signStatus")
    try:
        st_int = int(st)
    except (TypeError, ValueError):
        st_int = -1
    print(f"  ✓ 今日状态拉取成功")
    print(f"      状态     : {st} ({SIGN_STATUS_NAMES.get(st_int, '未知')})")

    print("\n" + "=" * 72)
    print("结论：✓ 该 token 在服务器上完全可用")
    print("      → 同学在自己电脑抓包后发给你，这条路成立")
    print("=" * 72)
except Exception as exc:  # noqa: BLE001
    print(f"\n  ✗ 调用失败：{exc}")
    print("\n结论：该 token 无法从服务器使用，需要另找方案")
    raise SystemExit(1) from exc
finally:
    client.close()
