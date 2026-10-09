#!/usr/bin/env python3
"""需求覆盖自检：把目标里的每条要求映射到实现与测试证据。

用法: python tools/check_requirements.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(".")
STATIC = ROOT / "app/static"

# (需求, [(文件, 必须出现的模式...)], [对应测试断言关键字...])
REQUIREMENTS: list[tuple[str, list[tuple[str, list[str]]], list[str]]] = [
    (
        "用户端 · 首次使用需学号+密码登录学校打卡账号",
        [
            ("app/static/index.html", ["bindForm", "bindStudentId", "bindPassword"]),
            ("app/static/app.js", ["/api/bind"]),
            ("app/routes_user.py", ["/api/bind", "bind_credentials"]),
            ("app/services.py", ["def bind_credentials", "login_auto"]),
        ],
        ["绑定成功"],
    ),
    (
        "用户端 · 图形验证码",
        [
            ("app/flysource.py", ["def get_captcha", "PATH_CAPTCHA"]),
            ("app/routes_user.py", ["/api/captcha"]),
            ("app/static/index.html", ["captchaBox", "bindCaptchaCode"]),
            ("app/static/app.js", ["loadCaptcha"]),
        ],
        [],
    ),
    (
        "用户端 · 只能通过输入学号查看自己的签到情况",
        [
            ("app/routes_user.py", ["/api/login", "query_password_hash", "_require_user"]),
            ("app/static/index.html", ["loginStudentId", "loginPassword"]),
            ("app/static/app.js", ["/api/login"]),
        ],
        ["正确口令登录成功", "错误口令被拒绝"],
    ),
    (
        "用户端 · 日历看板",
        [
            ("app/routes_user.py", ["/api/calendar"]),
            ("app/services.py", ["def calendar_payload"]),
            ("app/static/index.html", ["calGrid", "calTitle", "calPrev", "calNext"]),
            ("app/static/app.js", ["renderCalendar", "loadCalendar"]),
        ],
        ["日历返回当月天数"],
    ),
    (
        "用户端 · 21:00-22:30 之间随机时间自动签到",
        [
            ("app/config.py", ['DEFAULT_SIGN_WINDOW_START = "21:00"', 'DEFAULT_SIGN_WINDOW_END = "22:30"']),
            ("app/services.py", ["def randomize_plan_times", "randrange", "plan_sign_time"]),
            ("app/scheduler.py", ["def tick", "def start", "IntervalTrigger"]),
        ],
        ["自动分配了窗口内签到时刻", "重新随机时刻"],
    ),
    (
        "用户端 · 跳过寒暑假",
        [
            ("app/calendar_rules.py", ["def should_sign", "def holiday_for", "def term_for"]),
            ("app/db.py", ["CREATE TABLE IF NOT EXISTS holidays", "CREATE TABLE IF NOT EXISTS terms"]),
        ],
        ["假期内不执行签到", "学期内应签到", "未配置学期时不签到"],
    ),
    (
        "管理端 · 有且仅有管理员自己的账号能登录",
        [
            (
                "app/routes_admin.py",
                [
                    'prefix="/admin"',          # 路由前缀，缺了会导致管理接口暴露在根路径
                    '@router.post("/api/login")',
                    "settings.admin_username",
                    "_MAX_LOGIN_FAILURES",
                ],
            ),
            ("app/config.py", ["admin_username", "admin_password_hash"]),
            ("app/security.py", ["def verify_password", "bcrypt"]),
            ("app/static/admin.html", ["loginForm", "loginUsername", "loginPassword"]),
        ],
        ["管理员登录成功", "错误密码被拒绝", "未登录访问仪表盘被拒绝"],
    ),
    (
        "管理端 · 亲自批准后该用户才能使用自动签到",
        [
            ("app/routes_admin.py", ["/approve", "/reject"]),
            ("app/services.py", ["APPROVAL_PENDING", "APPROVAL_APPROVED", "def approve"]),
            ("app/static/admin.js", ["renderPending", "data-approve"]),
        ],
        ["未批准绑定被拒绝", "未批准登录被拒绝", "批准成功"],
    ),
    (
        "管理端 · 成员管理",
        [
            ("app/routes_admin.py", ["/enabled", "/reset-password", "admin_update_user"]),
            ("app/services.py", ["def set_enabled", "def reset_query_password", "def delete_user"]),
            ("app/static/admin.js", ["renderMembers", "data-toggle", "data-reset", "data-del"]),
        ],
        ["停用成功", "重置口令返回新口令", "删除成功", "撤回时注销了学校侧会话"],
    ),
    (
        "管理端 · 校历/假期维护",
        [
            ("app/routes_admin.py", ["/api/terms", "/api/holidays", "admin_check_calendar"]),
            ("app/static/admin.js", ["loadTerms", "loadHolidays", "termForm", "holidayForm"]),
        ],
        ["添加学期成功", "添加假期成功", "今天应执行签到"],
    ),
    (
        "可部署到 Linux 云主机",
        [
            ("deploy/autosinin.service", ["[Unit]", "ExecStart", "Restart=always", "StateDirectory"]),
            ("docs/DEPLOY.md", ["systemd", "nginx", "certbot", "timedatectl set-timezone"]),
            ("tools/preflight.py", ["def main"]),
            ("requirements.txt", ["fastapi", "uvicorn", "httpx", "cryptography"]),
        ],
        [],
    ),
]

failures = 0
print("=" * 78)
print("需求覆盖自检")
print("=" * 78)

for req, impls, tests in REQUIREMENTS:
    print(f"\n▸ {req}")
    row_ok = True
    for path_str, patterns in impls:
        path = ROOT / path_str
        if not path.exists():
            print(f"    [MISSING FILE] {path_str}")
            row_ok = False
            failures += 1
            continue
        text = path.read_text(encoding="utf-8")
        for pat in patterns:
            if pat not in text:
                print(f"    [MISSING] {path_str} 缺少 {pat!r}")
                row_ok = False
                failures += 1
    if row_ok:
        print(f"    实现 ✓  ({len(impls)} 个文件全部命中)")
    if tests:
        e2e = (ROOT / "tools/test_e2e.py").read_text(encoding="utf-8")
        units = (ROOT / "tools/test_units.py").read_text(encoding="utf-8")
        hit = [t for t in tests if t in e2e or t in units]
        if hit:
            print(f"    测试 ✓  ({len(hit)}/{len(tests)} 条断言命中)")
        else:
            print(f"    [WARN] 未找到对应测试断言：{tests}")
    else:
        print("    测试 –  （由 preflight / 冒烟脚本覆盖）")

print("\n" + "=" * 78)
if failures:
    print(f"发现 {failures} 处实现缺失")
else:
    print(f"全部 {len(REQUIREMENTS)} 条需求均有实现落点")
print("=" * 78)
raise SystemExit(1 if failures else 0)
