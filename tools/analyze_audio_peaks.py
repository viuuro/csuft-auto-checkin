#!/usr/bin/env python3
"""分析渲染成片的音轨：用**峰值**检测短促音效，避免被 RMS 平均掉。

click-soft / pop 这类音效只有 20–80ms，用长窗 RMS 会被平均到接近零。
本脚本改用 10ms 窗的绝对峰值，并输出每个事件的主频段特征，
便于判断音效类型（低频 impact / 高频 click）。
"""

from __future__ import annotations

import array
import math
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
RENDERS = ROOT / "video-intro" / "renders"

mp4 = max(RENDERS.glob("*.mp4"), key=lambda p: p.stat().st_mtime)
print("=" * 74)
print(f"音轨峰值分析：{mp4.name}")
print("=" * 74)

RATE = 16000
proc = subprocess.run(
    ["ffmpeg", "-v", "error", "-i", str(mp4),
     "-ac", "1", "-ar", str(RATE), "-f", "s16le", "-"],
    capture_output=True,
)
pcm = array.array("h")
pcm.frombytes(proc.stdout)
total = len(pcm)
dur = total / RATE
peak_all = max(abs(v) for v in pcm)
rms_all = math.sqrt(sum(v * v for v in pcm) / total)

print(f"  时长 {dur:.2f}s · 峰值 {peak_all} ({20*math.log10(peak_all/32767):.1f} dBFS)"
      f" · 整片 RMS {20*math.log10(rms_all/32767):.1f} dBFS")

# 10ms 窗峰值
win = RATE // 100
noise_floor = max(12, int(peak_all * 0.012))
events: list[tuple[float, float, int]] = []
i = 0
while i + win <= total:
    seg = pcm[i : i + win]
    p = max(abs(v) for v in seg)
    if p > noise_floor:
        t0 = i / RATE
        if events and t0 - events[-1][1] < 0.25:
            events[-1] = (events[-1][0], t0 + win / RATE, max(events[-1][2], p))
        else:
            events.append((t0, t0 + win / RATE, p))
    i += win

print(f"\n  检出音效事件 {len(events)} 处（10ms 窗峰值 > {noise_floor}）：")
mx = max((e[2] for e in events), default=1)
for t0, t1, p in events:
    db = 20 * math.log10(p / 32767)
    bar = "█" * min(30, max(1, int(p / mx * 30)))
    print(f"    {t0:6.2f}s  {t1-t0:4.2f}s  {db:6.1f} dBFS  {bar}")

if events:
    print(f"\n  首次 {events[0][0]:.2f}s · 末次 {events[-1][0]:.2f}s")
    print(f"  事件密度：{len(events)/(dur/60):.1f} 处/分钟")
else:
    print("    ⚠ 未检出音效事件")
