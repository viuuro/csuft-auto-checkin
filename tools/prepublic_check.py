#!/usr/bin/env python3
"""公开前的最终安全检查。

调用 GitHub 自身的 secret scanning + 本地规则，逐项确认没有敏感信息。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8")

GH = r"C:\Program Files\GitHub CLI\gh.exe"
REPO = "viuuro/csuft-auto-checkin"


def gh(args: list[str], timeout: int = 180) -> tuple[int, str]:
    p = subprocess.run([GH, *args], capture_output=True, timeout=timeout)
    return p.returncode, ((p.stdout or b"") + (p.stderr or b"")).decode(
        "utf-8", errors="replace"
    ).strip()


def git(args: list[str]) -> str:
    p = subprocess.run(["git", *args], capture_output=True, timeout=180,
                       cwd=str(ROOT))
    return ((p.stdout or b"") + (p.stderr or b"")).decode("utf-8", "replace").strip()


print("=" * 74)
print("公开前最终安全检查")
print("=" * 74)

# ---------------------------------------------------------- 1. 密钥扫描 --
print("\n① GitHub 密钥扫描告警")
code, out = gh(["api", f"repos/{REPO}/secret-scanning/alerts"])
if code == 0:
    try:
        alerts = json.loads(out)
        if isinstance(alerts, list) and alerts:
            for a in alerts:
                print(f"   ⚠ {a.get('secret_type')}  状态={a.get('state')}")
        else:
            print("   ✓ 无告警")
    except ValueError:
        print(f"   {out[:200]}")
else:
    print("   （私有仓库默认未开启，公开后 GitHub 会自动扫描）")

# ------------------------------------------------------- 2. 跟踪文件清单 --
print("\n② 仓库跟踪的文件中是否有敏感路径")
tracked = git(["ls-files"]).splitlines()
danger_patterns = [
    (r"^data/", "运行时数据库/配置"),
    (r"^\.deploy-keys/", "部署私钥"),
    (r"^dist/", "分发包二进制"),
    (r"^\.build-kit/", "构建中间产物"),
    (r"^tools/capture/", "抓包产物"),
    (r"^tools/\.private-terms$", "本地真实标识清单"),
    (r"\.env$", "环境变量"),
    (r"proxy_backup\.txt$", "系统代理备份"),
    (r"\.pem$|\.p12$|\.key$", "证书/私钥"),
    (r"\.db$|\.db-wal$|\.db-shm$", "数据库"),
]
found = []
for pattern, label in danger_patterns:
    for f in tracked:
        if re.search(pattern, f):
            found.append(f"   ⚠ [{label}] {f}")
if found:
    print("\n".join(found))
else:
    print(f"   ✓ {len(tracked)} 个跟踪文件，无敏感路径")

# --------------------------------------------------------- 3. 内容扫描 --
print("\n③ 全部跟踪文件的内容扫描")
needles_file = ROOT / "tools" / ".private-terms"
needles = []
if needles_file.exists():
    for raw in needles_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            needles.append(line)

patterns = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "私钥块"),
    (re.compile(r"eyJ[A-Za-z0-9_\-]{40,}\.[A-Za-z0-9_\-]{20,}"), "完整 JWT"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"), "GitHub token"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS Access Key"),
    # 只查「真正可能是个人凭据」的赋值：
    #   * 排除 client_id / client_secret —— 那是小程序前端的**应用级**凭据，
    #     逆向公开分发的小程序即可获得，不是任何人的私密信息
    #     （见 THIRD_PARTY_NOTICES.md 的说明）
    #   * 排除形如 "学校密码 with 中文" 的测试夹具（含中文或空格）
    (
        re.compile(
            r"(?i)\b(password|passwd|token|api_?key|access_?key)\s*[=:]\s*"
            r"[\"']([A-Za-z0-9_\-+/=@!#$%^&*.]{12,})[\"']"
        ),
        "疑似硬编码口令",
    ),
]
hits = []
for f in tracked:
    p = ROOT / f
    if not p.is_file():
        continue
    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        continue
    for n in needles:
        if n in text:
            hits.append(f"   ⚠ {f}  含本地标识 [{n[:16]}]")
    for rx, label in patterns:
        m = rx.search(text)
        if m:
            hits.append(f"   ⚠ {f}  命中「{label}」: {m.group(0)[:40]}")

if hits:
    print("\n".join(hits))
else:
    print("   ✓ 无命中")

# ----------------------------------------------------------- 4. 提交作者 --
print("\n④ 提交作者（公开后所有邮箱都会可见）")
log = git(["log", "--format=%h|%an|%ae|%s"])
no_reply = "users.noreply.github.com"
leaky = []
for line in log.splitlines():
    parts = line.split("|", 3)
    if len(parts) < 4:
        continue
    h, an, ae, subj = parts
    flag = ""
    if ae and no_reply not in ae:
        flag = "   <-- 真实邮箱会公开"
        leaky.append(h)
    print(f"   {h}  {an} <{ae}>{flag}")
    print(f"        {subj[:60]}")
if leaky:
    print(f"\n   ⚠ {len(leaky)} 个提交用了真实邮箱：{leaky}")

# -------------------------------------------------------------- 5. 规模 --
print("\n⑤ 仓库规模")
print(f"   跟踪文件: {len(tracked)}")
size = subprocess.run(["git", "count-objects", "-vH"], capture_output=True,
                      cwd=str(ROOT))
for line in (size.stdout or b"").decode("utf-8", "replace").splitlines():
    if line.startswith(("count:", "size-pack:", "size:")):
        print(f"   {line}")

print()
print("=" * 74)
problems = len(found) + len(hits)
print("结论：" + ("✓ 可以公开" if not problems else f"⚠ 有 {problems} 处需处理"))
if leaky:
    print(f"      另有 {len(leaky)} 个提交用了真实邮箱（建议公开前改写）")
print("=" * 74)
