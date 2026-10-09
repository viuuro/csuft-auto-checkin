#!/usr/bin/env python3
"""补齐服务器上缺失/过时的文件。

用 ``tools/check_server_sync.py`` 找出差异后，用它同步。
默认只同步「代码」类文件，保留 ``data/`` 与 ``.env``。
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

_spec = importlib.util.spec_from_file_location(
    "deploy_remote", str(ROOT / "tools" / "deploy_remote.py")
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
Remote = _mod.Remote

HOST = os.environ.get("ASI_HOST", "")
USER = os.environ.get("ASI_SSH_USER", "root")
REMOTE = os.environ.get("ASI_REMOTE_DIR", "/opt/autosinin")
PASSWORD = os.environ.get("ASI_SSH_PASSWORD", "")

if not HOST or not PASSWORD:
    print("请先设置 ASI_HOST 与 ASI_SSH_PASSWORD")
    raise SystemExit(1)

# 需要补齐的文件（用 check_server_sync.py 核对出来的差异）
FILES = [
    "app/__init__.py",
    "app/static/admin.html",
]

remote = Remote(HOST, USER, "", PASSWORD)
try:
    print("=" * 74)
    print("同步差异文件到服务器")
    print("=" * 74)
    for rel in FILES:
        code, _, err = remote.scp(ROOT / rel, f"{REMOTE}/{rel}")
        print(f"  {'✓' if code == 0 else '✗'} {rel}" + (f"  {err[:120]}" if code else ""))

    print()
    print("重启服务…")
    code, out, err = remote.ssh(
        f"cd {REMOTE} && chown -R autosinin:autosinin app tools && "
        f"systemctl restart autosinin && sleep 6 && systemctl is-active autosinin",
        timeout=300,
    )
    print(f"  服务状态: {(out or '').strip()}")
    if err.strip():
        print(f"  ! {err.strip()[:200]}")

finally:
    remote.cleanup()

print()
print("=" * 74)
print("完成 —— 请再跑一次 tools/check_server_sync.py 确认无差异")
print("=" * 74)
