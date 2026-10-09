#!/usr/bin/env python3
"""环境诊断：排查工具包无法运行的原因。

同学端双击「检查环境.bat」即可运行（由系统 Python 执行本脚本）。
它会把每一项检查结果都打印出来，出问题时截图发给管理员即可。
"""

from __future__ import annotations

import ctypes
import os
import platform
import socket
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MITMDUMP = HERE / "mitmdump.exe"
CA_FILE = HERE / ".mitmproxy" / "mitmproxy-ca-cert.cer"
PORT = 8888

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass


def banner(t: str) -> None:
    print("\n" + "=" * 66)
    print(t)
    print("=" * 66)


def run(cmd: list[str], timeout: int = 90) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout, cwd=str(HERE))
        return p.returncode, ((p.stdout or b"") + (p.stderr or b"")).decode(
            "utf-8", errors="replace"
        )
    except OSError as exc:
        code = getattr(exc, "winerror", None)
        msg = ""
        if code:
            try:
                msg = ctypes.FormatError(code).strip()
            except Exception:  # noqa: BLE001
                pass
        return -1, f"[WinError {code}] {msg or exc}"
    except subprocess.TimeoutExpired:
        return -2, "超时"


def main() -> int:
    os.chdir(HERE)
    banner("中南林学工 · 工具包环境诊断")
    print(f"  工具包目录 : {HERE}")
    print(f"  系统       : {platform.platform()}")
    print(f"  当前 Python: {sys.version.split()[0]}  ({sys.executable})")
    try:
        admin = ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:  # noqa: BLE001
        admin = None
    print(f"  管理员权限 : {admin}")

    banner("1. 关键文件")
    files = {
        "mitmdump.exe": MITMDUMP,
        "python/python.exe": HERE / "python" / "python.exe",
        "capture_addon.py": HERE / "capture_addon.py",
        "kit_capture.py": HERE / "kit_capture.py",
        "kit_finish.py": HERE / "kit_finish.py",
        "README.txt": HERE / "README.txt",
    }
    missing = []
    for label, p in files.items():
        ok = p.exists()
        size = f"{p.stat().st_size} 字节" if ok else ""
        print(f"  {'✓' if ok else '✗'} {label:36s} {size}")
        if not ok:
            missing.append(label)

    # 证书是「首次运行时释放、跑完即删」的，单独判断，缺失不算问题
    if CA_FILE.exists():
        print(f"  ✓ {'.mitmproxy/mitmproxy-ca-cert.cer':36s} {CA_FILE.stat().st_size} 字节")
    else:
        print(f"  - {'.mitmproxy/mitmproxy-ca-cert.cer':36s} 未释放（首次运行时自动创建）")

    if missing:
        print(f"\n  ⚠ 缺失 {len(missing)} 个文件 —— 压缩包可能没解压完整，")
        print("    请删掉文件夹后重新完整解压。")

    banner("2. mitmdump.exe 能否运行")
    if not MITMDUMP.exists():
        print("  ✗ 文件不存在，跳过")
    else:
        code, out = run([str(MITMDUMP), "--version"], timeout=180)
        print(f"  退出码: {code}")
        if out.strip():
            for line in out.strip().splitlines()[:8]:
                print(f"  | {line}")
        else:
            print("  | (无输出)")
        if code != 0 or "Mitmproxy" not in out:
            print("\n  ⚠ mitmdump 无法运行。常见原因：")
            print("     1. 杀毒软件隔离了它 → 把文件夹加入白名单后重新解压")
            print("     2. 解压不完整 → 删掉文件夹重新解压")
            print("     3. 系统缺少 VC++ 运行库 → 安装后重试：")
            print("        https://aka.ms/vs/17/release/vc_redist.x64.exe")

    banner("3. 端口 8888 是否被占用")
    busy = False
    try:
        with socket.socket() as s:
            s.settimeout(0.5)
            busy = s.connect_ex(("127.0.0.1", PORT)) == 0
    except OSError:
        pass
    if busy:
        print(f"  ⚠ 端口 {PORT} 已被其它程序占用")
        print("     常见占用者：其它抓包/代理工具（Fiddler、Charles、Clash、v2ray 等）")
        print("     请先关闭它们，再重新运行 1-start.bat")
    else:
        print(f"  ✓ 端口 {PORT} 空闲")

    banner("4. 上次运行的日志")
    log = HERE / "capture.log"
    if log.exists() and log.stat().st_size:
        text = log.read_bytes().decode("utf-8", errors="replace")
        for line in text.strip().splitlines()[-15:]:
            print(f"  | {line}")
    else:
        print("  （没有日志，说明还没成功启动过代理）")

    banner("5. 结论")
    print("""
  请把本窗口的完整内容截图（或复制文字）发给管理员。

  对照参考：
   * 第 1 步有 ✗        → 解压不完整，重新解压
   * 第 2 步失败        → 杀软拦截或缺运行库（见上面的提示）
   * 第 3 步显示被占用  → 关掉其它代理工具
   * 都正常但仍失败     → 把第 4 步的日志一起发过去
""")
    try:
        input("按回车退出…")
    except (EOFError, KeyboardInterrupt):
        pass
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001
        import traceback

        print(f"\n诊断脚本自身出错: {type(exc).__name__}: {exc}")
        traceback.print_exc()
        try:
            input("按回车退出…")
        except (EOFError, KeyboardInterrupt):
            pass
        raise SystemExit(1) from exc
