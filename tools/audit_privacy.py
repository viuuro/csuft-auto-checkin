#!/usr/bin/env python3
"""公开发布前的隐私信息审计。

扫描仓库中所有会被提交的文件，找出：
  * 真实学号 / 姓名
  * 服务器 IP / 部署路径
  * 密钥、口令、token
  * 本地绝对路径（含用户名）
  * 打包进分发包的证书私钥

用法::

    python tools/audit_privacy.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8")

# 不会提交进 git 的目录（构建产物、抓包产物、依赖）
SKIP_DIRS = {
    ".git", "__pycache__", ".venv", "venv", "node_modules",
    ".build-kit", "dist", "release", "tools/capture", ".deploy-keys",
    # 注意：data/ **不跳过** —— 它虽然被 .gitignore 忽略，但一旦有人
    # `git add -f` 就会连库带凭据一起提交。审计必须覆盖它。
}

TEXT_SUFFIXES = {
    ".py", ".md", ".txt", ".json", ".js", ".html", ".css", ".bat",
    ".service", ".example", ".cfg", ".toml", ".yml", ".yaml", ".sh",
    ".gitignore", ".gitattributes", "",
}

# 真实标识清单从 **不提交** 的本地文件读取。
# 这样审计脚本本身不会把真实学号 / IP / 姓名印在公开源码里。
#
# 建一个 ``tools/.private-terms``（已在 .gitignore 中），每行一条需要排查的值：
#
#     # 以 # 开头的行是注释
#     20240001
#     张三
#     203.0.113.10
#
PRIVATE_TERMS_FILE = ROOT / "tools" / ".private-terms"


def load_private_terms() -> list[str]:
    """读取本地真实标识清单；文件不存在时返回空列表。"""
    if not PRIVATE_TERMS_FILE.exists():
        return []
    terms: list[str] = []
    for raw in PRIVATE_TERMS_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            terms.append(line)
    return terms


PATTERNS = {
    "疑似学号（10 位以上纯数字）": re.compile(r"\b(19|20)\d{6,}\b"),
    "疑似中国手机号": re.compile(r"\b1[3-9]\d{9}\b"),
    "疑似公网 IPv4": re.compile(
        r"\b(?!127\.|0\.0\.0\.0|255\.|10\.|192\.168\.|172\.(?:1[6-9]|2\d|3[01])\.)"
        r"(?:\d{1,3}\.){3}\d{1,3}\b"
    ),
    "私钥块": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "JWT 形态的长串": re.compile(r"eyJ[A-Za-z0-9_\-]{40,}\.[A-Za-z0-9_\-]{20,}"),
    "疑似硬编码口令": re.compile(
        r"(?i)(password|passwd|pwd|secret|token|api_?key)\s*[=:]\s*[\"'][^\"'{}$]{6,}[\"']"
    ),
    "本地绝对路径（含用户名）": re.compile(r"[A-Za-z]:\\Users\\[^\\\s\"']+"),
    "home 绝对路径": re.compile(r"/(?:home|Users)/[a-zA-Z0-9_.\-]+"),
}


def iter_files() -> list[Path]:
    out: list[Path] = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT).as_posix()
        if any(rel == d or rel.startswith(d + "/") for d in SKIP_DIRS):
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES and path.name not in (
            ".gitignore", ".gitattributes"
        ):
            continue
        out.append(path)
    return out


def read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def main() -> int:
    print("=" * 78)
    print("公开发布前隐私审计")
    print("=" * 78)

    files = iter_files()
    print(f"\n扫描 {len(files)} 个文本文件\n")

    # ---------------------------------------------------------- 已知标识 --
    print("─" * 78)
    print("① 本地真实标识清单（必须清零）")
    print("─" * 78)
    terms = load_private_terms()
    if not terms:
        print(f"  （未找到 {PRIVATE_TERMS_FILE.relative_to(ROOT).as_posix()}）")
        print("  提示：把需要排查的真实学号 / 姓名 / IP 逐行写进该文件，")
        print("        它已被 .gitignore 忽略，不会随仓库公开。")
        print("  本次跳过第 ① 项检查。")
    known_hits = 0
    for needle in terms:
        hits: list[str] = []
        for path in files:
            if path == PRIVATE_TERMS_FILE:
                continue
            text = read_text(path)
            if text and needle in text:
                for i, line in enumerate(text.splitlines(), 1):
                    if needle in line:
                        hits.append(f"{path.relative_to(ROOT).as_posix()}:{i}")
        if hits:
            known_hits += len(hits)
            shown = needle if len(needle) <= 12 else needle[:6] + "…"
            print(f"  ⚠ [{shown}]  {len(hits)} 处")
            for h in hits[:8]:
                print(f"      {h}")
            if len(hits) > 8:
                print(f"      ... 还有 {len(hits) - 8} 处")
    print(f"\n  合计 {known_hits} 处需要处理")

    # ------------------------------------------------------------ 模式 --
    print()
    print("─" * 78)
    print("② 模式匹配（需人工判断）")
    print("─" * 78)
    for label, pattern in PATTERNS.items():
        hits = []
        for path in files:
            text = read_text(path)
            if not text:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if pattern.search(line):
                    snippet = line.strip()[:88]
                    hits.append(f"{path.relative_to(ROOT).as_posix()}:{i}: {snippet}")
        print(f"\n  【{label}】{len(hits)} 处")
        for h in hits[:10]:
            print(f"      {h}")
        if len(hits) > 10:
            print(f"      ... 还有 {len(hits) - 10} 处")

    # -------------------------------------------------------- 数据文件 --
    print()
    print("─" * 78)
    print("③ 运行时数据文件（不应提交）")
    print("─" * 78)
    ignore = ""
    gi = ROOT / ".gitignore"
    if gi.exists():
        ignore = gi.read_text(encoding="utf-8")

    risky = [
        "data/app.db", "data/config.json",
        ".deploy-keys/aliyun_deploy", ".deploy-keys/aliyun_deploy.pub",
        "tools/proxy_backup.txt", "dist/checkin-auth-tool.zip",
    ]
    for rel in risky:
        p = ROOT / rel
        if not p.exists():
            print(f"  -  {rel:42s} 不存在")
            continue
        size = p.stat().st_size
        top = rel.split("/")[0]
        covered = f"{top}/" in ignore or rel in ignore or f"/{rel}" in ignore
        print(f"  {'✓' if covered else '⚠'}  {rel:42s} {size:>10,} B   "
              f"gitignore: {'是' if covered else '否'}")

    print()
    print("=" * 78)
    if known_hits:
        print(f"结论：发现 {known_hits} 处真实标识，必须处理后再公开")
    else:
        print("结论：未发现已知真实标识")
    print("=" * 78)
    return 1 if known_hits else 0


if __name__ == "__main__":
    raise SystemExit(main())
