"""
把 mitmproxy 的 CA 证书装进 Windows 受信任根存储 / 卸载。

微信小程序容器走的是 Windows 证书存储，必须把 mitmproxy 的根证书装进去
才能解密它的 HTTPS（否则报"网络错误"，因为握手被拒）。

用法：
    python tools/install_ca.py install     # 安装到当前用户受信任根
    python tools/install_ca.py uninstall   # 从当前用户受信任根移除
    python tools/install_ca.py status      # 查看是否已安装
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

CERT = Path.home() / ".mitmproxy" / "mitmproxy-ca-cert.cer"
CN_HINT = "mitmproxy"

sys.stdout.reconfigure(encoding="utf-8")


def run(cmd: list[str]) -> tuple[int, str]:
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _decode(raw: bytes) -> str:
    """certutil 在中文 Windows 上输出 GBK，需要正确解码。"""
    for enc in ("gbk", "utf-8", "mbcs"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def run_bytes(cmd: list[str]) -> tuple[int, str]:
    proc = subprocess.run(cmd, capture_output=True)
    return proc.returncode, _decode(proc.stdout or b"") + _decode(proc.stderr or b"")


def is_installed() -> bool:
    """在用户受信任根里找 mitmproxy 证书。"""
    code, out = run_bytes(["certutil", "-user", "-store", "Root"])
    if code != 0:
        return False
    return CN_HINT in out.lower() or "mitmproxy" in out


def install() -> int:
    if not CERT.exists():
        print(f"找不到 CA 证书：{CERT}")
        print("请先启动一次 mitmdump 并访问任意 HTTPS 站点以生成证书。")
        return 1

    if is_installed():
        print("✓ 证书已在受信任根中，无需重复安装")
        return 0

    # -user 表示只装到当前用户，通常不需要管理员权限
    code, out = run_bytes(["certutil", "-user", "-addstore", "-f", "Root", str(CERT)])
    print(out.strip()[-500:] if out.strip() else "")
    if code == 0 and is_installed():
        print("\n✓ 已安装 mitmproxy CA 到【当前用户 → 受信任的根证书颁发机构】")
        print("  现在可以重新开启代理并重启微信了。")
        print("  抓包结束后建议运行 uninstall 移除，避免长期信任该证书。")
        return 0

    print("\n用户级安装失败，尝试系统级（需要管理员权限，可能弹 UAC）…")
    code, out = run_bytes(["certutil", "-addstore", "-f", "Root", str(CERT)])
    print(out.strip()[-500:] if out.strip() else "")
    if code == 0:
        print("\n✓ 已安装到【本地计算机 → 受信任的根证书颁发机构】")
        return 0

    print("\n✗ 安装失败。请改用图形界面手动导入：")
    print(f"  1. 双击 {CERT}")
    print("  2. 安装证书 → 本地计算机/当前用户 → 将所有的证书都放入下列存储")
    print("  3. 浏览 → 受信任的根证书颁发机构 → 确定 → 完成")
    return 1


def uninstall() -> int:
    if not is_installed():
        print("证书未安装，无需移除")
        return 0

    # certutil -store 的文本解析在不同语言/版本下不稳定，
    # 改用 PowerShell 的证书驱动器按 Subject 精确匹配并删除。
    ps = (
        "Get-ChildItem Cert:\\CurrentUser\\Root | "
        "Where-Object { $_.Subject -like '*mitmproxy*' } | "
        "ForEach-Object { $_.Thumbprint }"
    )
    code, out = run_bytes(["powershell", "-NoProfile", "-Command", ps])
    thumbs = [t.strip() for t in out.splitlines() if t.strip() and len(t.strip()) == 40]

    if not thumbs:
        print("未能在当前用户受信任根中找到 mitmproxy 证书（可能已移除）")
        return 0

    removed = 0
    for thumb in thumbs:
        del_ps = f"Remove-Item -Path 'Cert:\\CurrentUser\\Root\\{thumb}' -Force"
        c, o = run_bytes(["powershell", "-NoProfile", "-Command", del_ps])
        if c == 0:
            removed += 1
            print(f"  ✓ 已移除证书 {thumb}")
        else:
            print(f"  ✗ 移除失败 {thumb}: {o.strip()[:160]}")

    if removed and not is_installed():
        print("\n✓ mitmproxy CA 已从受信任根移除，系统恢复原状")
        return 0

    print("\n自动移除未完全成功。请手动删除：")
    print("  Win+R → certmgr.msc → 受信任的根证书颁发机构 → 证书 → 找到 mitmproxy → 删除")
    return 1


def main() -> int:
    action = sys.argv[1] if len(sys.argv) > 1 else "status"

    if action == "status":
        state = "已安装" if is_installed() else "未安装"
        print(f"mitmproxy CA 状态：{state}")
        print(f"证书文件：{CERT}")
        if not CERT.exists():
            print("（文件不存在，需先运行一次 mitmdump）")
        return 0

    if action == "install":
        return install()
    if action == "uninstall":
        return uninstall()

    print(f"未知操作：{action}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
