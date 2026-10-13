#!/usr/bin/env python3
"""用户端：停代理 + 卸证书 + 生成结果文件。"""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOKENS = HERE / "capture" / "tokens.json"
OUT = HERE / "token-result.txt"
CA_FLAG = HERE / ".ca-installed"
PID_FILE = HERE / ".mitm-pid"
CONFDIR = HERE / ".mitmproxy"
CA_FILE = CONFDIR / "mitmproxy-ca-cert.cer"

# 控制台代码页是 UTF-8（bat 里执行了 chcp 65001），stdout 与 stderr
# 都要按 UTF-8 输出，否则异常时的 traceback 中文会乱码。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass


def ask(prompt: str, default: str = "") -> str:
    """读一行输入；stdin 不可用时返回 default 而不是抛 EOFError。

    EOFError 若裸奔会打印一整段 traceback，使用者会看到窗口里
    滚出一堆源码 —— 退化成安全默认值即可。
    """
    try:
        return input(prompt)
    except EOFError:
        print()
        return default


def banner(t: str) -> None:
    print("\n" + "=" * 62)
    print(t)
    print("=" * 62)


def run(cmd: list[str]) -> tuple[int, str]:
    p = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=300)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main() -> int:
    os.chdir(HERE)
    banner("第 4 步：清理环境并生成结果")

    # 1 关代理
    try:
        import winreg
        KEY = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE
        ) as k:
            winreg.SetValueEx(k, "ProxyEnable", 0, winreg.REG_DWORD, 0)
        wininet = ctypes.windll.wininet
        wininet.InternetSetOptionW(0, 39, 0, 0)
        wininet.InternetSetOptionW(0, 37, 0, 0)
        print("  ✓ 系统代理已关闭")
    except OSError as exc:
        print(f"  ! 关闭代理失败，请手动到「Internet 选项」里关掉代理：{exc}")

    # 2 结束代理进程（先按 PID，再兜底按进程名）
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text(encoding="utf-8").strip())
            subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                           capture_output=True, timeout=30)
        except (ValueError, OSError, subprocess.SubprocessError):
            pass
        PID_FILE.unlink(missing_ok=True)
    # taskkill /PID 有时杀不掉（进程树/权限），用镜像名再兜一次
    try:
        r = subprocess.run(
            ["taskkill", "/F", "/IM", "mitmdump.exe"],
            capture_output=True, timeout=30,
        )
        out = ((r.stdout or b"") + (r.stderr or b"")).decode("gbk", errors="replace")
        if "成功" in out or "SUCCESS" in out.upper():
            print("  ✓ 抓包进程已结束")
        else:
            print("  ✓ 抓包进程已结束（或本来就未运行）")
    except (OSError, subprocess.SubprocessError):
        print("  ! 无法确认抓包进程状态，请到任务管理器检查 mitmdump.exe")

    # 3 卸证书
    if CA_FLAG.exists():
        scope = CA_FLAG.read_text(encoding="utf-8").strip()
        if scope == "user":
            ps = ("Get-ChildItem Cert:\\CurrentUser\\Root | "
                  "Where-Object { $_.Subject -like '*mitmproxy*' } | "
                  "ForEach-Object { $_.Thumbprint }")
            code, out = run(["powershell", "-NoProfile", "-Command", ps])
            for thumb in [t.strip() for t in out.splitlines() if len(t.strip()) == 40]:
                run(["powershell", "-NoProfile", "-Command",
                     f"Remove-Item -Path 'Cert:\\CurrentUser\\Root\\{thumb}' -Force"])
            print("  ✓ 临时证书已从「当前用户」受信任根移除")
        else:
            cert = Path.home() / ".mitmproxy" / "mitmproxy-ca-cert.cer"
            run(["certutil", "-delstore", "Root", str(cert)])
            print("  ✓ 临时证书已移除（系统级）")
        CA_FLAG.unlink(missing_ok=True)

    # 3.5 删掉工具包内的 CA 目录（含私钥）与备份文件，不留痕迹
    import shutil as _shutil

    if CONFDIR.exists():
        _shutil.rmtree(CONFDIR, ignore_errors=True)
        print("  ✓ 工具包内的证书文件已删除")
    (HERE / ".proxy-original").unlink(missing_ok=True)

    # 4 读结果
    banner("结果")
    if not TOKENS.exists():
        print("""
  ✗ 没有抓到任何数据。

  最常见的原因：微信没有完全退出。
  请重新双击「1-start.bat」，并严格按说明用【任务管理器】
  把所有 WeChat/Weixin 进程结束，再重新打开微信。
""")
        return 1

    data = json.loads(TOKENS.read_text(encoding="utf-8"))
    token = data.get("access_token", "")
    refresh = data.get("refresh_token", "")
    if not token:
        print("""
  ✗ 抓到了流量，但没有登录凭据。

  可能你只是打开了小程序首页，没有走登录流程。
  请重新运行，并在小程序里确保【重新登录】了一次，
  然后进入「打卡」页停留几秒。
""")
        return 1

    # 解码看是谁
    import base64
    who = ""
    try:
        p = token.split(".")[1]
        p += "=" * (-len(p) % 4)
        claims = json.loads(base64.urlsafe_b64decode(p))
        who = f"{claims.get('accountNo', '')} {claims.get('userName', '')}".strip()
    except Exception:  # noqa: BLE001
        pass

    OUT.write_text(
        "学工自动签到 —— 授权凭据\n"
        "（把本文件全部内容复制发给管理员）\n\n"
        f"身份: {who}\n"
        f"抓取时间: {data.get('captured_at', '')}\n"
        f"可自动续期: {'是' if refresh else '否'}\n\n"
        "=== access_token ===\n"
        f"{token}\n\n"
        "=== refresh_token ===\n"
        f"{refresh or '(无)'}\n",
        encoding="gbk",
        errors="replace",
    )

    print(f"""
  ✓ 抓取成功！

  身份：{who or '(未识别)'}
  可自动续期：{'是（管理员只需导入一次，之后长期有效）' if refresh else '否（约 18 天后需重新抓取）'}

  已生成文件：token-result.txt
  请打开它，把该文件或【全部内容】复制发给管理员。
""")
    try:
        subprocess.run(["explorer", "/select,", str(OUT)], timeout=15)
    except (OSError, subprocess.SubprocessError):
        pass
    ask("按回车关闭…")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
