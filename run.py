#!/usr/bin/env python3
"""启动脚本。

用法::

    python run.py                  # 默认监听 0.0.0.0:8000
    python run.py --port 9000      # 换端口
    python run.py --host 127.0.0.1 # 只监听本机

生产环境建议用 systemd + uvicorn（见 docs/DEPLOY.md）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="中南林学工自动签到服务")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址")
    parser.add_argument("--port", type=int, default=8000, help="监听端口")
    parser.add_argument("--reload", action="store_true", help="开发模式热重载")
    parser.add_argument("--workers", type=int, default=1, help="工作进程数（调度器存在时请保持 1）")
    args = parser.parse_args()

    if args.workers > 1 and not args.reload:
        print(
            "警告：本服务内置定时调度器，多进程会导致重复签到。"
            "请保持 --workers 1。",
            file=sys.stderr,
        )

    print(f"用户端  http://127.0.0.1:{args.port}/")
    print(f"管理端  http://127.0.0.1:{args.port}/admin")
    print(f"接口文档 http://127.0.0.1:{args.port}/api/docs")

    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        workers=1 if args.reload else args.workers,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
