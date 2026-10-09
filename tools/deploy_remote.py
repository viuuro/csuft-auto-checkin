#!/usr/bin/env python3
"""一键部署到 Linux 服务器。

在本机运行，通过 SSH 完成远端全部部署步骤：

    1. 预检远端环境（系统版本 / 内存 / 磁盘 / 时区 / 学校接口连通性）
    2. 打包本机项目（排除 data、抓包产物、缓存）
    3. 上传并解包到 --remote-dir（默认 /opt/autosinin）
    4. 安装依赖、创建专用用户、配置 systemd
    5. 启动服务并验证

用法::

    python tools/deploy_remote.py --host <你的服务器> --user root [--key 私钥路径]

不指定 --key 时使用密码登录（会交互式询问，不落盘）。
默认安装目录 ``/opt/autosinin``，可用 ``--remote-dir`` 改成别的路径。
"""

from __future__ import annotations

import argparse
import base64
import getpass
import io
import os
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8")

# 默认安装目录。用环境变量 ASI_REMOTE_DIR 或 --remote-dir 可覆盖，
# 方便部署到别的路径（systemd 单元与文档里出现的路径会同步替换）。
DEFAULT_REMOTE_DIR = os.environ.get("ASI_REMOTE_DIR", "/opt/autosinin")
REMOTE_DIR = DEFAULT_REMOTE_DIR
SERVICE_NAME = os.environ.get("ASI_SERVICE_NAME", "autosinin")
SERVICE_PORT = int(os.environ.get("ASI_SERVICE_PORT", "8000"))

# 打包时排除的内容
EXCLUDE_DIRS = {
    "__pycache__", ".git", "data", "capture", ".deploy-keys", ".venv", "venv",
    "src-wx0e47", "unpacked-wx0e47", "pc_wxapkg_decrypt", "pc_wxapkg_decrypt_python",
}
EXCLUDE_SUFFIX = {".pyc", ".pyo", ".wxapkg", ".db", ".db-wal", ".db-shm", ".log"}


def hr(title: str) -> None:
    print("\n" + "=" * 76)
    print(title)
    print("=" * 76)


def run(cmd: list[str], *, input_text: str | None = None, timeout: int = 900,
        check: bool = False) -> tuple[int, str, str]:
    proc = subprocess.run(
        cmd,
        input=input_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"命令失败({proc.returncode}): {' '.join(cmd)}\n{proc.stderr}")
    return proc.returncode, proc.stdout or "", proc.stderr or ""


# --------------------------------------------------------------------------- #
# SSH 封装
# --------------------------------------------------------------------------- #


class Remote:
    def __init__(self, host: str, user: str, key: str = "", password: str = "") -> None:
        self.target = f"{user}@{host}"
        self.key = key
        self.password = password
        self._askpass: Path | None = None

        if password and not key:
            self._askpass = self._make_askpass()

    def _make_askpass(self) -> Path:
        """创建一个临时 askpass 脚本，避免把密码写进命令行参数。"""
        fd, name = tempfile.mkstemp(suffix=".cmd", prefix="asi-askpass-")
        os.close(fd)
        path = Path(name)
        # Windows 批处理；密码通过环境变量传入，不写进文件
        path.write_text(
            "@echo off\r\necho %ASI_SSH_PASSWORD%\r\n",
            encoding="ascii",
        )
        return path

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        if self._askpass:
            env["ASI_SSH_PASSWORD"] = self.password
            env["SSH_ASKPASS"] = str(self._askpass)
            env["SSH_ASKPASS_REQUIRE"] = "force"
            env["DISPLAY"] = "localhost:0"
        return env

    def _base_opts(self) -> list[str]:
        opts = [
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "ConnectTimeout=15",
            "-o", "ServerAliveInterval=30",
        ]
        if self.key:
            opts += ["-i", self.key, "-o", "IdentitiesOnly=yes"]
        return opts

    def ssh(self, script: str, *, timeout: int = 900) -> tuple[int, str, str]:
        """在远端执行 bash 脚本。

        注意：必须显式传入 UTF-8 字节且统一为 LF 换行 —— Windows 上
        ``text=True`` 会把 ``\\n`` 转成 ``\\r\\n``，导致 bash 报
        ``$'\\r': command not found``。
        """
        cmd = ["ssh", *self._base_opts(), self.target, "bash -s"]
        payload = script.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
        proc = subprocess.run(
            cmd,
            input=payload,
            capture_output=True,
            timeout=timeout,
            env=self._env(),
        )
        out = (proc.stdout or b"").decode("utf-8", errors="replace")
        err = (proc.stderr or b"").decode("utf-8", errors="replace")
        return proc.returncode, out, err

    def scp(self, local: Path, remote: str, *, timeout: int = 900) -> tuple[int, str, str]:
        cmd = ["scp", *self._base_opts(), str(local), f"{self.target}:{remote}"]
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, env=self._env(),
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""

    def cleanup(self) -> None:
        if self._askpass and self._askpass.exists():
            self._askpass.unlink(missing_ok=True)


def show(code: int, out: str, err: str, *, verbose: bool = True) -> bool:
    ok = code == 0
    if out.strip() and verbose:
        for line in out.strip().splitlines()[-40:]:
            print("    " + line)
    if not ok and err.strip():
        for line in err.strip().splitlines()[-20:]:
            print("    ! " + line)
    return ok


# --------------------------------------------------------------------------- #
# 打包
# --------------------------------------------------------------------------- #


def build_tarball() -> Path:
    hr("打包项目")
    fd, name = tempfile.mkstemp(suffix=".tar.gz", prefix="autosinin-")
    os.close(fd)
    out = Path(name)

    def include(path: Path) -> bool:
        rel = path.relative_to(ROOT)
        for part in rel.parts:
            if part in EXCLUDE_DIRS:
                return False
        if path.suffix in EXCLUDE_SUFFIX:
            return False
        if path.name.startswith("_") and path.suffix == ".py":
            return False
        return True

    count = 0
    with tarfile.open(out, "w:gz") as tar:
        for path in sorted(ROOT.rglob("*")):
            if not path.is_file() or not include(path):
                continue
            tar.add(path, arcname=str(path.relative_to(ROOT)))
            count += 1

    size = out.stat().st_size / 1024
    print(f"  已打包 {count} 个文件，{size:.0f} KB")
    print(f"  临时文件: {out}")
    return out


# --------------------------------------------------------------------------- #
# 远端脚本
# --------------------------------------------------------------------------- #

PRECHECK = r"""
set -u
echo "--- 系统 ---"
. /etc/os-release 2>/dev/null && echo "  $PRETTY_NAME" || uname -a
echo "  kernel: $(uname -r)"
echo "  arch:   $(uname -m)"
echo "--- 资源 ---"
free -m 2>/dev/null | awk 'NR==2{printf "  memory: total %sMB free %sMB\n",$2,$4}'
df -h / | awk 'NR==2{printf "  disk /: %s total, %s free\n",$2,$4}'
echo "--- 时间与时区 ---"
date '+  now: %Y-%m-%d %H:%M:%S %Z (%z)'
timedatectl 2>/dev/null | grep -i "time zone" | sed 's/^/  /' || true
echo "--- 是否已安装过 ---"
if [ -d {REMOTE_DIR} ]; then echo "  {REMOTE_DIR} 已存在"; else echo "  {REMOTE_DIR} 不存在（全新）"; fi
if systemctl list-unit-files | grep -q '^autosinin'; then echo "  autosinin.service 已注册"; else echo "  autosinin.service 未注册"; fi
echo "--- 端口占用 ---"
ss -lntp 2>/dev/null | grep -E ':(80|443|8000)\b' | sed 's/^/  /' || echo "  80/443/8000 均空闲"
echo "--- 学校接口连通性（关键）---"
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 https://simp.csuft.edu.cn/ 2>/dev/null || echo 000)
if [ "$code" = "200" ]; then
  echo "  ✓ simp.csuft.edu.cn 可达 (HTTP $code)"
else
  echo "  ✗ simp.csuft.edu.cn 不可达 (code=$code) —— 自动签到将无法工作！"
fi
echo "--- DNS ---"
getent hosts simp.csuft.edu.cn | head -1 | sed 's/^/  /' || echo "  解析失败"
echo "PRECHECK_DONE"
"""

INSTALL = r"""
set -eu
export DEBIAN_FRONTEND=noninteractive

echo "### 1. 安装系统依赖"
if command -v apt-get >/dev/null 2>&1; then
  apt-get update -qq
  apt-get install -y -qq python3 python3-venv python3-pip tzdata curl ca-certificates >/dev/null
elif command -v dnf >/dev/null 2>&1; then
  dnf install -y -q python3 python3-pip tzdata curl ca-certificates >/dev/null
else
  echo "不支持的包管理器"; exit 1
fi
echo "  python3: $(python3 --version 2>&1)"

echo "### 2. 设置时区为 Asia/Shanghai（签到窗口按此时区计算）"
timedatectl set-timezone Asia/Shanghai 2>/dev/null || ln -sf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime
echo "  现在: $(date '+%Y-%m-%d %H:%M:%S %Z')"

echo "### 3. 创建专用用户"
if id autosinin >/dev/null 2>&1; then
  echo "  用户 autosinin 已存在"
else
  useradd -r -s /usr/sbin/nologin -d {REMOTE_DIR} --user-group autosinin
  echo "  已创建 autosinin 用户与同名用户组"
fi

echo "### 4. 解包项目"
mkdir -p {REMOTE_DIR}
tar -xzf /tmp/autosinin.tar.gz -C {REMOTE_DIR}
rm -f /tmp/autosinin.tar.gz
mkdir -p {REMOTE_DIR}/data
echo "  文件数: $(find {REMOTE_DIR} -type f | wc -l)"

echo "### 5. 创建虚拟环境并安装依赖"
cd {REMOTE_DIR}
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt
echo "  已安装: $(.venv/bin/pip list 2>/dev/null | wc -l) 个包"

echo "### 6. 生成 .env（写入随机会话密钥）"
if [ ! -f {REMOTE_DIR}/.env ]; then
  SECRET=$(head -c 48 /dev/urandom | base64 | tr -d '\n/+=' | head -c 48)
  cat > {REMOTE_DIR}/.env <<EOF
SITE_NAME=中南林学工自动签到
TIMEZONE=Asia/Shanghai
SIGN_WINDOW_START=21:00
SIGN_WINDOW_END=22:30
SCHEDULER_DISABLED=0
SECRET_KEY=$SECRET
EOF
  echo "  已生成 .env"
else
  echo "  .env 已存在，保持不变"
fi

echo "### 7. 配置 systemd 服务"
cp {REMOTE_DIR}/deploy/autosinin.service /etc/systemd/system/autosinin.service
systemctl daemon-reload
systemctl enable autosinin >/dev/null 2>&1

echo "### 8. 修正文件属主"
chown -R autosinin:autosinin {REMOTE_DIR}
chown autosinin:autosinin {REMOTE_DIR}/.env
chmod 600 {REMOTE_DIR}/.env
chmod 750 {REMOTE_DIR}/data
echo "  .env 属主: $(stat -c '%U:%G %a' {REMOTE_DIR}/.env)"

echo "### 9. 启动服务"
systemctl restart autosinin
sleep 6
systemctl is-active autosinin && echo "  服务状态: active" || echo "  服务状态: 未启动"
echo "INSTALL_DONE"
"""

VERIFY = r"""
echo "### 服务状态"
systemctl status autosinin --no-pager -l 2>/dev/null | head -12 | sed 's/^/  /'
echo
echo "### 最近日志"
journalctl -u autosinin -n 20 --no-pager 2>/dev/null | sed 's/^/  /'
echo
echo "### 本地自检"
curl -s --max-time 10 http://127.0.0.1:8000/healthz && echo || echo "  healthz 失败"
echo
echo "### 上线前自检工具"
cd {REMOTE_DIR}
sudo -u autosinin .venv/bin/python tools/preflight.py 2>&1 | tail -25 | sed 's/^/  /' || true
echo "VERIFY_DONE"
"""


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #


def main() -> int:
    global REMOTE_DIR

    parser = argparse.ArgumentParser(description="一键部署到 Linux 服务器")
    parser.add_argument("--host", required=True)
    parser.add_argument("--user", default="root")
    parser.add_argument("--key", default="", help="私钥路径；不给则用密码")
    parser.add_argument(
        "--remote-dir",
        default=DEFAULT_REMOTE_DIR,
        help=f"远端安装目录（默认 {DEFAULT_REMOTE_DIR}）",
    )
    parser.add_argument(
        "--password-env",
        default="ASI_SSH_PASSWORD",
        help="从该环境变量读取密码（默认 ASI_SSH_PASSWORD），便于非交互式运行",
    )
    parser.add_argument("--skip-precheck", action="store_true")
    args = parser.parse_args()

    REMOTE_DIR = args.remote_dir.rstrip("/") or DEFAULT_REMOTE_DIR

    password = ""
    if not args.key:
        # 优先从环境变量取（非交互），否则交互式询问（不落盘）
        password = os.environ.get(args.password_env, "")
        if not password:
            password = getpass.getpass(f"{args.user}@{args.host} 的密码（不会落盘）: ")
        if not password:
            print("未输入密码，退出。")
            return 1
        print(f"已获取 {args.user}@{args.host} 的密码（来源："
              f"{'环境变量 ' + args.password_env if os.environ.get(args.password_env) else '交互输入'}）")

    print(f"安装目录：{REMOTE_DIR}")
    remote = Remote(args.host, args.user, args.key, password)
    tarball: Path | None = None

    try:
        # ---------------------------------------------------------- 预检 --
        if not args.skip_precheck:
            hr("1. 远端环境预检")
            code, out, err = remote.ssh(
                PRECHECK.format(REMOTE_DIR=REMOTE_DIR), timeout=180
            )
            show(code, out, err)
            if "PRECHECK_DONE" not in out:
                print("\n✗ 预检未完成，可能是 SSH 登录失败。")
                return 1
            if "✓ simp.csuft.edu.cn 可达" not in out:
                print("\n⚠️  警告：服务器访问不了学校接口！")
                print("   自动签到会全部失败。请确认服务器地域在中国大陆。")
                if input("   仍要继续部署吗？(yes/no) ").strip().lower() not in ("yes", "y"):
                    return 1

        # ---------------------------------------------------------- 打包 --
        tarball = build_tarball()

        hr("2. 上传到服务器")
        code, out, err = remote.scp(tarball, "/tmp/autosinin.tar.gz")
        show(code, out, err, verbose=False)
        if code != 0:
            print("✗ 上传失败")
            return 1
        print("  ✓ 已上传 /tmp/autosinin.tar.gz")

        # ---------------------------------------------------------- 安装 --
        hr("3. 远端安装（可能需要几分钟）")
        code, out, err = remote.ssh(
            INSTALL.format(REMOTE_DIR=REMOTE_DIR), timeout=1800
        )
        show(code, out, err)
        if "INSTALL_DONE" not in out:
            print("\n✗ 安装未完成，请查看上面的输出。")
            return 1

        # ---------------------------------------------------------- 验证 --
        hr("4. 部署后验证")
        code, out, err = remote.ssh(VERIFY.format(REMOTE_DIR=REMOTE_DIR), timeout=600)
        show(code, out, err)

        hr("部署完成")
        print(f"  用户端   http://{args.host}:{SERVICE_PORT}/")
        print(f"  管理端   http://{args.host}:{SERVICE_PORT}/admin")
        print()
        print("  下一步：")
        print(f"   1) 打开 http://{args.host}:{SERVICE_PORT}/admin 设置管理员账号")
        print("   2) （可选）进入「校历管理」添加学期与寒暑假 —— 只影响日历着色")
        print("   3) 本机抓包录入同学账号后导入服务器：")
        print(f"      python tools/import_token.py --host {args.host} --user {args.user}")
        print()
        print("  常用运维命令：")
        print("    systemctl status autosinin        # 查看状态")
        print("    journalctl -u autosinin -f        # 实时日志")
        print("    systemctl restart autosinin       # 重启")
        return 0

    finally:
        remote.cleanup()
        if tarball and tarball.exists():
            tarball.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
