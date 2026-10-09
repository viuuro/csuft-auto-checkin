#!/usr/bin/env python3
"""对运行中的服务做一次真实 HTTP 冒烟测试（UTF-8 正确解码）。"""

from __future__ import annotations

import json
import sys

import httpx

sys.stdout.reconfigure(encoding="utf-8")

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8123"
client = httpx.Client(base_url=BASE, timeout=20, follow_redirects=True)

ok = True


def section(title: str) -> None:
    print("\n" + "=" * 66)
    print(title)
    print("=" * 66)


def show(label: str, value: object) -> None:
    print(f"  {label}: {value}")


section("健康检查与页面")
r = client.get("/healthz")
show("GET /healthz", f"{r.status_code} {json.dumps(r.json(), ensure_ascii=False)}")
for path in ("/", "/admin"):
    r = client.get(path)
    title = ""
    if "<title>" in r.text:
        title = r.text.split("<title>")[1].split("</title>")[0]
    show(f"GET {path}", f"{r.status_code} title={title}")

section("中文编码检查")
r = client.get("/api/me")
data = r.json()
show("Content-Type", r.headers.get("content-type"))
show("siteName", data["config"]["siteName"])
if data["config"]["siteName"] != "中南林学工自动签到":
    ok = False
    print("  !! 中文编码异常")

section("用户端接口")
show("GET /api/me", json.dumps(data, ensure_ascii=False))

r = client.post("/api/apply", json={"studentId": "99999999", "name": "冒烟测试"})
show("POST /api/apply", f"{r.status_code} {json.dumps(r.json(), ensure_ascii=False)}")

r = client.get("/api/apply/status", params={"studentId": "99999999"})
show("GET /api/apply/status", json.dumps(r.json(), ensure_ascii=False))

r = client.post("/api/login", json={"studentId": "99999999", "queryPassword": "x"})
show("POST /api/login", f"{r.status_code} {json.dumps(r.json(), ensure_ascii=False)}")

section("管理端接口")
r = client.get("/admin/api/status")
show("GET /admin/api/status", json.dumps(r.json(), ensure_ascii=False))

r = client.post("/admin/api/login", json={"username": "a", "password": "b"})
show("POST /admin/api/login（未配置）", f"{r.status_code} {json.dumps(r.json(), ensure_ascii=False)}")

r = client.get("/admin/api/dashboard")
show("GET /admin/api/dashboard（未登录）", f"{r.status_code} {json.dumps(r.json(), ensure_ascii=False)}")

section("接口文档")
r = client.get("/api/docs")
show("GET /api/docs", r.status_code)

client.close()
print("\n" + ("全部正常" if ok else "存在问题"))
raise SystemExit(0 if ok else 1)
