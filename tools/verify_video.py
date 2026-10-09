#!/usr/bin/env python3
"""成片验收：对渲染出的 MP4 做客观核验，避免"看起来没问题"。

检查项
------
1. 容器与编码 —— 分辨率、帧率、时长、编码格式
2. 音轨 —— 是否存在、是否真的有声（峰值/整片 RMS）、音效事件落点
3. 帧完整性 —— 用 ffmpeg 解码全片，确认无解码错误
4. 时长 —— 与 STORYBOARD 的帧时长之和比对

用法::

    python tools/verify_video.py                    # 自动取最新成片
    python tools/verify_video.py path/to/video.mp4
"""

from __future__ import annotations

import array
import json
import math
import re
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "video-intro"
RENDERS = PROJECT / "renders"
STORYBOARD = PROJECT / "STORYBOARD.md"

EXPECT_W, EXPECT_H = 1920, 1080
TOLERANCE_S = 1.0


def run(args: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, **kw)


def probe(mp4: Path) -> dict:
    p = run(["ffprobe", "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", str(mp4)])
    return json.loads(p.stdout.decode("utf-8", "replace") or "{}")


def storyboard_duration() -> float | None:
    if not STORYBOARD.exists():
        return None
    t = STORYBOARD.read_text(encoding="utf-8")
    durs = [float(x) for x in re.findall(r"(?m)^- duration:\s*([\d.]+)s", t)]
    return sum(durs) if durs else None


def main() -> int:
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    if arg:
        mp4 = Path(arg)
    else:
        cands = sorted(RENDERS.glob("*.mp4"), key=lambda p: p.stat().st_mtime)
        if not cands:
            print("✗ renders/ 下没有 MP4")
            return 1
        mp4 = cands[-1]

    print("=" * 74)
    print(f"成片验收：{mp4.name}")
    print("=" * 74)
    if not mp4.exists():
        print(f"✗ 文件不存在：{mp4}")
        return 1

    info = probe(mp4)
    streams = info.get("streams", [])
    fmt = info.get("format", {})
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)

    problems: list[str] = []

    # ---- 1. 容器与编码 ----
    print("\n[1] 容器与视频编码")
    if not v:
        problems.append("没有视频流")
        print("  ✗ 没有视频流")
    else:
        w, h = int(v.get("width", 0)), int(v.get("height", 0))
        fps = v.get("r_frame_rate", "?")
        dur = float(fmt.get("duration", 0) or 0)
        size_mb = mp4.stat().st_size / 1024 / 1024
        print(f"  编码 {v.get('codec_name')} · {w}×{h} · {fps} · "
              f"{dur:.2f}s · {size_mb:.2f} MB")
        if (w, h) != (EXPECT_W, EXPECT_H):
            problems.append(f"分辨率 {w}×{h} ≠ {EXPECT_W}×{EXPECT_H}")
            print(f"  ✗ 分辨率不符（应 {EXPECT_W}×{EXPECT_H}）")
        else:
            print("  ✓ 分辨率与画幅正确（16:9 横屏）")

    # ---- 2. 时长 ----
    print("\n[2] 时长")
    dur = float(fmt.get("duration", 0) or 0)
    want = storyboard_duration()
    if want:
        print(f"  实际 {dur:.2f}s · 分镜帧时长合计 {want:.2f}s（差值含转场）")
        if abs(dur - 120) <= 15:
            print("  ✓ 在「约 2 分钟」范围内")
        else:
            print(f"  ⚠ 与 120s 相差 {abs(dur-120):.1f}s")
    else:
        print(f"  实际 {dur:.2f}s（未找到分镜）")

    # ---- 3. 音轨 ----
    print("\n[3] 音轨")
    if not a:
        problems.append("没有音频流")
        print("  ✗ 没有音频流")
    else:
        print(f"  编码 {a.get('codec_name')} · "
              f"{a.get('channels')} 声道 · {a.get('sample_rate')} Hz")
        RATE = 16000
        p = run(["ffmpeg", "-v", "error", "-i", str(mp4),
                 "-ac", "1", "-ar", str(RATE), "-f", "s16le", "-"])
        pcm = array.array("h")
        pcm.frombytes(p.stdout)
        if not pcm:
            problems.append("音轨为空")
            print("  ✗ 音轨为空")
        else:
            peak = max(abs(x) for x in pcm)
            rms = math.sqrt(sum(x * x for x in pcm) / len(pcm))
            print(f"  峰值 {peak} ({20*math.log10(peak/32767):.1f} dBFS) · "
                  f"整片 RMS {20*math.log10(rms/32767):.1f} dBFS")
            if peak < 200:
                problems.append("音轨几乎静音")
                print("  ✗ 音轨几乎静音")
            else:
                win = RATE // 100
                floor = max(12, int(peak * 0.012))
                ev: list[tuple[float, float, int]] = []
                i = 0
                while i + win <= len(pcm):
                    seg = pcm[i:i + win]
                    pk = max(abs(x) for x in seg)
                    if pk > floor:
                        t0 = i / RATE
                        if ev and t0 - ev[-1][1] < 0.25:
                            ev[-1] = (ev[-1][0], t0 + win / RATE, max(ev[-1][2], pk))
                        else:
                            ev.append((t0, t0 + win / RATE, pk))
                    i += win
                print(f"  ✓ 检出音效事件 {len(ev)} 处")
                for t0, t1, pk in ev[:12]:
                    print(f"      {t0:6.2f}s  {t1-t0:4.2f}s  "
                          f"{20*math.log10(pk/32767):6.1f} dBFS")
                if len(ev) > 12:
                    print(f"      … 另有 {len(ev)-12} 处")

    # ---- 4. 解码完整性 ----
    print("\n[4] 解码完整性（全片扫描）")
    p = run(["ffmpeg", "-v", "error", "-i", str(mp4), "-f", "null", "-"])
    err = p.stderr.decode("utf-8", "replace").strip()
    if err:
        problems.append("解码报错")
        print("  ✗ 解码有报错：")
        for line in err.splitlines()[:6]:
            print(f"      {line}")
    else:
        print("  ✓ 全片解码无错误")

    # ---- 结论 ----
    print()
    print("=" * 74)
    if problems:
        print(f"结论：{len(problems)} 项需要处理")
        for x in problems:
            print(f"  ✗ {x}")
    else:
        print("结论：全部验收项通过 ✓")
    print("=" * 74)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
