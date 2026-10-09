#!/usr/bin/env python3
"""测试 refresh_token 是否能换取新的 access_token。

如果能，同学们就不用每 18 天重新抓包 —— 我们会话可以自动续期。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

import httpx  # noqa: E402

from app.flysource import (  # noqa: E402
    CLIENT_ID,
    CLIENT_SECRET,
    PATH_TOKEN,
    WX_CLIENT_ID,
    WX_CLIENT_SECRET,
    FlySourceClient,
    build_basic_auth,
    jwt_claims,
    token_days_left,
)

CAPTURE = ROOT / "tools" / "capture" / "tokens.json"
data = json.loads(CAPTURE.read_text(encoding="utf-8"))
access = data.get("access_token", "")
refresh = data.get("refresh_token", "")

print("=" * 74)
print("refresh_token 续期可行性测试")
print("=" * 74)
print(f"  access_token  长度: {len(access)}")
print(f"  refresh_token 长度: {len(refresh)}")

if not refresh:
    print("\n✗ 没有 refresh_token，无法测试")
    raise SystemExit(1)

claims = jwt_claims(access)
print(f"  当前 access exp   : {claims.get('exp')}  ({token_days_left(access):.1f} 天后)")
print(f"  当前 access jti   : {claims.get('jti')}")

BASE = "https://simp.csuft.edu.cn"

# 尝试用不同的客户端凭据组合调用 refresh_token 授权
COMBOS = [
    ("小程序客户端", WX_CLIENT_ID, WX_CLIENT_SECRET, {"Web-Type": "wxapp"}),
    ("小程序客户端(无Web-Type)", WX_CLIENT_ID, WX_CLIENT_SECRET, {}),
    ("网页端客户端", CLIENT_ID, CLIENT_SECRET, {}),
]

new_token = ""
for label, cid, csec, extra_headers in COMBOS:
    headers = {
        "Authorization": "Basic "
        + __import__("base64").b64encode(f"{cid}:{csec}".encode()).decode(),
        "Tenant-Id": "000000",
        **extra_headers,
    }
    params = {
        "tenantId": "000000",
        "refresh_token": refresh,
        "grant_type": "refresh_token",
        "scope": "all",
    }
    print(f"\n--- 尝试：{label} ---")
    try:
        r = httpx.post(BASE + PATH_TOKEN, params=params, headers=headers, timeout=25)
        try:
            payload = r.json()
        except ValueError:
            print(f"  HTTP {r.status_code}: {r.text[:200]}")
            continue

        if payload.get("access_token"):
            new_token = payload["access_token"]
            print(f"  ✓ HTTP {r.status_code} 换到了新的 access_token！")
            new_claims = jwt_claims(new_token)
            print(f"    新 jti : {new_claims.get('jti')}")
            print(f"    新 exp : {new_claims.get('exp')}  ({token_days_left(new_token):.1f} 天后)")
            print(f"    jti 是否变化 : {'是 ★' if new_claims.get('jti') != claims.get('jti') else '否'}")
            if payload.get("refresh_token"):
                print(f"    还返回了新的 refresh_token（长度 {len(payload['refresh_token'])}）")
            break
        err = payload.get("error_description") or payload.get("msg") or payload.get("error")
        print(f"  ✗ HTTP {r.status_code}: {err}")
    except httpx.HTTPError as exc:
        print(f"  ✗ 请求失败: {exc}")

print("\n" + "=" * 74)
if new_token:
    print("结论：✓ refresh_token 可以续期 —— 可以做自动续期，同学无需反复抓包")
    # 用新 token 验证一次业务接口
    client = FlySourceClient(client_id=WX_CLIENT_ID, client_secret=WX_CLIENT_SECRET)
    client.access_token = new_token
    try:
        tasks = client.list_tasks()
        print(f"      新 token 业务接口验证：✓ 拉到 {len(tasks)} 个任务")
    except Exception as exc:  # noqa: BLE001
        print(f"      新 token 业务接口验证：✗ {exc}")
    finally:
        client.close()
else:
    print("结论：✗ refresh_token 不可用 —— 仍需每 18 天重新抓包")
print("=" * 74)
