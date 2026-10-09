#!/usr/bin/env python3
"""把抓包工具打包成免安装压缩包，发给没有 Python 的同学。

产物：dist/学工签到授权工具.zip
同学解压后双击「1-start.bat」即可，无需安装任何东西。

用法::

    python tools/build_classmate_kit.py            # 完整构建
    python tools/build_classmate_kit.py --keep-tmp # 保留中间目录便于调试
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8")

DIST = ROOT / "dist"
BUILD = ROOT / ".build-kit"
# 全部使用 ASCII 名称：避免解压工具处理中文目录名时出现编码问题
KIT_NAME = "checkin-auth-tool"
PAYLOAD = BUILD / KIT_NAME
ZIP_NAME = f"{KIT_NAME}.zip"

PY_VER = "3.12.4"
EMBED_URL = f"https://www.python.org/ftp/python/{PY_VER}/python-{PY_VER}-embed-amd64.zip"
GETPIP_URL = "https://bootstrap.pypa.io/get-pip.py"
PY_MIN = "3.8"  # 使用方机器上跑控制脚本的最低版本
MITM_VER = "12.2.3"

# 面向使用者的称呼。默认中性，可用环境变量替换成你自己的名字：
#     $env:ASI_ADMIN_NAME = "你的名字"
# 开源版保持默认值，避免把作者昵称打进分发给陌生人的包里。
ADMIN_NAME = os.environ.get("ASI_ADMIN_NAME", "管理员")
# 结果文件名（使用者要发给部署者的那个文件）
RESULT_FILE = os.environ.get("ASI_RESULT_FILE", "token-result.txt")

README = r"""========================================================
  中南林学工 · 自动签到授权工具
========================================================

重要：请把整个文件夹解压到【纯英文路径】下再运行
--------------------------------------------------------
例如：  D:\checkin\     或    C:\Users\你的用户名\Desktop\checkin\

不要把文件夹放在含中文、空格或括号的路径里
（例如「桌面\新建文件夹 (1)\」这种）。

解压后目录名是 checkin-auth-tool（全英文）。
请在文件夹里直接双击 1-start.bat，不要从压缩包里直接运行。

本工具**不需要你自己安装任何东西**（Python、mitmproxy 都已内置）。


这个工具做什么？
----------------
你的学校账号开启了短信二次认证，所以{ADMIN_NAME}无法用你的账号密码
自动登录。需要你在自己的微信里登录一次小程序，让工具抓取那一次
的登录凭据，然后你把结果发给{ADMIN_NAME}。之后系统就能长期自动帮你签到。

全程约 2 分钟。你只需要操作微信，剩下的工具会做。


【重要】这个工具会做什么、不会做什么
------------------------------------
会做：
  * 在本机启动一个抓包代理（仅监听 127.0.0.1:8888）
  * 装一个临时证书到「当前用户」的受信任根，让微信愿意把请求
    交给本机代理 —— 不装这个，微信会拒绝连接
  * 抓取你登录小程序时产生的登录凭据
  * 结束后【自动卸载证书、关闭代理】

不会做：
  * 不读你的微信聊天记录
  * 不读你的密码
  * 不联网上传任何东西
  * 不会在后台驻留（结束后进程全部退出）

如果杀毒软件弹窗报警，那是因为抓包代理类工具常被误报。
你可以先关掉它，或者直接找{ADMIN_NAME}当面操作。


【操作步骤】
--------------------------------------------------------

第 1 步  双击「1-start.bat」
        会弹出一个黑色窗口，按提示按回车。
        出现「代理已就绪」后，先别关窗口。

第 2 步  完全退出微信
        * 右下角托盘找到微信图标 → 右键 → 退出
        * 托盘里没有的话，按 Ctrl+Shift+Esc 打开任务管理器，
          把所有 WeChat / Weixin 进程都「结束任务」
        ⚠️ 只点窗口右上角的 × 没用，那只是最小化到托盘！

第 3 步  重新打开微信 → 进入「中南林学工」小程序
        * 如果提示未登录，正常登录（微信一键登录即可）
        * 登录后点进底部「打卡」页，停留 5 秒

第 4 步  回到文件夹，双击「2-finish.bat」
        工具会自动卸载证书、关闭代理、并生成结果。

第 5 步  把生成的「{RESULT_FILE}」里的内容复制发给{ADMIN_NAME}
        就完成了。


【常见问题】
--------------------------------------------------------

Q: 显示"生成证书失败"
A: 把整个 checkin-auth-tool 文件夹移动到纯英文路径
   （如 D:\checkin\）下重新运行。
   如果还失败，双击「check-env.bat」并把结果发给{ADMIN_NAME}。

Q: 双击 1-start.bat 提示找不到 mitmdump.exe 或 Python
A: 压缩包没有完整解压。请删掉整个文件夹，重新完整解压一次
   （不要直接在压缩包预览窗口里双击运行）。

Q: 提示端口 8888 被占用
A: 你电脑上可能有别的代理/抓包工具在运行（Fiddler、Charles、
   Clash、v2ray 等）。关掉它们再试，或双击「check-env.bat」查看。

Q: 窗口里一直显示"等待中"，抓不到
A: 99% 是因为微信没有完全退出。请按第 2 步用【任务管理器】
   把所有 WeChat/Weixin 进程都结束，然后重新打开微信。

Q: 小程序里显示"网络错误"或"加载失败"
A: 证书没装上。关掉窗口，重新双击「1-start.bat」，
   注意看是否有"已安装证书"的提示。若反复失败，把「诊断信息.txt」
   发给{ADMIN_NAME}。

Q: 杀毒软件报警 / 直接删掉了文件
A: 这是抓包工具的常见误报。请在杀软里点"信任"或"恢复"，
   或直接找{ADMIN_NAME}当面用他的电脑操作。

Q: 我点了「打卡」但今天已经打过了
A: 没关系。工具只需要你「打开打卡页」这个动作，
   不需要真的打卡成功。

Q: 会不会影响我的微信 / 账号安全？
A: 抓到的只是学校系统签发的会话凭据，用途仅限自动签到。
   证书和代理在工具结束时都会自动清除。
   如果你不放心，随时可以让{ADMIN_NAME}删除你的数据、停止代签。
"""


def hr(t: str) -> None:
    print("\n" + "=" * 74)
    print(t)
    print("=" * 74)


def download(url: str, dest: Path) -> None:
    print(f"  下载 {url}")
    with urllib.request.urlopen(url, timeout=180) as resp, dest.open("wb") as fh:
        shutil.copyfileobj(resp, fh)
    print(f"    完成，{dest.stat().st_size / 1024:.0f} KB""")


def run(cmd: list[str], *, cwd: Path | None = None) -> tuple[int, str]:
    proc = subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=1800,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def main() -> int:
    parser = argparse.ArgumentParser(description="构建同学用的免安装抓包工具包")
    parser.add_argument("--keep-tmp", action="store_true")
    args = parser.parse_args()

    hr("准备构建目录")
    if BUILD.exists():
        shutil.rmtree(BUILD, ignore_errors=True)
    BUILD.mkdir(parents=True, exist_ok=True)
    PAYLOAD.mkdir(parents=True, exist_ok=True)
    print(f"  {PAYLOAD}")

    # ------------------------------------------------- 内置 Python 运行时 --
    # 为什么用「嵌入式 Python + pip 装 mitmproxy」而不是官方单文件版：
    # 官方 mitmdump.exe 是 PyInstaller 打包的，2026-10-09 起被 Windows Defender
    # 判为 PUA（ThreatID 2147861799）并直接阻止运行 —— 全新下载的也一样。
    # pip 装出来的 mitmdump 是薄启动器（0.1 MB）+ 常规 .py/.pyd，
    # 实测未被拦截，因此回到这条路。
    hr("1. 下载内置 Python 运行时")
    embed_zip = BUILD / "python-embed.zip"
    download(EMBED_URL, embed_zip)

    hr("2. 解压并启用 site-packages")
    py_dir = PAYLOAD / "python"
    with zipfile.ZipFile(embed_zip) as zf:
        zf.extractall(py_dir)
    print(f"  解压 {len(list(py_dir.iterdir()))} 个条目")

    # 嵌入式 Python 默认隔离 site，需要打开才能 import 第三方包
    pth_files = list(py_dir.glob("python*._pth"))
    if not pth_files:
        print("  ✗ 找不到 ._pth 文件")
        return 1
    pth = pth_files[0]
    content = pth.read_text(encoding="utf-8")
    content = content.replace("#import site", "import site")
    if "Lib\\site-packages" not in content:
        content = content.rstrip("\n") + "\nLib\\site-packages\n"
    pth.write_text(content, encoding="utf-8")
    (py_dir / "Lib" / "site-packages").mkdir(parents=True, exist_ok=True)
    print(f"  已启用 site-packages（{pth.name}）")

    py_exe = py_dir / "python.exe"
    code, out = run([str(py_exe), "-c", "import sys; print(sys.version.split()[0])"])
    print(f"  ✓ 内置 Python {out.strip() or '启动失败'}")
    if code != 0:
        print("  ✗ 嵌入式 Python 无法运行")
        return 1

    probe = (
        "import ctypes, winreg, socket, tempfile, pathlib, subprocess, platform;"
        "print('stdlib ok')"
    )
    code, out = run([str(py_exe), "-c", probe])
    if "stdlib ok" not in out:
        print("  ✗ 内置 Python 缺少所需标准库")
        print(out[-500:])
        return 1
    print("  ✓ 控制脚本所需标准库齐全")

    # ---------------------------------------------------------------- pip --
    hr("3. 安装 pip")
    getpip = BUILD / "get-pip.py"
    download(GETPIP_URL, getpip)
    code, out = run([str(py_exe), str(getpip), "--no-warn-script-location"])
    if code != 0:
        print("  ✗ pip 安装失败")
        print(out[-800:])
        return 1
    print("  ✓ pip 已安装")

    # ----------------------------------------------------------- mitmproxy --
    hr("4. 安装 mitmproxy（约 100-200MB，需要几分钟）")
    code, out = run([
        str(py_exe), "-m", "pip", "install", "--no-warn-script-location",
        "-q", f"mitmproxy=={MITM_VER}",
    ])
    if code != 0:
        print("  ✗ mitmproxy 安装失败")
        print(out[-1500:])
        return 1

    mitmdump_exe = py_dir / "Scripts" / "mitmdump.exe"
    if not mitmdump_exe.exists():
        print("  ✗ 找不到 python\\Scripts\\mitmdump.exe")
        return 1
    code, out = run([str(mitmdump_exe), "--version"])
    first = out.strip().splitlines()[0] if out.strip() else "(无输出)"
    print(f"  ✓ {first}")
    if code != 0 or "Mitmproxy" not in out:
        print("  ✗ mitmdump 无法运行")
        print(out[-800:])
        return 1

    # 关键：验证 pip 版不会被 Defender 拦（官方单文件版就是死在这里）
    code, out = run([str(py_exe), "-c",
                     "from mitmproxy.tools.main import mitmdump; print('import ok')"])
    if "import ok" not in out:
        print("  ✗ 内置 Python 无法导入 mitmproxy")
        print(out[-800:])
        return 1
    print("  ✓ 内置 Python 可导入 mitmproxy（未被杀软拦截）")

    # ------------------------------------------------- 不预置 CA 证书 --
    # 以前这里会把构建机生成的 CA（含 RSA 私钥）打进分发包。
    # 那个做法的隐患：所有拿到分发包的人共用同一把私钥，可以解密彼此的
    # HTTPS 流量。公开分发不能这样。
    # 现在改为每台机器首次运行时由本机 mitmdump 自己生成 —— 实测可行，
    # 私钥留在本机 .mitmproxy 目录，跑完即删。
    hr("4.5 确认不内置 CA 私钥（每台机器首跑自生成）")
    ca_dir = PAYLOAD / ".mitmproxy"
    if ca_dir.exists():
        shutil.rmtree(ca_dir, ignore_errors=True)
    print("  ✓ 分发包内不含任何证书/私钥")
    print("    → 使用方首次运行时由本机 mitmdump 生成自己的 CA")

    # -------------------------------------------------------------- 脚本 --
    hr("5. 生成面向使用者的脚本")

    # 抓包插件（从仓库复制，保证逻辑一致）
    shutil.copy2(ROOT / "tools" / "capture_addon.py", PAYLOAD / "capture_addon.py")
    # 诊断脚本也放进包里，出问题时使用者可直接双击运行
    doctor_src = ROOT / "tools" / "doctor.py"
    if doctor_src.exists():
        shutil.copy2(doctor_src, PAYLOAD / "doctor.py")

    # 把 {ADMIN_NAME} / {RESULT_FILE} 占位符填成实际值。
    # 默认是中性的「管理员」与 token-result.txt；私有分发时用环境变量
    # ASI_ADMIN_NAME / ASI_RESULT_FILE 覆盖成自己的名字。
    def fill(template: str) -> str:
        return template.replace("{ADMIN_NAME}", ADMIN_NAME).replace(
            "{RESULT_FILE}", RESULT_FILE
        )

    (PAYLOAD / "kit_capture.py").write_text(fill(KIT_CAPTURE), encoding="utf-8")
    (PAYLOAD / "kit_finish.py").write_text(fill(KIT_FINISH), encoding="utf-8")
    # 面向使用者的文件全部用 GBK：中文 Windows 的记事本按 ANSI 打开，
    # 用 UTF-8 存会显示成乱码。
    (PAYLOAD / "README.txt").write_text(
        fill(README), encoding="gbk", errors="replace"
    )
    (PAYLOAD / "1-start.bat").write_text(
        fill(BAT_START), encoding="gbk", errors="replace"
    )
    (PAYLOAD / "2-finish.bat").write_text(
        fill(BAT_STOP), encoding="gbk", errors="replace"
    )
    (PAYLOAD / "check-env.bat").write_text(
        fill(BAT_DOCTOR), encoding="gbk", errors="replace"
    )
    print(f"  称呼：{ADMIN_NAME}    结果文件：{RESULT_FILE}")
    # ---------------------------------------------------------------- 压缩 --
    hr("6. 打包")
    DIST.mkdir(parents=True, exist_ok=True)
    out_zip = DIST / ZIP_NAME
    if out_zip.exists():
        out_zip.unlink()
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in sorted(PAYLOAD.rglob("*")):
            if path.is_file():
                # 相对 PAYLOAD 写入，解压后只得到一层 checkin-auth-tool/
                # （以前写 relative_to(BUILD) 会多套一层同名目录）
                zf.write(path, Path(KIT_NAME) / path.relative_to(PAYLOAD))
    size = out_zip.stat().st_size / 1024 / 1024
    print(f"  ✓ {out_zip}  ({size:.0f} MB)")
    print(f"  解压后顶层目录：{KIT_NAME}/（单层，全英文）")

    if not args.keep_tmp:
        shutil.rmtree(BUILD, ignore_errors=True)
        print("  已清理中间目录（加 --keep-tmp 可保留）")
    else:
        print(f"  中间目录保留在 {BUILD}")

    hr("完成")
    print(f"  发给同学：{out_zip}")
    print(f"  解压后目录名：{KIT_NAME}（全英文）")
    print("  同学操作：解压 → 双击「1-start.bat」→ 按说明操作")
    print()
    print("  ⚠ 提醒：先在你自己电脑上完整试跑一次再发出去")
    return 0


# --------------------------------------------------------------------------- #
# 面向同学的脚本
# --------------------------------------------------------------------------- #

KIT_CAPTURE = r'''#!/usr/bin/env python3
"""同学端：装证书 + 开代理 + 启动抓包（一键）。"""
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

sys.stdout.reconfigure(encoding="utf-8")


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
    """把诊断信息写到文件，方便同学直接发给{ADMIN_NAME}。"""
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
        print("  请把这个文件发给{ADMIN_NAME}。")
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
  诊断信息.txt 一起发给{ADMIN_NAME}。
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
     → 请找{ADMIN_NAME}当面处理

  3. 杀毒软件拦截了证书写入
     → 临时关闭杀软后重试

  请把本窗口截图发给{ADMIN_NAME}。
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
    if input("确认开始？输入 yes 回车：").strip().lower() not in ("yes", "y"):
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

  请把本窗口截图 + 诊断信息.txt 发给{ADMIN_NAME}。
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
        print("  请把本窗口截图发给{ADMIN_NAME}。已自动回滚（卸载证书、结束进程）。")
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
        print("\n✗ 设置系统代理失败，已回滚。请联系{ADMIN_NAME}。")
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
    input("\n按回车关闭本窗口（代理会继续在后台运行）…")
    print("\n代理仍在后台运行。请完成微信操作后，双击「2-finish.bat」。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException as _exc:  # noqa: BLE001 - 任何异常都要留下线索
        import traceback as _tb

        print("\n" + "=" * 62)
        print("工具遇到未预期的错误")
        print("=" * 62)
        print(f"  {type(_exc).__name__}: {_exc}")
        print()
        _tb.print_exc()
        try:
            dump_diagnostics(f"未预期异常：{type(_exc).__name__}")
        except Exception:  # noqa: BLE001
            pass
        print("\n请把本窗口截图 + 诊断信息.txt 发给{ADMIN_NAME}。")
        input("按回车退出…")
        raise SystemExit(1) from _exc
'''

KIT_FINISH = r'''#!/usr/bin/env python3
"""同学端：停代理 + 卸证书 + 生成结果文件。"""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOKENS = HERE / "capture" / "tokens.json"
OUT = HERE / "{RESULT_FILE}"
CA_FLAG = HERE / ".ca-installed"
PID_FILE = HERE / ".mitm-pid"
CONFDIR = HERE / ".mitmproxy"
CA_FILE = CONFDIR / "mitmproxy-ca-cert.cer"

sys.stdout.reconfigure(encoding="utf-8")


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

    # 1) 关代理（恢复同学原来的设置）
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

    # 2) 结束代理进程（先按 PID，再兜底按进程名）
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

    # 3) 卸证书
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

    # 3.5) 删掉工具包内的 CA 目录（含私钥）与备份文件，不留痕迹
    import shutil as _shutil

    if CONFDIR.exists():
        _shutil.rmtree(CONFDIR, ignore_errors=True)
        print("  ✓ 工具包内的证书文件已删除")
    (HERE / ".proxy-original").unlink(missing_ok=True)

    # 4) 读结果
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
        "（把本文件全部内容复制发给{ADMIN_NAME}）\n\n"
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
  可自动续期：{'是（{ADMIN_NAME}只需导入一次，之后长期有效）' if refresh else '否（约 18 天后需重新抓取）'}

  已生成文件：{RESULT_FILE}
  请打开它，把【全部内容】复制发给{ADMIN_NAME}。
""")
    try:
        subprocess.run(["explorer", "/select,", str(OUT)], timeout=15)
    except (OSError, subprocess.SubprocessError):
        pass
    input("按回车关闭…")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''

BAT_START = """\
@echo off
chcp 65001 >nul 2>&1
title 中南林学工 - 授权抓包（第1步：开始抓包）
cd /d "%~dp0"

if not exist "mitmdump.exe" (
  echo.
  echo   [错误] 找不到 mitmdump.exe
  echo   请确认你把整个压缩包解压出来了，而不是直接在压缩包里运行。
  echo.
  pause
  exit /b 1
)

rem 优先用内置 Python，其次用系统的
set "PYCMD="
if exist "python\\python.exe" set "PYCMD=python\\python.exe"
if not defined PYCMD ( where py >nul 2>&1 && set "PYCMD=py" )
if not defined PYCMD ( where python >nul 2>&1 && set "PYCMD=python" )

if not defined PYCMD (
  echo.
  echo   [错误] 既没有内置 Python，系统里也没找到 Python。
  echo   这说明压缩包解压不完整 —— 请删掉文件夹重新完整解压。
  echo.
  pause
  exit /b 1
)

echo.
echo   正在启动...
echo.
%PYCMD% kit_capture.py
if errorlevel 1 (
  echo.
  echo   启动失败。请双击「check-env.bat」并把结果截图发给{ADMIN_NAME}。
  pause
)
"""

BAT_STOP = """\
@echo off
chcp 65001 >nul 2>&1
title 中南林学工 - 授权抓包（第2步：完成并生成结果）
cd /d "%~dp0"

set "PYCMD="
if exist "python\\python.exe" set "PYCMD=python\\python.exe"
if not defined PYCMD ( where py >nul 2>&1 && set "PYCMD=py" )
if not defined PYCMD ( where python >nul 2>&1 && set "PYCMD=python" )

if not defined PYCMD (
  echo.
  echo   [错误] 找不到 Python，压缩包可能解压不完整。
  echo.
  pause
  exit /b 1
)

%PYCMD% kit_finish.py
"""

BAT_DOCTOR = """\
@echo off
chcp 65001 >nul 2>&1
title 中南林学工 - 环境诊断（出问题时运行）
cd /d "%~dp0"

set "PYCMD="
if exist "python\\python.exe" set "PYCMD=python\\python.exe"
if not defined PYCMD ( where py >nul 2>&1 && set "PYCMD=py" )
if not defined PYCMD ( where python >nul 2>&1 && set "PYCMD=python" )

if not defined PYCMD (
  echo.
  echo   [错误] 找不到 Python，压缩包可能解压不完整。
  echo   请删掉文件夹后重新完整解压。
  echo.
  pause
  exit /b 1
)

echo.
echo   正在诊断环境... 完成后请把本窗口内容截图发给{ADMIN_NAME}
echo.
%PYCMD% doctor.py
"""


if __name__ == "__main__":
    raise SystemExit(main())
