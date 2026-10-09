#!/usr/bin/env python3
"""前端接线检查：HTML 里的 id 与 JS 里 getElementById 的引用是否一一对应。

能抓出"点了没反应"这类最常见的低级 bug。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

STATIC = Path("app/static")
problems = 0

PAIRS = [
    ("index.html", "app.js"),
    ("admin.html", "admin.js"),
]

for html_name, js_name in PAIRS:
    html = (STATIC / html_name).read_text(encoding="utf-8")
    js = (STATIC / js_name).read_text(encoding="utf-8")

    html_ids = set(re.findall(r'\bid=["\']([^"\']+)["\']', html))
    js_ids = set(re.findall(r'getElementById\(\s*["\']([^"\']+)["\']', js))
    js_ids |= set(re.findall(r'\$\(\s*["\']([^"\']+)["\']\s*\)', js))
    # data-* 属性驱动的事件绑定
    data_attrs = set(re.findall(r"data-([a-z]+)=", html))

    # JS 动态创建的元素：模板串 / 拼接串里写出的 id
    dynamic_ids = set(re.findall(r'id=["\']([^"\'$]+)["\']', js))
    # 'tab-' + x 这类拼接：收集前缀，后续按前缀匹配
    dynamic_prefixes = set(re.findall(r'["\']([a-z-]+-)["\']\s*\+', js))

    print(f"\n{'=' * 70}\n{html_name}  /  {js_name}\n{'=' * 70}")
    print(f"  HTML id 数：{len(html_ids)}    JS 引用数：{len(js_ids)}")
    if dynamic_ids:
        print(f"  JS 动态创建的 id：{sorted(dynamic_ids)}")
    if dynamic_prefixes:
        print(f"  JS 拼接的 id 前缀：{sorted(dynamic_prefixes)}")

    def satisfied(name: str) -> bool:
        if name in html_ids or name in dynamic_ids:
            return True
        return any(name.startswith(p) for p in dynamic_prefixes)

    missing = sorted(n for n in js_ids if not satisfied(n))
    if missing:
        problems += len(missing)
        print("\n  [FAIL] JS 引用了不存在的 id（会导致点击无反应）：")
        for name in missing:
            line = next(
                (i for i, t in enumerate(js.splitlines(), 1)
                 if f'"{name}"' in t or f"'{name}'" in t),
                -1,
            )
            print(f"         {name}   (js 第 {line} 行)")
    else:
        print("  [ OK ] JS 引用的 id 全部有来源（HTML 静态 或 JS 动态创建）")

    unused = sorted(html_ids - js_ids)
    if unused:
        print("\n  [info] HTML 中未被 JS 引用的 id（可能仅用于样式/展示）：")
        for name in unused:
            print(f"         {name}")

    # 检查 JS 里用到的 data-* 属性是否在 HTML 里存在
    js_data = set(re.findall(r'data-([a-z]+)[=\]]', js))
    js_data |= set(re.findall(r'\bdata-([a-z]+)\b', js))
    missing_data = sorted(js_data - data_attrs - {"tab"})
    if missing_data:
        print(f"\n  [info] JS 里出现但 HTML 未定义的 data-*：{missing_data}")

print(f"\n{'=' * 70}")
print(f"发现 {problems} 处接线错误" if problems else "前端接线检查通过")
print("=" * 70)
raise SystemExit(1 if problems else 0)
