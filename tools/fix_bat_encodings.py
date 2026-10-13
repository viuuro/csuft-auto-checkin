#!/usr/bin/env python3
"""把含 `chcp 65001` 的 .bat 从 GBK 无 BOM 转为 UTF-8 带 BOM。

问题
----
这些 .bat 第 2 行执行 `chcp 65001`（控制台切 UTF-8），但文件本身是 GBK 字节
—— cmd.exe 从第 3 行起按 UTF-8 解析 GBK 中文，于是中文全部乱码。
（实测：GBK+chcp65001 → 乱码；UTF-8/BOM+chcp65001 → 正确）

修法
----
转成 **UTF-8 带 BOM**：BOM 让编码对任何代码页都无歧义。

安全措施
--------
* 只处理目标目录下的 .bat/.cmd，不碰其它文件
* 每个文件转换前备份为 `<名>.bak-gbk`
* 内容零语义改动：解码 → 重编码，行尾统一 CRLF
* 往返校验 + 关键行存在性检查 + 写后复验，任一失败即中止该文件
* 幂等：已是 UTF-8 BOM 的跳过

用法::

    python tools/fix_bat_encodings.py --dry-run
    python tools/fix_bat_encodings.py
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

TARGET_DIR = Path(
    r"D:\testLLM\ds\AutoSinIn\dist\checkin-auth-tool\checkin-auth-tool"
)
BACKUP_SUFFIX = ".bak-gbk"
UTF8_BOM = b"\xef\xbb\xbf"


def convert(p: Path, dry: bool) -> tuple[str, str]:
    """返回 (状态标记, 说明)。"""
    raw = p.read_bytes()

    if raw.startswith(UTF8_BOM):
        return "·", "已是 UTF-8 BOM，跳过"

    try:
        text = raw.decode("gbk")
    except UnicodeDecodeError as exc:
        return "✗", f"按 GBK 解码失败 @byte {exc.start} —— 请人工确认编码"

    # 行尾统一 CRLF
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")

    # 自检：必须保留的关键行
    must = ["@echo off", "chcp 65001", "cd /d \"%~dp0\""]
    missing = [k for k in must if k not in normalized]
    if missing:
        return "✗", f"缺少关键行 {missing} —— 中止"

    out = UTF8_BOM + normalized.encode("utf-8")

    # 往返校验
    if out[len(UTF8_BOM):].decode("utf-8") != normalized:
        return "✗", "往返校验失败 —— 中止"

    if dry:
        return "✓", f"{len(raw):,} B → {len(out):,} B（dry-run 未写入）"

    backup = p.with_name(p.name + BACKUP_SUFFIX)
    if not backup.exists():
        shutil.copy2(p, backup)
        note = f"已备份 {backup.name}"
    else:
        note = f"备份已存在（保留）"

    p.write_bytes(out)

    # 写后复验
    check = p.read_bytes()
    ok = check.startswith(UTF8_BOM) and check[len(UTF8_BOM):].decode("utf-8") == normalized
    return ("✓" if ok else "✗"), f"{len(raw):,} B → {len(out):,} B · {note}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    print("=" * 76)
    print(f"批量转换 .bat 编码  GBK(无 BOM) → UTF-8(BOM)"
          f"{'  [dry-run]' if args.dry_run else ''}")
    print(f"目录：{TARGET_DIR.name}")
    print("=" * 76)

    files = sorted(list(TARGET_DIR.glob("*.bat")) + list(TARGET_DIR.glob("*.cmd")))
    if not files:
        print("  （没有 .bat/.cmd）")
        return 1

    bad = 0
    for f in files:
        mark, note = convert(f, args.dry_run)
        if mark == "✗":
            bad += 1
        print(f"  {mark} {f.name:24s} {note}")

    print()
    print("=" * 76)
    print(f"处理 {len(files)} 个文件，失败 {bad} 个" if bad else
          f"处理 {len(files)} 个文件，全部成功 ✓")
    print("=" * 76)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
