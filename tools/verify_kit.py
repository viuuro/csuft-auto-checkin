#!/usr/bin/env python3
"""验证分发包：结构、无 CA 私钥、无作者标识、占位符已填值。

敏感标识清单从 ``tools/.private-terms``（已被 .gitignore 忽略）读取，
不把真实学号 / IP / 昵称写进本文件。
"""

from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8")

PRIVATE_TERMS_FILE = ROOT / "tools" / ".private-terms"

# 通用项：用**模式**而不是具体字符串，这样本文件自身不含任何真实标识。
# 只匹配「确实指向某个人的绝对路径」——说明文档里的
# 「C:\\Users\\你的用户名\\Desktop」这类示例不会被误判。
BUILTIN_PATTERNS = (
    # Windows 用户目录下的具体路径（用户名非占位词）
    (re.compile(r"[A-Za-z]:\\+Users\\+(?!你的用户名|username|<)[^\\\s\"']{2,}"),
     "Windows 用户目录路径"),
    # Linux / macOS 家目录下的具体路径
    (re.compile(r"/(?:home|Users)/(?!你的用户名|username|<)[a-zA-Z0-9_.\-]{2,}/"),
     "家目录路径"),
)


def load_terms() -> list[str]:
    """本地真实标识清单。"""
    if not PRIVATE_TERMS_FILE.exists():
        return []
    terms: list[str] = []
    for raw in PRIVATE_TERMS_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            terms.append(line)
    return terms


TERMS = load_terms()

zp = ROOT / "dist" / "checkin-auth-tool.zip"
print("=" * 72)
print("分发包最终验证")
print("=" * 72)

leaks: list[str] = []
with zipfile.ZipFile(zp) as z:
    names = z.namelist()
    print(f"\n大小     : {zp.stat().st_size / 1024 / 1024:.1f} MB")
    print(f"文件数   : {len(names)}")
    print(f"顶层目录 : {sorted({n.split('/')[0] for n in names})}")
    print(f"内容     : "
          f"{sorted({n.split('/')[1] for n in names if n.count('/') >= 1 and n.split('/')[1]})}")

    # 1. 不含 CA 私钥
    ca = [n for n in names if ".mitmproxy" in n]
    print(f"\n① 内置 CA 证书/私钥 : {ca if ca else '无 ✓'}")

    # 2. 不含作者/本机标识
    print(f"\n② 隐私检查（{len(BUILTIN_PATTERNS)} 个模式 + {len(TERMS)} 个本地标识）:")
    for info in z.infolist():
        if info.is_dir() or info.file_size > 3_000_000:
            continue
        if "site-packages" in info.filename or "/python/" in info.filename:
            continue
        try:
            data = z.read(info)
        except (KeyError, OSError):
            continue
        text = None
        for enc in ("utf-8", "gbk"):
            try:
                text = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            continue
        for pattern, label in BUILTIN_PATTERNS:
            m = pattern.search(text)
            if m:
                leaks.append(f"{info.filename} 命中「{label}」: {m.group(0)[:40]}")
        for term in TERMS:
            if term in text:
                leaks.append(f"{info.filename} 含本地标识 [{term[:14]}]")
    if leaks:
        for item in leaks:
            print(f"   ⚠ {item}")
    else:
        print("   ✓ 未发现本机路径 / 作者标识 / 真实学号")

    # 3. 占位符已填值
    print("\n③ 占位符已填值:")
    for target in ("README.txt", "kit_finish.py", "1-start.bat"):
        hit = next((n for n in names if n.endswith(target)), None)
        if not hit:
            continue
        raw = z.read(hit)
        text = ""
        for enc in ("utf-8", "gbk"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        left = [p for p in ("{ADMIN_NAME}", "{RESULT_FILE}") if p in text]
        print(f"   {target:16s} {'⚠ 未替换 ' + str(left) if left else '✓'}")

ok = not leaks and not ca
print()
print("=" * 72)
print("结论：结构与隐私检查" + ("通过" if ok else "存在问题"))
print("=" * 72)
raise SystemExit(0 if ok else 1)

