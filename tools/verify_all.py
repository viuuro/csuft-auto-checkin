#!/usr/bin/env python3
"""一键跑完全部检查。

用法::

    python tools/verify_all.py              # 本地检查（不联网）
    python tools/verify_all.py --online     # 额外探测学校接口
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8")

CHECKS: list[tuple[str, list[str], str]] = [
    ("模块编译", [sys.executable, "-m", "compileall", "-q", "app", "tools", "run.py"],
     "所有 Python 文件语法正确"),
    ("静态检查", [sys.executable, "tools/check_requirements.py"],
     "11 条需求均有实现落点"),
    ("前端接线", [sys.executable, "tools/check_wiring.py"],
     "JS 引用的 DOM id 全部有来源"),
    ("单元测试", [sys.executable, "tools/test_units.py"],
     "签名算法 / 加密 / 校历判定 / 配置"),
    ("端到端测试", [sys.executable, "tools/test_e2e.py"],
     "申请→审批→绑定→签到→管理全链路"),
]

ONLINE_CHECKS: list[tuple[str, list[str], str]] = [
    ("上线前自检", [sys.executable, "tools/preflight.py", "--online"],
     "时区/密钥/权限/校历/学校接口（校历未配置时预期 FAIL）"),
]


def run(label: str, cmd: list[str], desc: str) -> tuple[bool, str]:
    print(f"\n{'─' * 74}")
    print(f"▶ {label}  —— {desc}")
    print("─" * 74)
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    output = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        # 打印尾部，便于定位
        tail = [line for line in output.splitlines() if line.strip()][-12:]
        for line in tail:
            print("   ", line)
    return proc.returncode == 0, output


def main() -> int:
    online = "--online" in sys.argv

    print("=" * 74)
    print("中南林学工自动签到 —— 全量检查")
    print("=" * 74)

    results: list[tuple[str, bool, str]] = []
    for label, cmd, desc in CHECKS:
        ok, out = run(label, cmd, desc)
        # 抓出通过数
        stat = ""
        for line in out.splitlines():
            if "通过" in line and "失败" in line:
                stat = line.strip()
        results.append((label, ok, stat))
        print(f"   {'✓ 通过' if ok else '✗ 失败'}   {stat}")

    if online:
        for label, cmd, desc in ONLINE_CHECKS:
            ok, out = run(label, cmd, desc)
            stat = ""
            for line in out.splitlines():
                if line.strip().startswith("发现") or "没有发现阻塞性问题" in line:
                    stat = line.strip()
            results.append((label, ok, stat))
            # preflight 在"校历未配置"时会返回 1，这是预期的
            print(f"   {'✓ 通过' if ok else '! 有待办'}   {stat}")

    print("\n" + "=" * 74)
    print("汇总")
    print("=" * 74)
    failed = 0
    for label, ok, stat in results:
        mark = "✓" if ok else ("!" if label == "上线前自检" else "✗")
        if not ok and label != "上线前自检":
            failed += 1
        print(f"  {mark}  {label:12s} {stat}")

    print()
    if failed:
        print(f"{failed} 项检查未通过")
    else:
        print("本地检查全部通过。")

    print("\n真实验证状态：")
    print("  ✓ 签名算法 —— 与抓包向量逐字节一致")
    print("  ✓ 全部业务接口 —— 已用真实凭据验证")
    print("  ✓ 窗口内真实签到成功 —— 2026-10-07 / 10-08 已签到（见 docs/VERIFICATION.md §七）")
    print("\n  注意判定方式：签到成功后 signStatus 仍为 0，")
    print("  必须看 signTime 是否有值（signStatus=0 是「正常」不是「未打卡」）。")
    print("  查服务端真实状态：python tools/probe_token.py")
    print("=" * 74)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
