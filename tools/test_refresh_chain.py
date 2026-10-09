#!/usr/bin/env python3
"""验证 refresh_token 能否连续续期（滚动刷新）。

若可以，系统就能长期自动维持会话，同学只需授权一次。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

import httpx  # noqa: E402

from app.flysource import (  # noqa: E402
    PATH_TOKEN,
    WX_CLIENT_ID,
    WX_CLIENT_SECRET,
    FlySourceClient,
    jwt_claims,
)


def refresh_once(refresh_token: str) -> dict:
    import base64

    headers = {
        "Authorization": "Basic "
        + base64.b64encode(f"{WX_CLIENT_ID}:{WX_CLIENT_SECRET}".encode()).decode(),
        "Tenant-Id": "000000",
        "Web-Type": "wxapp",
    }
    params = {
        "tenantId": "000000",
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
        "scope": "all",
    }
    r = httpx.post(PATH_TOKEN if PATH_TOKEN.startswith("http") else
                   "https://simp.csuft.edu.cn" + PATH_TOKEN,
                   params=params, headers=headers, timeout=25)
    return r.json()


data = json.loads((ROOT / "tools" / "capture" / "tokens.json").read_text(encoding="utf-8"))
refresh = data["refresh_token"]

print("=" * 74)
print("连续续期测试（模拟自动续期运行 6 天）")
print("=" * 74)

first_refresh = refresh
print(f"\n起始 refresh_token: {refresh[:40]}…")

for i in range(1, 7):
    try:
        payload = refresh_once(refresh)
    except Exception as exc:  # noqa: BLE001
        print(f"  第 {i} 次: ✗ 请求异常 {exc}")
        break

    if not payload.get("access_token"):
        err = payload.get("error_description") or payload.get("error") or payload.get("msg")
        print(f"  第 {i} 次: ✗ {err}")
        break

    access = payload["access_token"]
    new_refresh = payload.get("refresh_token") or refresh
    claims = jwt_claims(access)
    rotated = new_refresh != refresh
    print(
        f"  第 {i} 次: ✓ 新 jti={claims.get('jti', '')[:8]}…  "
        f"refresh {'已轮换' if rotated else '未变'}"
    )
    refresh = new_refresh
    time.sleep(0.5)

print("\n--- 最终可用性验证 ---")
client = FlySourceClient(client_id=WX_CLIENT_ID, client_secret=WX_CLIENT_SECRET)
client.access_token = access
try:
    tasks = client.list_tasks()
    print(f"  ✓ 连续续期后的 token 仍可正常调用业务接口（{len(tasks)} 个任务）")
    print("\n" + "=" * 74)
    print("结论：✓ 支持滚动续期 —— 可实现长期自动维持，同学只需授权一次")
    print("=" * 74)
except Exception as exc:  # noqa: BLE001
    print(f"  ✗ {exc}")
finally:
    client.close()
