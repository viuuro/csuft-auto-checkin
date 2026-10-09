#!/usr/bin/env python3
"""把 kicker 前缀的 `✱`(U+2731) 换成 `*`(U+002A)。

原因
----
`✱` HEAVY ASTERISK 不在任何随片字体（EB Garamond / Inter / JetBrains Mono /
Noto Serif SC / Noto Sans SC）的字形表里 —— 实测六种字体栈给出的宽度完全
相同（88.83px @100px），证明它们都缺该字形、统一回退到系统字体。
本机渲染正常，但换机器可能变成豆腐块。

`*` ASTERISK (U+002A) 是 ASCII，所有字体必备，零回退风险。
U+2731 是居中五角星、U+002A 偏上，故补一点垂直偏移让视觉位置接近。

用法::

    python tools/fix_asterisk_glyph.py --dry-run
    python tools/fix_asterisk_glyph.py
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRAMES = ROOT / "video-intro" / "compositions" / "frames"

OLD = "\u2731"   # ✱
NEW = "*"        # *


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    print("=" * 74)
    print(f"替换 kicker 前缀 {OLD} → {NEW}"
          f"{'  [dry-run]' if args.dry_run else ''}")
    print("=" * 74)

    total = 0
    for f in sorted(FRAMES.glob("*.html")):
        text = original = f.read_text(encoding="utf-8")
        n = text.count(OLD)
        if not n:
            print(f"  · {f.name:32s} 无")
            continue
        text = text.replace(OLD, NEW)
        if not args.dry_run:
            f.write_text(text, encoding="utf-8")
        total += n
        print(f"  ✓ {f.name:32s} {n} 处")

    print()
    print(f"  合计 {total} 处")
    if args.dry_run:
        print("  （dry-run，未写入）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
