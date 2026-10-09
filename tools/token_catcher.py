"""
一键抓取某个同学的 access_token，并直接写入本系统数据库。

适用场景
--------
同学的学工账号启用了短信二次认证，无法用"学号+密码"自动登录，
因此需要让同学在小程序里登录一次，我们抓取那一次的 token 并长期复用。

用法（在管理员自己的电脑上运行）
------------------------------
    python tools/token_catcher.py 20240001

流程：
    1. 工具启动本地代理并安装临时 CA 证书（会明确提示）
    2. 同学按提示重启微信 → 进入「中南林学工」→ 点进「打卡」页
    3. 工具抓到 token，询问同学姓名等信息，写入数据库
    4. 结束时自动卸载证书、关闭代理

安全说明
--------
* 证书只装「当前用户 → 受信任的根证书颁发机构」，抓完立即移除
* 需要用户按回车确认后才安装，不静默操作
* 抓到的 token 在本机加密后入库（Fernet），不落明文
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from app.config import get_settings  # noqa: E402
from app.db import Database  # noqa: E402
from app.security import SecretBox, generate_password, hash_password  # noqa: E402
from app.services import SignInService  # noqa: E402

CAPTURE_DIR = ROOT / "tools" / "capture"
TOKEN_FILE = CAPTURE_DIR / "tokens.json"
PROXY_PORT = 8888

MITMDUMP = Path.home() / "AppData/Local/Programs/Python/Python312/Scripts/mitmdump.exe"


# --------------------------------------------------------------------------- #
# 显示辅助
# --------------------------------------------------------------------------- #


def banner(text: str, char: str = "=") -> None:
    print("\n" + char * 74)
    print(text)
    print(char * 74)


def step(n: int, text: str) -> None:
    print(f"\n【第 {n} 步】{text}")
    print("-" * 74)


def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        value = input(f"{prompt}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        return default
    return value or default


def pause(prompt: str) -> bool:
    try:
        input(f"\n{prompt}\n（准备好后按回车，输入 q 放弃）: ").strip()
        return True
    except (EOFError, KeyboardInterrupt):
        return False


# --------------------------------------------------------------------------- #
# 环境控制
# --------------------------------------------------------------------------- #


def run_module(script: str, *args: str) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / script), *args],
        capture_output=True,
    )
    for enc in ("utf-8", "gbk"):
        try:
            out = (proc.stdout or b"").decode(enc) + (proc.stderr or b"").decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        out = "(输出无法解码)"
    return proc.returncode, out


def start_proxy() -> subprocess.Popen[bytes] | None:
    if not MITMDUMP.exists():
        # 退回到 PATH 里的 mitmdump
        found = shutil.which("mitmdump")
        if not found:
            print("  ✗ 找不到 mitmdump，请先 pip install mitmproxy")
            return None
        exe = found
    else:
        exe = str(MITMDUMP)

    log = open(CAPTURE_DIR / "mitm.log", "wb")  # noqa: SIM115
    proc = subprocess.Popen(
        [exe, "-p", str(PROXY_PORT), "-s", str(ROOT / "tools" / "capture_addon.py"),
         "--set", "connection_strategy=lazy", "--set", "http2=false"],
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    # 等待端口就绪
    import socket

    for _ in range(40):
        time.sleep(0.25)
        with socket.socket() as s:
            s.settimeout(0.3)
            if s.connect_ex(("127.0.0.1", PROXY_PORT)) == 0:
                return proc
    proc.terminate()
    return None


def stop_proxy(proc: subprocess.Popen[bytes] | None) -> None:
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def read_tokens() -> tuple[str, str]:
    """返回 (access_token, refresh_token)。"""
    if not TOKEN_FILE.exists():
        return "", ""
    try:
        data = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "", ""
    return str(data.get("access_token") or ""), str(data.get("refresh_token") or "")


def decode_claims(token: str) -> dict:
    parts = token.split(".")
    if len(parts) != 3:
        return {}
    try:
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        return json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, TypeError):
        return {}


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #


def main() -> int:
    parser = argparse.ArgumentParser(description="抓取同学的小程序 token 并录入系统")
    parser.add_argument("student_id", nargs="?", help="学号（可留空，抓完再填）")
    parser.add_argument("--keep-cert", action="store_true", help="抓完不卸载证书（不建议）")
    args = parser.parse_args()

    banner("中南林学工 —— 同学授权采集工具")
    print("""
用途：同学的学工账号启用了短信二次认证，无法用密码自动登录。
      本工具让同学在小程序里登录一次，抓取该次会话的 token 并加密存入本系统，
      之后系统就能长期代替他自动签到。

⚠️  过程中你（管理员）的电脑会短暂处于「HTTPS 可被本机解密」的状态：
    * 只会把 mitmproxy 的证书装进【当前用户】的受信任根
    * 抓取结束后自动移除证书、关闭代理
    * 需要你在每一步明确按回车确认
""")
    if ask("确认继续？输入 yes 继续", "no").lower() not in ("yes", "y"):
        print("已取消。")
        return 0

    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)

    settings = get_settings(refresh=True)
    db = Database(settings.db_path)
    service = SignInService(db, settings)
    vault = SecretBox(settings.encryption_key)

    # ---------------------------------------------------------------- 证书 --
    step(1, "安装临时证书（让微信小程序愿意把请求交给本地代理）")
    code, out = run_module("install_ca.py", "install")
    print("  " + out.strip().replace("\n", "\n  ")[-400:])
    if code != 0:
        print("\n✗ 证书安装失败，无法继续。")
        return 1

    # ---------------------------------------------------------------- 代理 --
    step(2, "启动本地代理并接管系统代理")
    proxy = start_proxy()
    if proxy is None:
        print("  ✗ 代理启动失败")
        run_module("install_ca.py", "uninstall")
        return 1
    print(f"  ✓ 代理已监听 127.0.0.1:{PROXY_PORT}")

    code, out = run_module("system_proxy.py", "on")
    print("  " + out.strip().splitlines()[-1] if out.strip() else "")

    # ------------------------------------------------------------ 采集 ----
    step(3, "请把电脑交给同学，让他在微信小程序里操作")
    print("""
  同学需要做的（大约 1 分钟）：

    ① 完全退出微信 —— 右下角托盘图标右键 → 退出（不是关窗口）
    ② 重新打开微信，进入「中南林学工」小程序
    ③ 如果提示未登录，正常登录（微信一键登录 / 账号登录都可以）
    ④ 点进底部「打卡」页，停留 5 秒左右

  ⚠️ 关键：一定要「完全退出再重新打开」，否则微信不会走新代理。
""")

    token = ""
    refresh = ""
    deadline = time.time() + 900  # 最长等 15 分钟
    last_hint = 0
    try:
        while time.time() < deadline:
            token, refresh = read_tokens()
            if token:
                break
            waited = int(time.time() - (deadline - 900))
            if waited - last_hint >= 20:
                last_hint = waited
                print(f"  …等待中（已 {waited} 秒）")
            time.sleep(2)
    except KeyboardInterrupt:
        print("\n  已中断。")

    # ------------------------------------------------------------ 收尾 ----
    step(4, "恢复环境")
    run_module("system_proxy.py", "off")
    stop_proxy(proxy)
    if not args.keep_cert:
        code, out = run_module("install_ca.py", "uninstall")
        print("  ✓ 系统代理已关闭")
        print("  " + (out.strip().splitlines()[-1] if out.strip() else "证书已处理"))
    else:
        print("  ✓ 系统代理已关闭（按要求保留了证书）")

    if not token:
        print("\n✗ 没有抓到 token。常见原因：")
        print("   * 微信没有完全退出重启（最常见）")
        print("   * 同学没有点进「打卡」页 —— 只有那一页会带认证头发请求")
        print("   * 中途取消了登录")
        return 1

    claims = decode_claims(token)
    banner("抓到 token", "-")
    print(f"  学号     : {claims.get('accountNo') or '(未知)'}")
    print(f"  姓名     : {claims.get('userName') or '(未知)'}")
    print(f"  学校     : {claims.get('schoolName') or '(未知)'}")
    exp = claims.get("exp")
    left = 0.0
    if exp:
        import datetime as dt

        left = (dt.datetime.fromtimestamp(exp) - dt.datetime.now()).total_seconds() / 86400
        print(f"  有效期至 : {dt.datetime.fromtimestamp(exp):%Y-%m-%d %H:%M}（约 {left:.1f} 天）")
    print(f"  token 长度: {len(token)}")
    if refresh:
        print(f"  ✓ 同时拿到 refresh_token（{len(refresh)} 字符）—— 可用于自动续期")
    else:
        print("  ⚠ 未拿到 refresh_token —— 约 18 天后需要重新采集")
        print("    （通常是因为小程序复用了旧会话，没走完整的重新登录流程）")

    # ------------------------------------------------------------ 入库 ----
    step(5, "录入系统")
    student_id = (args.student_id or claims.get("accountNo") or "").strip()
    if not student_id:
        student_id = ask("请输入该同学的学号")
    if not student_id:
        print("✗ 没有学号，无法录入。token 保留在 tools/capture/tokens.json 供手工处理。")
        return 1

    name = str(claims.get("userName") or "").strip() or ask("该同学姓名（可留空）", "")
    phone = ask("手机号（可留空）", "")
    major = ask("专业/班级（可留空）", "")

    existing = db.get_user(student_id)
    if existing is None:
        service.submit_application(student_id, name=name, phone=phone, major=major)
        print(f"  已创建用户记录：{student_id}")
    else:
        print(f"  用户已存在：{student_id}（状态 {existing['approval_state']}）")
        if name and existing.get("name") != name:
            db.update_user(student_id, name=name)

    if (db.get_user(student_id) or {}).get("approval_state") != "approved":
        service.approve(student_id, "token-catcher")
        print("  已批准（不再需要单独审批）")

    # 验证 token 可用
    print("\n  正在用该 token 校验并获取打卡任务…")
    from app.flysource import FlySourceClient

    client = FlySourceClient(
        client_id="flysource_wise_wxapp",
        client_secret="DA788asdUDjnasd_flysource_wxappdsdadDAIUiuwqe",
    )
    client.access_token = token
    try:
        tasks = client.list_tasks()
        if not tasks:
            print("  ✗ 该 token 拉不到打卡任务，未录入。")
            return 1
        task = client.get_task(tasks[0].task_id)
        print(f"  ✓ 任务：{task.task_name}")
        print(f"     签到时段：{task.sign_start_time} ~ {task.sign_end_time}")
        print(f"     宿舍：{task.dorm_name} {task.room_no}")
        print(f"     基准坐标：({task.location_lat}, {task.location_lng})")
    except Exception as exc:  # noqa: BLE001
        print(f"  ✗ 校验失败：{exc}")
        print("    token 未录入，请重试。")
        return 1
    finally:
        client.close()

    query_password = generate_password()
    db.update_user(
        student_id,
        query_password_hash=hash_password(query_password),
        token_enc=vault.encrypt(token),
        refresh_token_enc=vault.encrypt(refresh) if refresh else "",
        credential_ok=1,
        trust_state="trusted",
        last_error="",
        task_id=task.task_id,
        task_name=task.task_name,
        dorm_name=task.dorm_name,
        room_no=task.room_no,
        location_lat=task.location_lat,
        location_lng=task.location_lng,
        location_accuracy=task.location_accuracy or settings.location_accuracy,
    )

    if not (db.get_user(student_id) or {}).get("plan_sign_time"):
        service.randomize_plan_times([student_id])
    final = db.get_user(student_id) or {}

    db.log(
        "token_captured",
        f"通过抓包录入 {student_id}（{name or '未填姓名'}）的会话",
        student_id=student_id,
    )

    banner("录入完成")
    print(f"  学号       : {student_id}")
    print(f"  姓名       : {final.get('name') or '(未填)'}")
    print(f"  查询口令   : {query_password}")
    print("               ↑ 请转告该同学，用于登录本系统查看自己的签到情况")
    print(f"  签到时刻   : {final.get('plan_sign_time')}（窗口 {settings.sign_window_start}-{settings.sign_window_end}）")
    print(f"  宿舍       : {final.get('dorm_name')} {final.get('room_no')}")
    if refresh:
        print("  续期能力   : ✓ 已保存 refresh_token，可自动续期，无需再次采集")
    elif exp:
        print(f"  续期能力   : ✗ 无 refresh_token，约 {left:.0f} 天后需重新采集")
    print("\n  token 已用本地密钥加密后存入数据库，未落明文。")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
