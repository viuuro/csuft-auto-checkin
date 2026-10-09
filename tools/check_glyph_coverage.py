#!/usr/bin/env python3
"""读 woff2 的 cmap 表，判断指定码位是否被字体覆盖。

woff2 结构
----------
    header(48B) + tableDirectory + [collectionDirectory] + compressedData

其中每个表的 4 字节 tag 以 **Known Tags** 表编码为单字节（woff2 规范里的
63 个已知 tag），所以解码目录需要那张表。数据整体用 brotli 压缩。

本脚本实现到「解出未压缩 sfnt → 定位 cmap → 读 format 4/12」为止，
足以回答「这个字符在不在字体里」。
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

import brotli

sys.stdout.reconfigure(encoding="utf-8")

# woff2 规范的 Known Tags（索引 = 单字节值）
KNOWN_TAGS = [
    "cmap", "head", "hhea", "hmtx", "maxp", "name", "OS/2", "post", "cvt ", "fpgm",
    "glyf", "loca", "prep", "CFF ", "VORG", "EBDT", "EBLC", "gasp", "hdmx", "kern",
    "LTSH", "PCLT", "VDMX", "vhea", "vmtx", "BASE", "GDEF", "GPOS", "GSUB", "EBSC",
    "JSTF", "MATH", "CBDT", "CBLC", "COLR", "CPAL", "SVG ", "sbix", "acnt", "avar",
    "bdat", "bloc", "bsln", "cvar", "fdsc", "feat", "fmtx", "fvar", "gvar", "hsty",
    "just", "lcar", "mort", "morx", "opbd", "prop", "trak", "Zapf", "Silf", "Glat",
    "Gloc", "Feat", "Sill",
]

# 需要判断的码位
CHECKS = {
    0x2731: "✱ HEAVY ASTERISK（kicker 前缀）",
    0x2014: "— EM DASH",
    0x2192: "→ RIGHTWARDS ARROW",
    0x00B7: "· MIDDLE DOT",
    0x300C: "「 LEFT CORNER BRACKET",
    0x300D: "」 RIGHT CORNER BRACKET",
    0x3002: "。 IDEOGRAPHIC FULL STOP",
    0xFF0C: "， FULLWIDTH COMMA",
    0xFF1A: "： FULLWIDTH COLON",
    0x4E00: "一 CJK 样本字",
}


def read_base128(data: bytes, pos: int) -> tuple[int, int]:
    """woff2 的 255UInt16 / UIntBase128 解码（简化版，够用）。"""
    result = 0
    for _ in range(5):
        b = data[pos]
        pos += 1
        result = (result << 7) | (b & 0x7F)
        if not (b & 0x80):
            return result, pos
    raise ValueError("UIntBase128 过长")


def woff2_tables(data: bytes) -> dict[str, tuple[int, int]]:
    """解出 {tag: (offset, length)}（未压缩 sfnt 中的位置）。"""
    if data[:4] != b"wOF2":
        raise ValueError("不是 woff2")
    num_tables = struct.unpack(">H", data[12:14])[0]
    pos = 48
    entries: list[tuple[str, int]] = []
    for _ in range(num_tables):
        flags = data[pos]
        pos += 1
        idx = flags & 0x3F
        tag = KNOWN_TAGS[idx] if idx < len(KNOWN_TAGS) else data[pos : pos + 4].decode(
            "latin-1"
        )
        if idx >= len(KNOWN_TAGS):
            pos += 4
        orig_len, pos = read_base128(data, pos)
        if flags & 0x40:  # transform version 存在
            _transform, pos = read_base128(data, pos)
        entries.append((tag, orig_len))

    # 再读一遍拿 offset（offset 在 length 之后，按同样顺序）
    pos2 = 48
    offsets: list[int] = []
    for _ in range(num_tables):
        flags = data[pos2]
        pos2 += 1
        idx = flags & 0x3F
        if idx >= len(KNOWN_TAGS):
            pos2 += 4
        _orig, pos2 = read_base128(data, pos2)
        if flags & 0x40:
            _t, pos2 = read_base128(data, pos2)
        off, pos2 = read_base128(data, pos2)
        offsets.append(off)

    return {tag: (off, ln) for (tag, ln), off in zip(entries, offsets)}


def parse_cmap(sfnt: bytes, off: int) -> set[int]:
    """解析 cmap 表，返回覆盖的码位集合。"""
    version, num = struct.unpack(">HH", sfnt[off : off + 4])
    codepoints: set[int] = set()
    for i in range(num):
        rec = off + 4 + i * 8
        plat, enc, sub_off = struct.unpack(">HHI", sfnt[rec : rec + 8])
        sub = off + sub_off
        fmt = struct.unpack(">H", sfnt[sub : sub + 2])[0]
        if fmt == 4:
            segx2 = struct.unpack(">H", sfnt[sub + 6 : sub + 8])[0]
            seg = segx2 // 2
            ends = struct.unpack(f">{seg}H", sfnt[sub + 14 : sub + 14 + segx2])
            starts_off = sub + 16 + segx2
            starts = struct.unpack(f">{seg}H", sfnt[starts_off : starts_off + segx2])
            for s, e in zip(starts, ends):
                if e == 0xFFFF and s == 0xFFFF:
                    continue
                # 只记录范围（不做 glyph id 映射，判断"是否覆盖"足够）
                if e - s < 0x10000:
                    codepoints.update(range(s, e + 1))
        elif fmt == 12:
            ngroups = struct.unpack(">I", sfnt[sub + 12 : sub + 16])[0]
            for g in range(ngroups):
                go = sub + 16 + g * 12
                s, e, _gid = struct.unpack(">III", sfnt[go : go + 12])
                codepoints.update(range(s, e + 1))
    return codepoints


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    fonts = sorted((root / "video-intro" / "assets" / "fonts").glob("*.woff2"))

    print("=" * 78)
    print(f"字形覆盖（直接读 woff2 的 cmap，{len(fonts)} 个字体）")
    print("=" * 78)

    coverage: dict[str, set[int]] = {}
    for f in fonts:
        data = f.read_bytes()
        try:
            tables = woff2_tables(data)
            if "cmap" not in tables:
                print(f"  ⚠ {f.name}: 无 cmap 表")
                continue
            # brotli 解压整块，再按未压缩 offset 定位 cmap
            # woff2 的 compressedData 起点需跳过头部+目录
            header_len = 48
            # 重算目录长度
            pos = header_len
            num = struct.unpack(">H", data[12:14])[0]
            for _ in range(num):
                flags = data[pos]; pos += 1
                if (flags & 0x3F) >= len(KNOWN_TAGS):
                    pos += 4
                _, pos = read_base128(data, pos)
                if flags & 0x40:
                    _, pos = read_base128(data, pos)
                _, pos = read_base128(data, pos)
            sfnt = brotli.decompress(data[pos:])
            coverage[f.name] = parse_cmap(sfnt, tables["cmap"][0])
        except Exception as exc:  # noqa: BLE001
            print(f"  ⚠ {f.name}: 解析失败 {exc}")

    if not coverage:
        print("✗ 没有成功解析任何字体")
        return 1

    print(f"\n  {'字符':<5} {'码位':<9} 覆盖字体")
    print("  " + "-" * 70)
    missing: list[int] = []
    for cp, desc in CHECKS.items():
        have = [n.replace(".woff2", "") for n, cov in coverage.items() if cp in cov]
        if not have:
            missing.append(cp)
        mark = "✓" if have else "✗"
        names = ", ".join(have[:5]) + (f" …+{len(have)-5}" if len(have) > 5 else "")
        print(f"  {chr(cp):<5} U+{cp:04X}  {mark}  {names or '（无 → 走系统回退）'}")
        print(f"        {desc}")

    print()
    print("=" * 78)
    if missing:
        print(f"⚠ {len(missing)} 个字符无随片字体覆盖，依赖渲染机器的系统字体：")
        for cp in missing:
            print(f"    U+{cp:04X} {chr(cp)}")
        print("  → 换机器渲染可能变豆腐块或字形不同（本机渲染正常）")
    else:
        print("✓ 片中全部特殊字符均有随片字体覆盖")
    print("=" * 78)
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
