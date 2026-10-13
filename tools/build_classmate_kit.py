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

# 面向使用者的 README 以 **dist 里已维护好的那一份为唯一源**。
# 理由：README 的文案会随着分发对象不断调整，内嵌副本迟早与实物不一致
# （曾出现过：dist 里改了 66 行，内嵌模板仍是旧版，重新打包会覆盖掉修改）。
# 现在只维护一份 —— 直接编辑这个文件，打包脚本原样复制。
#
#   dist/checkin-auth-tool/checkin-auth-tool/README.txt
#
# 注意：该文件是 **GBK** 编码（中文 Windows 的记事本按 ANSI 打开 txt，
# 用 UTF-8 存会显示成乱码）。脚本按 GBK 读入、按 GBK 写出，不改编码。
README_SRC = DIST / KIT_NAME / KIT_NAME / "README.txt"

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

# README 的文案不在这里 —— 见上方 README_SRC。
# 打包脚本以 dist 里维护的那一份为唯一源，避免内嵌副本与实物不一致。


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

    # kit_capture.py / kit_finish.py 也走「单一源 + 复制」，
    # 与 capture_addon.py / doctor.py 一致。
    # 源文件在 tools/ 下，直接编辑那里即可（不要改 dist 里的副本，
    # 它每次打包都会被覆盖）。
    for _src_name in ("kit_capture.py", "kit_finish.py"):
        shutil.copy2(ROOT / "tools" / _src_name, PAYLOAD / _src_name)

    # README.txt：**原样复制 dist 里维护的那一份**（唯一源，见 README_SRC 注释）。
    # 保持 GBK 编码：中文 Windows 的记事本按 ANSI 打开 txt，
    # 用 UTF-8 存（无 BOM）会显示成乱码。txt 里没有 chcp，不受代码页影响。
    if not README_SRC.exists():
        raise SystemExit(
            f"\n✗ 找不到 README 源文件：\n    {README_SRC}\n\n"
            "  打包脚本以这份文件为唯一源（不再内嵌副本）。\n"
            "  请先创建它，再重新运行打包。\n"
        )
    _readme = README_SRC.read_bytes()
    (PAYLOAD / "README.txt").write_bytes(_readme)
    print(f"  README：{len(_readme):,} 字节 ← {README_SRC.relative_to(ROOT)}")

    # .bat 必须用 **UTF-8 + BOM**，不能用 GBK。
    # 原因：这三个脚本第 2 行都执行 `chcp 65001`（把控制台切到 UTF-8）。
    # 若文件本身是 GBK 字节，cmd.exe 从第 3 行起按 UTF-8 解析 GBK 中文，
    # 于是所有中文提示乱码。BOM 则让编码对任何代码页都无歧义。
    # （实测：GBK+chcp65001 → 乱码；UTF-8 或 UTF-8+BOM + chcp65001 → 正确）
    for _name, _body in (
        ("1-start.bat", BAT_START),
        ("2-finish.bat", BAT_STOP),
        ("check-env.bat", BAT_DOCTOR),
    ):
        _text = fill(_body).replace("\r\n", "\n").replace("\n", "\r\n")
        (PAYLOAD / _name).write_bytes(b"\xef\xbb\xbf" + _text.encode("utf-8"))
    print(f"  称呼：{ADMIN_NAME}    结果文件：{RESULT_FILE}")
    # ---------------------------------------------------------------- 压缩 --
    hr("6. 打包")
    DIST.mkdir(parents=True, exist_ok=True)
    out_zip = DIST / ZIP_NAME
    if out_zip.exists():
        out_zip.unlink()
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        skipped = 0
        for path in sorted(PAYLOAD.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(PAYLOAD)
            # 排除 Python 字节码缓存：它是本机运行脚本时自动生成的，
            # 含绝对路径与解释器版本，因人而异，不该发给使用者。
            if "__pycache__" in rel.parts or rel.suffix in (".pyc", ".pyo"):
                skipped += 1
                continue
            # 相对 PAYLOAD 写入，解压后只得到一层 checkin-auth-tool/
            # （以前写 relative_to(BUILD) 会多套一层同名目录）
            zf.write(path, Path(KIT_NAME) / rel)
        if skipped:
            print(f"  （已排除 {skipped} 个字节码缓存文件）")
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



BAT_START = """\
@echo off
chcp 65001 >nul 2>&1
title 中南林学工 - 授权抓包（第1步：开始抓包）
cd /d "%~dp0"

if not exist "python\\Scripts\\mitmdump.exe" goto :missing
if not exist "python\\python.exe" goto :missing
goto :run

:missing
echo.
echo   [错误] 工具包文件不完整，缺少内置的抓包程序。
echo.
echo   应该存在但没找到：
echo       python\\python.exe
echo       python\\Scripts\\mitmdump.exe
echo.
echo   常见原因有这几种，请按顺序排查：
echo.
echo     1) 直接在压缩包里双击运行了。
echo        请先把整个文件夹解压到磁盘上再运行。
echo.
echo     2) 压缩包没解压完整，或解压中途被打断。
echo        请删掉旧文件夹，重新完整解压一次。
echo.
echo     3) 文件夹被移动或改名，导致内部文件丢失。
echo        请把整个 checkin-auth-tool 文件夹整体复制过去。
echo.
echo     4) 杀毒软件把 python\\Scripts\\mitmdump.exe 删掉了。
echo        这是抓包类工具常见的误报。请查看杀毒软件的
echo        「隔离区」并还原该文件，或先把文件夹加入白名单，
echo        然后重新解压。
echo.
pause
exit /b 1

:run
rem 优先用内置 Python，其次用系统的
set "PYCMD="
if exist "python\\python.exe" set "PYCMD=python\\python.exe"
if not defined PYCMD ( where py >nul 2>&1 && set "PYCMD=py" )
if not defined PYCMD ( where python >nul 2>&1 && set "PYCMD=python" )

if not defined PYCMD (
  echo.
  echo   [错误] 找不到可用的 Python。
  echo.
  echo   这个工具包自带 Python，正常情况下不需要你另外安装。
  echo   出现这个提示，说明包内的 Python 缺失或被杀毒软件隔离了。
  echo.
  echo   请先重新完整解压一次；仍然不行就双击「check-env.bat」
  echo   并把窗口内容截图发给{ADMIN_NAME}。
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
  echo   [错误] 找不到可用的 Python。
  echo.
  echo   这个工具包自带 Python，正常情况下不需要你另外安装。
  echo   出现这个提示，说明包内的 Python 缺失或被杀毒软件隔离了。
  echo.
  echo   请先重新完整解压一次；仍然不行就双击「check-env.bat」
  echo   并把窗口内容截图发给{ADMIN_NAME}。
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
  echo   [错误] 找不到可用的 Python。
  echo.
  echo   这个工具包自带 Python，正常情况下不需要你另外安装。
  echo   出现这个提示，说明包内的 Python 缺失或被杀毒软件隔离了。
  echo.
  echo   请先重新完整解压一次，再运行本诊断。
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
