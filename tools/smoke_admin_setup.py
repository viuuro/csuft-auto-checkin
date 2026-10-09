#!/usr/bin/env python3
"""验证"首次设置管理员"网页流程 + 审批 + 校历维护的完整真实 HTTP 链路。

运行前需要先启动服务（干净数据目录）。

用法: python tools/smoke_admin_setup.py http://127.0.0.1:8124
"""

from __future__ import annotations

import datetime as dt
import json
import sys

import httpx

sys.stdout.reconfigure(encoding="utf-8")

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8124"
c = httpx.Client(base_url=BASE, timeout=25, follow_redirects=True)

PASSED, FAILED = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASSED if cond else FAILED).append(name if cond else f"{name} :: {detail}")
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + ("" if cond else f" :: {detail}"))


def post(path, payload):
    return c.post(path, json=payload)


print("\n=== 1. 初始状态：未配置管理员 ===")
r = c.get("/admin/api/status").json()
check("configured 为 false", r["configured"] is False, json.dumps(r, ensure_ascii=False))
check("未登录", r["loggedIn"] is False)

print("\n=== 2. 弱密码被拒绝 ===")
r = post("/admin/api/setup", {"username": "ab", "password": "short"})
check("用户名过短被拒绝", r.status_code == 400, r.text)
r = post("/admin/api/setup", {"username": "admin", "password": "123"})
check("密码过短被拒绝", r.status_code == 400, r.text)

print("\n=== 3. 首次设置管理员 ===")
r = post("/admin/api/setup", {"username": "admin", "password": "MyStrongPass123"})
data = r.json()
check("设置成功", r.status_code == 200 and data.get("success") is True, r.text)
check("自动登录（拿到管理员会话）", "asi_admin" in c.cookies, str(c.cookies))

print("\n=== 4. 设置后入口关闭 ===")
r = post("/admin/api/setup", {"username": "hacker", "password": "WhateverPass1"})
check("二次设置被拒绝", r.status_code == 400, r.text)
status = c.get("/admin/api/status").json()
check("configured 变为 true", status["configured"] is True, json.dumps(status, ensure_ascii=False))

print("\n=== 5. 仪表盘可用 ===")
r = c.get("/admin/api/dashboard")
check("仪表盘 200", r.status_code == 200, r.text)
dash = r.json()
check("包含统计", "stats" in dash and "settings" in dash)
check("调度器被测试关闭", dash["scheduler"]["running"] is False, str(dash["scheduler"]))
check("今日判定为不签到（未配置学期）", dash["today"]["shouldSign"] is False, str(dash["today"]))

print("\n=== 6. 用户申请 → 审批 ===")
r = post("/api/apply", {"studentId": "20239999", "name": "李四", "major": "计算机"})
check("提交申请", r.json().get("state") == "submitted", r.text)

dash = c.get("/admin/api/dashboard").json()
check("出现在待审批", len(dash["pending"]) == 1, str(dash["pending"]))

r = post("/admin/api/users/20239999/approve", {})
check("批准成功", r.json()["user"]["approvalState"] == "approved", r.text)

dash = c.get("/admin/api/dashboard").json()
check("进入已批准列表", len(dash["users"]) == 1, str(len(dash["users"])))

print("\n=== 7. 校历维护 ===")
today = dt.date.today()
first = today.replace(day=1)
nxt = (first + dt.timedelta(days=32)).replace(day=1)
last = nxt - dt.timedelta(days=1)

r = post("/admin/api/terms", {"name": "测试学期", "startDate": first.isoformat(), "endDate": last.isoformat()})
check("添加学期", r.status_code == 200 and r.json()["success"], r.text)

r = post("/admin/api/terms", {"name": "倒置区间", "startDate": last.isoformat(), "endDate": first.isoformat()})
check("起止倒置被拒绝", r.status_code == 400, r.text)

r = post("/admin/api/holidays", {"name": "测试假期", "kind": "vacation",
                                 "startDate": first.isoformat(),
                                 "endDate": (first + dt.timedelta(days=1)).isoformat()})
check("添加假期", r.status_code == 200 and r.json()["success"], r.text)

r = post("/admin/api/holidays", {"name": "坏类型", "kind": "nonsense",
                                 "startDate": first.isoformat(), "endDate": first.isoformat()})
check("非法假期类型被拒绝", r.status_code == 400, r.text)

r = post("/admin/api/check-calendar", {"date": today.isoformat()}).json()
check("今天会签到", r["shouldSign"] is True, json.dumps(r, ensure_ascii=False))

r = post("/admin/api/check-calendar", {"date": first.isoformat()}).json()
check("假期内不签到", r["shouldSign"] is False and r["holiday"], json.dumps(r, ensure_ascii=False))

print("\n=== 8. 中文与日志 ===")
logs = c.get("/admin/api/logs?limit=50").json()["logs"]
actions = {row["action"] for row in logs}
check("有 admin_setup 日志", "admin_setup" in actions, str(sorted(actions)))
check("有 term_add 日志", "term_add" in actions, str(sorted(actions)))
check("日志中文正常", any("学期" in row["message"] for row in logs),
      str([row["message"] for row in logs[:3]]))

print("\n=== 9. 未登录访问被拒 ===")
c2 = httpx.Client(base_url=BASE, timeout=20)
r = c2.get("/admin/api/dashboard")
check("新客户端未授权", r.status_code == 400, r.text)
r = c2.get("/api/overview")
check("用户端未授权", r.status_code == 400, r.text)
c2.close()

print("\n=== 10. 用户端提示 ===")
r = c.get("/api/apply/status?studentId=20239999").json()
check("用户可查审批状态", r["state"] == "approved", json.dumps(r, ensure_ascii=False))
r = c.get("/api/apply/status?studentId=00000000").json()
check("未申请学号返回 not_found", r["state"] == "not_found", json.dumps(r, ensure_ascii=False))

c.close()
print("\n" + "=" * 70)
print(f"通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
for item in FAILED:
    print("  -", item)
print("=" * 70)
raise SystemExit(1 if FAILED else 0)
