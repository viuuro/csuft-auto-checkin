#!/usr/bin/env python3
"""把打包脚本里的 bat 模板同步到 dist 的实际文件。

为什么需要
----------
`1-start.bat` 的 mitmdump 存在性检查路径写错了（检查根目录，实际在
`python\\Scripts\\` 下），导致用户**即使完整解压也必然报错**，且提示文案
把原因单一归为「没解压完整」，是误导。修正后模板更新了，但学生拿到的是
dist 里的实物，必须同步过去。

做法
----
从 build_classmate_kit.py 抽出 BAT_START / BAT_STOP / BAT_DOCTOR，
替换占位符，按 **UTF-8 + BOM + CRLF** 写入 dist（与既有约定一致）。

安全措施
--------
* 每个文件写入前备份为 `<名>.bak`
* 写后复验：能按 UTF-8 解出且含关键行
* 只写这三个 .bat，不碰其它文件
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "build_classmate_kit.py"
DIST = ROOT / "dist" / "checkin-auth-tool" / "checkin-auth-tool"
UTF8_BOM = b"\xef\xbb\xbf"

# 加载打包脚本取模板
spec = importlib.util.spec_from_file_location("bck", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

PAIRS = [
    ("1-start.bat", mod.BAT_START),
    ("2-finish.bat", mod.BAT_STOP),
    ("check-env.bat", mod.BAT_DOCTOR),
]

print("=" * 76)
print("同步 bat 模板 → dist")
print("=" * 76)

fail = 0
for name, tpl in PAIRS:
    dest = DIST / name
    if not dest.exists():
        print(f"\n  ✗ {name}: dist 里不存在，跳过")
        fail += 1
        continue

    text = tpl.replace("{ADMIN_NAME}", mod.ADMIN_NAME).replace(
        "{RESULT_FILE}", mod.RESULT_FILE
    )
    text = text.replace("\r\n", "\n").replace("\n", "\r\n")
    payload = UTF8_BOM + text.encode("utf-8")

    old = dest.read_bytes()
    backup = dest.with_name(dest.name + ".bak")
    if not backup.exists():
        shutil.copy2(dest, backup)
        bk = f"已备份 {backup.name}"
    else:
        bk = "备份已存在（保留）"

    dest.write_bytes(payload)

    # 写后复验
    chk = dest.read_bytes()
    ok = chk.startswith(UTF8_BOM)
    body = chk[3:].decode("utf-8") if ok else ""
    must = ["@echo off", "chcp 65001", "%PYCMD%"]
    ok = ok and all(m in body for m in must)

    print(f"\n  {'✓' if ok else '✗'} {name}")
    print(f"      {len(old):,} B → {len(payload):,} B · {bk}")
    print(f"      含 key 行：{', '.join(must)}")
    if not ok:
        fail += 1

print()
print("=" * 76)
print(f"{'全部同步成功 ✓' if not fail else f'{fail} 个文件有问题 ✗'}")
print("=" * 76)
