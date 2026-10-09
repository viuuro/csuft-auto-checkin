#!/usr/bin/env python3
"""核对服务器上的代码是否与本地一致（逐文件哈希比对）。"""

from __future__ import annotations

import hashlib
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

# 需要核对的代码文件（会部署到服务器、影响运行行为的）
WATCH = [
    "app/__init__.py", "app/calendar_rules.py", "app/config.py",
    "app/constants.py", "app/db.py", "app/flysource.py", "app/main.py",
    "app/routes_admin.py", "app/routes_user.py", "app/scheduler.py",
    "app/security.py", "app/services.py",
    "app/static/admin.html", "app/static/admin.js", "app/static/app.js",
    "app/static/index.html", "app/static/style.css",
    "run.py", "requirements.txt",
    "deploy/autosinin.service", "deploy/env.example",
    "tools/verify_deploy.py", "tools/preflight.py", "tools/verify_final.py",
]

remote = Remote(HOST, USER, "", PASSWORD)
try:
    print("=" * 74)
    print("服务器 vs 本地 代码一致性核对")
    print("=" * 74)

    # 一次性取远端所有文件的哈希（快）
    paths = " ".join(f"{REMOTE}/{p}" for p in WATCH)
    code, out, err = remote.ssh(
        f"cd {REMOTE} && sha256sum {paths} 2>/dev/null", timeout=300
    )
    remote_hashes: dict[str, str] = {}
    for line in (out or "").splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2:
            h, path = parts
            rel = path.strip().replace(f"{REMOTE}/", "")
            remote_hashes[rel] = h[:12]

    same, diff, missing = [], [], []
    for rel in WATCH:
        local = ROOT / rel
        if not local.exists():
            continue
        lh = hashlib.sha256(local.read_bytes()).hexdigest()[:12]
        rh = remote_hashes.get(rel)
        if rh is None:
            missing.append(rel)
        elif lh == rh:
            same.append(rel)
        else:
            diff.append(rel)

    print(f"\n  一致   : {len(same)} 个")
    print(f"  不一致 : {len(diff)} 个")
    for rel in diff:
        print(f"     ✗ {rel}   本地={hashlib.sha256((ROOT/rel).read_bytes()).hexdigest()[:12]}"
              f"  远端={remote_hashes.get(rel, '(缺失)')}")
    print(f"  远端缺失: {len(missing)} 个")
    for rel in missing:
        print(f"     ✗ {rel}  （未部署）")

    print()
    print("=" * 74)
    if diff or missing:
        print(f"结论：有 {len(diff) + len(missing)} 个文件需要同步到服务器")
    else:
        print("结论：✓ 服务器代码与本地完全一致，无需更新")
    print("=" * 74)

finally:
    remote.cleanup()
