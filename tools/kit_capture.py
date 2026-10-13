#!/usr/bin/env python3
"""用户端：装证书 + 开代理 + 启动抓包（一键）"""
from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = HERE / "python" / "python.exe"
MITMDUMP = HERE / "python" / "Scripts" / "mitmdump.exe"
# 证书放在工具包自己的目录，不污染系统的 ~/.mitmproxy。
# 首次运行时由本机 mitmdump 生成 —— 分发包里**不含**任何私钥。
CONFDIR = HERE / ".mitmproxy"
CA_FILE = CONFDIR / "mitmproxy-ca-cert.cer"
CAPTURE = HERE / "capture"
CA_INSTALLED_FLAG = HERE / ".ca-installed"
PORT = 8888

# 控制台代码页是 UTF-8（1-start.bat 里执行了 chcp 65001），
# 所以 stdout 与 stderr 都要按 UTF-8 输出 —— 只设 stdout 的话，
# 一旦出现异常，traceback 里的中文源码行会变成乱码。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass


def ask(prompt: str, default: str = "") -> str:
    """读一行输入；stdin 不可用时返回 default 而不是抛 EOFError。

    为什么需要：双击 .bat 运行时 stdin 通常是可用的，但在某些环境下
    （输出被重定向、以特定方式启动）input() 会抛 EOFError。裸奔的
    EOFError 会打印一整段 traceback —— 那正是使用者看到的
    "窗口里出现一堆代码"。这里退化成安全默认值。
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


def patch_proxy(enable: bool) -> bool:
    """改注册表开关系统代理。

    注意：``winreg.OpenKey`` 打开已存在的键时不会自动获得写权限，
    必须显式传 ``KEY_SET_VALUE``，否则会报 WinError 5（拒绝访问）。
    """
    import winreg

    KEY = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
    try:
        if enable:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE
            ) as k:
                try:
                    old_enable = winreg.QueryValueEx(k, "ProxyEnable")[0]
                except FileNotFoundError:
                    old_enable = 0
                try:
                    old_server = winreg.QueryValueEx(k, "ProxyServer")[0]
                except FileNotFoundError:
                    old_server = ""
                # 备份原值，finish 阶段据此恢复
                (HERE / ".proxy-original").write_text(
                    f"{old_enable}\n{old_server}", encoding="utf-8"
                )
                winreg.SetValueEx(k, "ProxyEnable", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(k, "ProxyServer", 0, winreg.REG_SZ, f"127.0.0.1:{PORT}")
                winreg.SetValueEx(
                    k, "ProxyOverride", 0, winreg.REG_SZ,
                    "localhost;127.*;10.*;172.16.*;172.17.*;172.18.*;172.19.*;"
                    "172.2*;172.30.*;172.31.*;192.168.*;<local>",
                )
        else:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_SET_VALUE
            ) as k:
                winreg.SetValueEx(k, "ProxyEnable", 0, winreg.REG_DWORD, 0)
        # 通知系统刷新
        wininet = ctypes.windll.wininet
        wininet.InternetSetOptionW(0, 39, 0, 0)
        wininet.InternetSetOptionW(0, 37, 0, 0)
        return True
    except OSError as exc:
        print(f"  ✗ 设置代理失败：{exc}")
        return False


def dump_diagnostics(reason: str) -> Path:
    """把诊断信息写到文件，方便同学直接发给管理员。"""
    import platform

    lines = [
        "学工签到授权工具 —— 诊断信息",
        "=" * 50,
        "",
        f"失败环节: {reason}",
        f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "--- 环境 ---",
        f"工具包目录 : {HERE}",
        f"当前工作目录: {os.getcwd()}",
        f"Python      : {sys.version}",
        f"可执行文件   : {sys.executable}",
        f"系统        : {platform.platform()}",
        f"是否管理员   : {ctypes.windll.shell32.IsUserAnAdmin() != 0}",
        "",
        "--- 关键文件 ---",
    ]
    for label, p in (
        ("mitmdump.exe", MITMDUMP),
        ("capture_addon.py", HERE / "capture_addon.py"),
        ("kit_capture.py", HERE / "kit_capture.py"),
        ("kit_finish.py", HERE / "kit_finish.py"),
        (".mitmproxy\\mitmproxy-ca-cert.cer", CA_FILE),
        ("capture.log", HERE / "capture.log"),
    ):
        exists = p.exists()
        size = f"{p.stat().st_size} 字节" if exists and p.is_file() else ""
        lines.append(f"{label:38s} {'存在' if exists else '缺失'}  {size}")

    lines += ["", "--- mitmdump 可执行性 ---"]
    if MITMDUMP.exists():
        try:
            pr = subprocess.run([str(MITMDUMP), "--version"], capture_output=True, timeout=120)
            lines.append(
                ((pr.stdout or b"") + (pr.stderr or b"")).decode("utf-8", "replace").strip()
            )
            lines.append(f"退出码: {pr.returncode}")
        except (OSError, subprocess.SubprocessError) as exc:
            lines.append(f"启动失败: {exc}")
    else:
        lines.append("（文件缺失，无法测试）")

    lines += ["", "--- PATH ---", os.environ.get("PATH", "")[:800]]

    log = HERE / "capture.log"
    if log.exists():
        lines += ["", "--- capture.log（末尾 60 行）---"]
        try:
            text = log.read_bytes().decode("utf-8", errors="replace")
            lines += text.strip().splitlines()[-60:]
        except OSError as exc:
            lines.append(f"  （读取失败：{exc}）")

    out = HERE / "诊断信息.txt"
    try:
        out.write_text("\n".join(lines), encoding="gbk", errors="replace")
        print(f"\n  诊断信息已写入：{out.name}")
        print("  请把这个文件发给管理员。")
    except OSError:
        pass
    return out


def smoke_test_runtime() -> tuple[bool, str]:
    """自检：内置 Python 能否运行、mitmdump 能否真正启动。

    这两项能直接定位问题，而不是等到"代理启动失败"再猜。
    注意杀毒软件可能拦截 mitmproxy 相关文件，这里失败时给出明确提示。
    """
    lines: list[str] = []

    if not PY.exists():
        return False, f"缺少 {PY.relative_to(HERE)}（压缩包可能解压不完整）"
    if not MITMDUMP.exists():
        return False, f"缺少 {MITMDUMP.relative_to(HERE)}（压缩包可能解压不完整）"

    # 检查 1：内置 Python 能 import mitmproxy
    try:
        p = subprocess.run(
            [str(PY), "-c",
             "import sys; sys.stdout.reconfigure(encoding='utf-8');"
             "from mitmproxy.tools.main import mitmdump;"
             "print('python', sys.version.split()[0]); print('import ok')"],
            capture_output=True, timeout=180, cwd=str(HERE),
        )
        out = ((p.stdout or b"") + (p.stderr or b"")).decode("utf-8", errors="replace")
        lines.append(out.strip())
        if "import ok" not in out:
            lines.append("内置 Python 无法导入 mitmproxy（可能被杀软拦截了相关文件）")
            return False, "\n".join(lines)
    except (OSError, subprocess.SubprocessError) as exc:
        lines.append(f"无法运行内置 Python：{exc}")
        return False, "\n".join(lines)

    # 检查 2：mitmdump 能真正启动
    try:
        p2 = subprocess.run(
            [str(MITMDUMP), "--version"], capture_output=True, timeout=180, cwd=str(HERE),
        )
        out2 = ((p2.stdout or b"") + (p2.stderr or b"")).decode("utf-8", errors="replace")
        lines.append(out2.strip())
        if p2.returncode != 0 or "Mitmproxy" not in out2:
            lines.append(f"mitmdump 退出码 {p2.returncode}，无法运行")
            return False, "\n".join(lines)
    except (OSError, subprocess.SubprocessError) as exc:
        lines.append(f"无法启动 mitmdump：{exc}")
        return False, "\n".join(lines)

    return True, "\n".join(lines)


def mitmdump_command() -> list[str]:
    """返回 mitmdump 的调用方式（pip 装出来的薄启动器）。"""
    return [str(MITMDUMP)]


def ensure_ca() -> bool:
    """确保本机 CA 证书已生成。

    **分发包里不含任何证书/私钥** —— 每台机器首次运行时由本机 mitmdump
    自己生成，私钥互不相同（公开分发不能共用一把 CA 私钥）。
    生成位置是工具包内的 ``.mitmproxy``，跑完由 2-finish 删除。
    """
    if CA_FILE.exists() and CA_FILE.stat().st_size > 0:
        print(f"  证书已就绪（{CA_FILE.stat().st_size} 字节）")
        return True

    print("  首次运行，正在生成本机证书…")
    return _generate_ca_fallback()


def _generate_ca_fallback() -> bool:
    """在本机生成一次 CA 证书。

    先尝试直接生成到工具包目录；某些路径（中文/括号/空格）或权限问题
    会导致失败，此时退到纯 ASCII 临时目录生成再复制回来。
    """
    import tempfile

    attempts: list[tuple[str, Path]] = [("工具包目录", CONFDIR)]
    try:
        attempts.append(("临时目录", Path(tempfile.mkdtemp(prefix="asi-ca-"))))
    except OSError:
        pass

    for label, workdir in attempts:
        print(f"  正在生成证书（{label}）…")
        try:
            workdir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            print(f"    ✗ 无法创建目录：{exc}")
            continue
        target = workdir / "mitmproxy-ca-cert.cer"
        if target.exists():
            target.unlink()

        log_path = HERE / "capture.log"
        try:
            fh = log_path.open("ab")
        except OSError:
            fh = None
        try:
            proc = subprocess.Popen(
                [str(MITMDUMP), "--set", f"confdir={workdir}", "-p", str(PORT + 1)],
                stdout=fh or subprocess.DEVNULL,
                stderr=subprocess.STDOUT if fh else subprocess.DEVNULL,
                cwd=str(workdir),
            )
        except OSError as exc:
            print(f"    ✗ 无法启动 mitmdump：{exc}")
            if fh:
                fh.close()
            continue

        found = False
        for _ in range(60):
            time.sleep(0.5)
            if target.exists() and target.stat().st_size > 0:
                found = True
                break
            if proc.poll() is not None:
                break

        exit_code = proc.poll()
        exited_early = exit_code is not None
        if not exited_early:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
            exit_code = proc.poll()
        if fh:
            fh.close()

        if not found:
            log_text = ""
            if log_path.exists() and log_path.stat().st_size:
                try:
                    log_text = log_path.read_bytes().decode("utf-8", errors="replace")
                except OSError:
                    pass
            print("    ✗ 未生成证书")
            print(f"      mitmdump 退出码: {exit_code}"
                  f"{'（启动后立即退出）' if exited_early else '（仍在运行但没生成）'}")
            if log_text.strip():
                for line in log_text.strip().splitlines()[-10:]:
                    print(f"      | {line}")
            else:
                print("      | 日志为空 —— 进程无法启动（缺 VC++ 运行库或被杀软拦截）")
        elif found:
            if workdir != CONFDIR:
                # 在临时目录生成的，复制回工具包目录
                CONFDIR.mkdir(parents=True, exist_ok=True)
                for src in workdir.glob("mitmproxy-*"):
                    shutil.copy2(src, CONFDIR / src.name)
                shutil.rmtree(workdir, ignore_errors=True)
            if CA_FILE.exists() and CA_FILE.stat().st_size > 0:
                print(f"    ✓ 证书已就绪（{CA_FILE.stat().st_size} 字节）")
                return True
            print("    ✗ 生成成功但复制失败")

        # 清理本次尝试的临时目录（工具包目录不能删）
        if workdir != CONFDIR:
            shutil.rmtree(workdir, ignore_errors=True)

    print("""
  ─────────────────────────────────────────────────────
  无法获得证书。请把本窗口截图 + 工具包目录里的
  诊断信息.txt 一起发给管理员。
  ─────────────────────────────────────────────────────""")
    return False


def install_ca() -> bool:
    if not CA_FILE.exists():
        print(f"  ✗ 找不到证书文件：{CA_FILE}")
        return False

    # 1) 先试用户级（通常不需要管理员权限）
    code, out = run(["certutil", "-user", "-addstore", "-f", "Root", str(CA_FILE)])
    if code == 0:
        CA_INSTALLED_FLAG.write_text("user", encoding="utf-8")
        return True
    user_err = out.strip().splitlines()
    print(f"  用户级安装未成功：{user_err[-1] if user_err else '(无输出)'}")

    # 2) 再试系统级（会弹 UAC 管理员确认）
    print()
    print("  正在尝试系统级安装 —— 可能会弹出「用户账户控制」窗口，")
    print("  请点击【是】。若没有弹窗或你点了「否」，这里就会失败。")
    code, out = run(["certutil", "-addstore", "-f", "Root", str(CA_FILE)])
    if code == 0:
        CA_INSTALLED_FLAG.write_text("machine", encoding="utf-8")
        return True

    print()
    print(f"  系统级安装也失败：{out.strip().splitlines()[-1] if out.strip() else '(无输出)'}")
    print("""
  ─────────────────────────────────────────────────────
  证书安装失败，常见原因：

  1. 点了 UAC 弹窗的「否」
     → 重新运行本程序，弹窗时务必点【是】

  2. 电脑由学校/公司统一管理，策略禁止安装根证书
     → 请找管理员当面处理

  3. 杀毒软件拦截了证书写入
     → 临时关闭杀软后重试

  请把本窗口截图发给管理员。
  ─────────────────────────────────────────────────────""")
    return False


def uninstall_ca() -> bool:
    """卸载临时证书（回滚用）。"""
    if not CA_INSTALLED_FLAG.exists():
        return True
    scope = CA_INSTALLED_FLAG.read_text(encoding="utf-8").strip() or "user"
    if scope == "user":
        ps = ("Get-ChildItem Cert:\\CurrentUser\\Root | "
              "Where-Object { $_.Subject -like '*mitmproxy*' } | "
              "ForEach-Object { $_.Thumbprint }")
        _, out = run(["powershell", "-NoProfile", "-Command", ps])
        for thumb in [t.strip() for t in out.splitlines() if len(t.strip()) == 40]:
            run(["powershell", "-NoProfile", "-Command",
                 f"Remove-Item -Path 'Cert:\\CurrentUser\\Root\\{thumb}' -Force"])
    else:
        run(["certutil", "-delstore", "Root", str(CA_FILE)])
    CA_INSTALLED_FLAG.unlink(missing_ok=True)
    return True


def main() -> int:
    os.chdir(HERE)
    banner("中南林学工 · 自动签到授权工具")
    print("""
本工具会：
  1. 安装一个临时证书（让微信愿意把登录请求交给本机代理）
  2. 启动本机抓包代理
  3. 等你登录小程序后，抓取登录凭据

结束后会自动卸载证书、关闭代理。不会上传任何东西。
""")
    if ask("确认开始？输入 yes 回车：").strip().lower() not in ("yes", "y"):
        print("已取消。")
        return 0

    banner("第 0 步：自检运行环境")
    ok, detail = smoke_test_runtime()
    if not ok:
        print("  ✗ 包内运行环境异常：")
        for line in detail.strip().splitlines()[-12:]:
            print(f"      {line}")
        print("""
  ─────────────────────────────────────────────────────
  mitmdump.exe 无法运行。常见原因：

  1. 压缩包解压不完整
     → 删掉文件夹重新完整解压

  2. 杀毒软件隔离了 mitmdump.exe
     → 把整个文件夹加入白名单后重新解压

  请把本窗口截图 + 诊断信息.txt 发给管理员。
  ─────────────────────────────────────────────────────""")
        dump_diagnostics("运行时自检失败（无法导入 mitmproxy）")
        return 1
    first = detail.strip().splitlines()[0] if detail.strip() else ""
    print(f"  ✓ 运行环境正常（{first}）")

    banner("第 1 步：准备并安装临时证书")
    if not ensure_ca():
        print("\n✗ 无法生成证书。")
        dump_diagnostics("生成 CA 证书")
        return 1
    if not install_ca():
        print("\n✗ 证书安装失败，无法继续。")
        dump_diagnostics("安装 CA 证书到受信任根")
        return 1
    print("  ✓ 证书已安装到「受信任的根证书颁发机构」")

    banner("第 2 步：启动抓包代理")
    CAPTURE.mkdir(exist_ok=True)
    for old in CAPTURE.rglob("*"):
        if old.is_file():
            old.unlink()
    mitm_cmd = mitmdump_command()
    if len(mitm_cmd) > 1:
        print("  （启动器不可用，改用 python -m 方式调用）")
    log = (HERE / "capture.log").open("wb")
    proc = subprocess.Popen(
        [*mitm_cmd,
         "--set", f"confdir={CONFDIR}",
         "-p", str(PORT), "-s", str(HERE / "capture_addon.py"),
         "--set", "connection_strategy=lazy", "--set", "http2=false"],
        stdout=log, stderr=subprocess.STDOUT, cwd=str(HERE),
    )
    # 等端口就绪
    import socket
    ready = False
    for _ in range(60):
        time.sleep(0.5)
        with socket.socket() as s:
            s.settimeout(0.3)
            if s.connect_ex(("127.0.0.1", PORT)) == 0:
                ready = True
                break
    if not ready:
        # 失败时必须报出真实原因，否则只能靠猜
        print("  ✗ 代理启动失败")
        code = proc.poll()
        print(f"     进程状态: {'已退出（退出码 ' + str(code) + '）' if code is not None else '仍在运行但未监听端口'}")
        try:
            log_text = (HERE / "capture.log").read_bytes().decode("utf-8", errors="replace")
        except OSError:
            log_text = ""
        if log_text.strip():
            print("     日志末尾：")
            for line in log_text.strip().splitlines()[-12:]:
                print(f"       | {line}")
        else:
            print("     日志为空 —— 进程没能正常启动或没来得及输出")

        # 端口是否被别的程序占用
        try:
            with socket.socket() as s:
                s.settimeout(0.5)
                busy = s.connect_ex(("127.0.0.1", PORT)) == 0
            if busy:
                print(f"     ⚠ 端口 {PORT} 已被其它程序占用")
                print("       请关闭占用该端口的程序（常见：其它抓包工具、代理软件），或重启电脑")
        except OSError:
            pass

        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        if CA_INSTALLED_FLAG.exists():
            uninstall_ca()
        print()
        print("  请把本窗口截图发给管理员。已自动回滚（卸载证书、结束进程）。")
        dump_diagnostics("启动抓包代理")
        return 1
    print(f"  ✓ 代理已就绪（127.0.0.1:{PORT}）")

    if not patch_proxy(True):
        # 失败必须回滚：否则会留下一个开着的代理和一个装上的证书，
        # 同学那边上网就用不了了。
        print("  正在回滚（卸载证书、结束代理）…")
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        if CA_INSTALLED_FLAG.exists():
            uninstall_ca()
        print("\n✗ 设置系统代理失败，已回滚。请联系管理员。")
        return 1
    print("  ✓ 系统代理已指向本机代理")

    (HERE / ".mitm-pid").write_text(str(proc.pid), encoding="utf-8")

    banner("第 3 步：现在去操作微信")
    print("""
  ① 完全退出微信
     右下角托盘图标右键 → 退出
     （托盘里没有就按 Ctrl+Shift+Esc，把所有 WeChat/Weixin 结束任务）
     ⚠️ 只点窗口右上角的 × 是没用的！

  ② 重新打开微信，进入「中南林学工」小程序
     如果提示未登录，正常登录
     登录后点进底部「打卡」页，停留 5 秒

  ③ 回到本窗口，双击「2-finish.bat」
""")
    ask("\n按回车关闭本窗口（代理会继续在后台运行）…")
    print("\n代理仍在后台运行。请完成微信操作后，双击「2-finish.bat」。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException as _exc:  # noqa: BLE001 - 任何异常都要留下线索
        # 故意**不**打印 traceback：使用者看到窗口里滚出一堆源码与英文堆栈
        # 只会困惑。完整堆栈写进诊断信息.txt，让管理员去看。
        _kind = type(_exc).__name__
        print()
        print("=" * 62)
        print("工具遇到未预期的错误")
        print("=" * 62)

        if isinstance(_exc, EOFError):
            print("  程序需要在这里等你输入，但读取输入失败了。")
            print()
            print("  这通常是因为运行方式不对。请直接在本文件夹里")
            print("  双击「1-start.bat」运行，不要用其它方式启动。")
        elif isinstance(_exc, KeyboardInterrupt):
            print("  你按了 Ctrl+C，已中断。")
        else:
            print(f"  错误类型：{_kind}")
            print(f"  错误信息：{_exc}")
            print()
            print("  如果你看不懂这个错误，不用管它 —— 下面的诊断文件")
            print("  里记录了完整细节，发给管理员即可。")

        try:
            dump_diagnostics(f"异常：{_kind}")
            print()
            print("  诊断信息已写入：诊断信息.txt")
        except Exception:  # noqa: BLE001
            pass

        print()
        print("  请把本窗口截图 + 诊断信息.txt 发给管理员。")
        ask("按回车退出…")
        raise SystemExit(1) from _exc
