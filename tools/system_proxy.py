"""
设置/恢复 Windows 系统代理，用于配合 mitmproxy 抓取微信小程序请求。

用法：
    python tools/system_proxy.py on      # 开启代理指向 127.0.0.1:8888
    python tools/system_proxy.py off     # 关闭代理
    python tools/system_proxy.py status  # 查看当前状态
"""

from __future__ import annotations

import sys
import winreg

PROXY = "127.0.0.1:8888"
KEY = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"

sys.stdout.reconfigure(encoding="utf-8")


def read_state() -> dict[str, object]:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY) as key:
        state: dict[str, object] = {}
        for name in ("ProxyEnable", "ProxyServer", "ProxyOverride"):
            try:
                state[name] = winreg.QueryValueEx(key, name)[0]
            except FileNotFoundError:
                state[name] = None
        return state


def write(name: str, value: object, kind: int) -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, name, 0, kind, value)


def notify() -> None:
    """通知系统代理设置已变更，让应用立即感知。"""
    import ctypes

    INTERNET_OPTION_SETTINGS_CHANGED = 39
    INTERNET_OPTION_REFRESH = 37
    wininet = ctypes.windll.wininet
    wininet.InternetSetOptionW(0, INTERNET_OPTION_SETTINGS_CHANGED, 0, 0)
    wininet.InternetSetOptionW(0, INTERNET_OPTION_REFRESH, 0, 0)


def show() -> None:
    st = read_state()
    enabled = st.get("ProxyEnable")
    print("当前系统代理状态：")
    print(f"  ProxyEnable   = {enabled}  ({'已开启' if enabled else '已关闭'})")
    print(f"  ProxyServer   = {st.get('ProxyServer')}")
    print(f"  ProxyOverride = {st.get('ProxyOverride')}")


def main() -> int:
    action = sys.argv[1] if len(sys.argv) > 1 else "status"

    if action == "status":
        show()
        return 0

    if action == "on":
        before = read_state()
        # 备份原值，便于恢复
        print("原始设置（请记下，或用 off 恢复为直连）：")
        for k, v in before.items():
            print(f"  {k} = {v}")
        write("ProxyEnable", 1, winreg.REG_DWORD)
        write("ProxyServer", PROXY, winreg.REG_SZ)
        # 让本机与内网直连，避免影响其它服务
        write("ProxyOverride", "localhost;127.*;10.*;172.16.*;172.17.*;172.18.*;"
                               "172.19.*;172.2*;172.30.*;172.31.*;192.168.*;<local>",
              winreg.REG_SZ)
        notify()
        print(f"\n✓ 已开启系统代理 → {PROXY}")
        print("  现在请：① 完全退出微信（右下角托盘 → 退出） ② 重新打开微信")
        print("  然后在小程序里进「打卡」页点一次打卡。")
        return 0

    if action == "off":
        write("ProxyEnable", 0, winreg.REG_DWORD)
        notify()
        print("✓ 已关闭系统代理（恢复直连）")
        return 0

    print(f"未知操作：{action}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
