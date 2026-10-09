#!/usr/bin/env python3
"""上线前自检 / 部署排障。

一次性检查所有"能起服务但功能不工作"的常见原因，并给出可执行的修复建议。

用法::

    python tools/preflight.py                # 本机自检
    python tools/preflight.py --online       # 额外探测学校接口连通性
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import socket
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

OK = "  [ OK ]"
WARN = "  [WARN]"
FAIL = "  [FAIL]"

problems: list[str] = []
warnings: list[str] = []


def ok(msg: str) -> None:
    print(f"{OK} {msg}")


def warn(msg: str, fix: str = "") -> None:
    print(f"{WARN} {msg}")
    if fix:
        print(f"         → {fix}")
    warnings.append(msg)


def fail(msg: str, fix: str = "") -> None:
    print(f"{FAIL} {msg}")
    if fix:
        print(f"         → {fix}")
    problems.append(msg)


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def main() -> int:
    parser = argparse.ArgumentParser(description="上线前自检")
    parser.add_argument("--online", action="store_true", help="额外探测学校接口")
    args = parser.parse_args()

    print("=" * 74)
    print("中南林学工自动签到 —— 上线前自检")
    print("=" * 74)

    # ---------------------------------------------------------------- 运行环境
    section("1. Python 与依赖")
    ok(f"Python {sys.version.split()[0]}  ({sys.executable})")

    missing = []
    for mod in ("fastapi", "uvicorn", "httpx", "cryptography", "bcrypt", "apscheduler"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        fail(f"缺少依赖：{', '.join(missing)}", "pip install -r requirements.txt")
    else:
        ok("依赖齐全（fastapi / uvicorn / httpx / cryptography / bcrypt / apscheduler）")

    # ---------------------------------------------------------------- 配置
    section("2. 配置与密钥")
    from app.config import get_settings

    settings = get_settings(refresh=True)
    ok(f"站点名称：{settings.site_name}")
    ok(f"数据目录：{settings.data_dir}")
    ok(f"数据库：{settings.db_path}")

    if settings.secret_key:
        ok(f"会话密钥已就绪（{len(settings.secret_key)} 字符）")
    else:
        fail("会话密钥为空", "删除 data/config.json 后重启，会自动重新生成")

    if len(settings.encryption_key) == 44 and settings.encryption_key.endswith("="):
        ok("加密密钥格式正确（Fernet 32 字节 base64）")
    else:
        fail("加密密钥格式异常", "删除 data/config.json 后重启，会自动重新生成")

    # 密钥可用性实测
    try:
        from app.security import SecretBox

        box = SecretBox(settings.encryption_key)
        probe = "自检-" + str(time.time())
        if box.decrypt(box.encrypt(probe)) != probe:
            raise ValueError("加解密结果不一致")
        ok("加密密钥可用（加解密往返一致）")
    except Exception as exc:  # noqa: BLE001
        fail(f"加密密钥不可用：{exc}", "删除 data/config.json 后重启，会自动重新生成")

    # 数据目录可写
    try:
        probe_file = settings.data_dir / ".preflight"
        probe_file.write_text("ok", encoding="utf-8")
        probe_file.unlink()
        ok("数据目录可写")
    except OSError as exc:
        fail(
            f"数据目录不可写：{exc}",
            "chown -R autosinin:autosinin /opt/autosinin "
            "（systemd 的 EnvironmentFile 读不到会静默忽略，务必确认属主）",
        )

    # 配置文件权限
    cfg = settings.data_dir / "config.json"
    if cfg.exists():
        mode = oct(cfg.stat().st_mode & 0o777)
        if mode in ("0o600", "0o400"):
            ok(f"config.json 权限 {mode}")
        else:
            warn(
                f"config.json 权限为 {mode}，建议收紧",
                f"chmod 600 {cfg}",
            )

    # ---------------------------------------------------------------- 时区
    section("3. 时区与签到窗口")
    local_now = dt.datetime.now()
    ok(f"系统本地时间：{local_now.strftime('%Y-%m-%d %H:%M:%S')}  时区 {time.tzname[0]}")

    try:
        tz = ZoneInfo(settings.timezone)
        tz_now = dt.datetime.now(tz)
        drift = abs((tz_now.replace(tzinfo=None) - local_now).total_seconds())
        if drift <= 60:
            ok(f"系统时区与配置时区 {settings.timezone} 一致")
        else:
            fail(
                f"系统时区与配置的 {settings.timezone} 相差 {drift / 3600:.1f} 小时"
                f"（配置时间 {tz_now:%H:%M} vs 系统时间 {local_now:%H:%M}）",
                "sudo timedatectl set-timezone Asia/Shanghai && sudo systemctl restart autosinin",
            )
    except ZoneInfoNotFoundError:
        warn(f"系统缺少时区数据 {settings.timezone}", "sudo apt install -y tzdata")

    ok(f"签到窗口：{settings.sign_window_start} - {settings.sign_window_end}")

    from app.db import parse_time

    ws = parse_time(settings.sign_window_start)
    we = parse_time(settings.sign_window_end)
    if ws and we and ws < we:
        ok("窗口起止合法")
    else:
        fail("窗口起止不合法（需 HH:MM 且开始早于结束）", "检查 SIGN_WINDOW_START / SIGN_WINDOW_END")

    if settings.scheduler_enabled:
        ok("自动签到调度已启用")
    else:
        warn("自动签到调度已关闭（SCHEDULER_DISABLED=1）", "这是刻意关闭时请忽略")

    # ---------------------------------------------------------------- 管理员
    section("4. 管理员账号")
    if settings.is_admin_configured():
        ok(f"管理员已配置：{settings.admin_username}")
        from app.security import verify_password

        if settings.admin_password_hash.startswith(("$2a$", "$2b$", "$2y$")):
            ok("口令为 bcrypt 哈希（未存明文）")
        else:
            fail(
                "ADMIN_PASSWORD_HASH 看起来不是 bcrypt 哈希",
                "python tools/set_admin.py hash --password '你的密码' 重新生成",
            )
        if verify_password("__definitely_wrong__", settings.admin_password_hash):
            fail("口令校验异常：任意密码都能通过", "重新生成哈希")
    else:
        warn(
            "尚未配置管理员账号",
            "打开 /admin 页面按提示设置（设置后入口自动关闭）",
        )

    # ---------------------------------------------------------------- 数据库
    section("5. 数据库")
    from app.db import Database

    try:
        db = Database(settings.db_path)
        tables = {
            row["name"]
            for row in db.query("SELECT name FROM sqlite_master WHERE type='table'")
        }
        expected = {"users", "terms", "holidays", "sign_records", "sign_logs", "settings"}
        absent = expected - tables
        if absent:
            fail(f"缺少数据表：{', '.join(sorted(absent))}", "删除 data/app.db 后重启可重建")
        else:
            ok(f"表结构完整（{len(expected)} 张表）")

        stats = db.stats(dt.date.today())
        ok(
            f"成员 {stats['totalUsers']}（已批准 {stats['approvedUsers']}，"
            f"待审批 {stats['pendingUsers']}）"
        )
        ok(
            f"今日签到：成功 {stats['successToday']}，失败 {stats['failedToday']}"
        )

        # 校历
        from app.calendar_rules import CalendarRules

        rules = CalendarRules(db)
        if not rules.has_terms():
            fail(
                "尚未配置任何学期 —— 系统不会执行自动签到",
                "打开 /admin → 校历管理 → 添加学期区间",
            )
        else:
            ok(f"已配置 {len(db.list_terms())} 个学期、{len(db.list_holidays())} 个假期区间")

        should, reason = rules.should_sign(dt.date.today())
        today_flag = "会" if should else "不会"
        print(f"         今天（{dt.date.today()}）{today_flag}执行自动签到 — {reason}")

        nxt = rules.next_signable_day(dt.date.today())
        if nxt:
            ok(f"下一个可签到日：{nxt}")
        else:
            warn("未来 60 天内没有可签到日", "检查学期区间是否覆盖当前时间")

        # 已绑定用户
        signable = db.list_signable_users()
        ok(f"可自动签到的用户：{len(signable)} 位")
        for user in signable[:5]:
            print(
                f"         {user['student_id']}  {user.get('plan_sign_time') or '--:--'}"
                f"  {user.get('dorm_name') or '-'} {user.get('room_no') or ''}"
            )
    except Exception as exc:  # noqa: BLE001
        fail(f"数据库不可用：{exc}", "检查 data/ 目录属主与权限")

    # ---------------------------------------------------------------- 端口
    section("6. 监听端口")
    for port in (8000, 80, 443):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            in_use = sock.connect_ex(("127.0.0.1", port)) == 0
        if in_use:
            ok(f"端口 {port} 已被监听")
        else:
            print(f"  [ -- ] 端口 {port} 未监听")

    # ---------------------------------------------------------------- 学校接口
    if args.online:
        section("7. 学校接口连通性")
        import httpx

        base = settings.flysource_base_url
        try:
            r = httpx.get(base, timeout=15, follow_redirects=True, verify=settings.verify_tls)
            ok(f"{base} 可达（HTTP {r.status_code}）")
        except httpx.HTTPError as exc:
            fail(f"{base} 不可达：{exc}", "检查网络 / 防火墙 / DNS")

        try:
            from app.flysource import build_basic_auth, build_sign_header

            token_url = base + "/api/flySource-auth/oauth/token"
            r = httpx.post(
                token_url,
                params={
                    "tenantId": settings.tenant_id,
                    "username": "0000000000",
                    "password": "0",
                    "grant_type": "password",
                    "scope": "all",
                    "type": "captcha",
                },
                headers={
                    "Authorization": build_basic_auth(),
                    "Tenant-Id": settings.tenant_id,
                },
                timeout=20,
                verify=settings.verify_tls,
            )
            body = r.text[:200]
            if "Unauthorized grant type" in body:
                fail(
                    "服务端拒绝 password 授权 —— 学校可能已更换客户端凭据",
                    "需要重新逆向网页端 client 凭据，见 README 附录",
                )
            elif "用户名或密码错误" in body:
                ok("登录接口工作正常（用假账号探测，如期返回“用户名或密码错误”）")
            else:
                warn(f"登录接口返回了预期外的响应：{r.status_code} {body}")
        except Exception as exc:  # noqa: BLE001
            fail(f"登录接口探测失败：{exc}")

        try:
            sig = build_sign_header(base + "/api/flySource-yxgl/dormSignRecord/stuSign", "x", 1700000000000)
            ok(f"签名算法本地可算（示例 {sig[:24]}…）")
        except Exception as exc:  # noqa: BLE001
            fail(f"签名算法异常：{exc}")

    # ---------------------------------------------------------------- 结论
    print("\n" + "=" * 74)
    if problems:
        print(f"发现 {len(problems)} 个必须修复的问题：")
        for item in problems:
            print(f"  ✗ {item}")
    else:
        print("没有发现阻塞性问题。")
    if warnings:
        print(f"\n{len(warnings)} 项提醒：")
        for item in warnings:
            print(f"  ! {item}")

    print("\n上线前别忘了用真实账号跑一次：")
    print("  python tools/probe.py --username 学号 --password 密码")
    print("=" * 74)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
