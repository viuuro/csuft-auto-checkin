#!/usr/bin/env python3
"""读取同学发来的「token-result.txt」授权文件，校验并可直接导入服务器。

用法::

    # 只校验
    python tools/read_token_file.py "路径/token-result.txt"

    # 校验 + 直接导入服务器（一条命令搞定）
    python tools/read_token_file.py "路径/token-result.txt" --host <你的服务器地址>
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from app.flysource import (  # noqa: E402
    SIGN_STATUS_NAMES,
    WX_CLIENT_ID,
    WX_CLIENT_SECRET,
    FlySourceClient,
    jwt_claims,
    token_days_left,
)

JWT_RE = re.compile(r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+")


def read_file(path: Path) -> tuple[str, str]:
    """从文件里提取 access_token 与 refresh_token。"""
    raw = path.read_bytes()
    text = None
    for enc in ("gbk", "utf-8", "utf-8-sig"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise SystemExit(f"无法解码文件：{path}")

    # 按小节切分
    access = refresh = ""
    section = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("==="):
            section = "access" if "access" in stripped else (
                "refresh" if "refresh" in stripped else ""
            )
            continue
        m = JWT_RE.search(stripped)
        if not m:
            continue
        if section == "access" and not access:
            access = m.group(0)
        elif section == "refresh" and not refresh:
            refresh = m.group(0)
        elif not access:
            access = m.group(0)
        elif not refresh and m.group(0) != access:
            refresh = m.group(0)
    return access, refresh


def main() -> int:
    parser = argparse.ArgumentParser(description="读取并校验同学的授权文件")
    parser.add_argument("file")
    parser.add_argument("--save-to", default="", help="另存为 JSON")
    parser.add_argument("--host", default="", help="给了就直接导入该服务器")
    parser.add_argument("--user", default="root")
    parser.add_argument("--password-env", default="ASI_SSH_PASSWORD")
    args = parser.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"✗ 文件不存在：{path}")
        return 1

    print("=" * 74)
    print("同学授权文件校验")
    print("=" * 74)
    print(f"  文件: {path.name}  ({path.stat().st_size} 字节)")

    access, refresh = read_file(path)
    if not access:
        print("  ✗ 文件里没有找到 access_token")
        return 1

    claims = jwt_claims(access)
    student_id = str(claims.get("accountNo") or "")
    name = str(claims.get("userName") or "")
    days = token_days_left(access)

    print(f"\n  身份       : {student_id}  {name}")
    print(f"  学校       : {claims.get('schoolName')}")
    print(f"  access_token: {len(access)} 字符")
    print(f"  refresh_token: {len(refresh)} 字符" if refresh else "  refresh_token: 无")
    if days is not None:
        print(f"  有效期剩余 : {days:.1f} 天")
        import datetime as dt

        exp = claims.get("exp")
        if exp:
            print(f"  过期时间   : {dt.datetime.fromtimestamp(exp):%Y-%m-%d %H:%M:%S}")

    print("\n  正在用该 token 调用学校接口校验…")
    client = FlySourceClient(client_id=WX_CLIENT_ID, client_secret=WX_CLIENT_SECRET)
    client.access_token = access
    ok = False
    try:
        tasks = client.list_tasks()
        if not tasks:
            print("  ✗ 拉不到打卡任务")
        else:
            print(f"  ✓ 任务列表拉取成功（{len(tasks)} 个）")
            task = client.get_task(tasks[0].task_id)
            print(f"      任务     : {task.task_name}")
            print(f"      签到时段 : {task.sign_start_time} ~ {task.sign_end_time}")
            print(f"      宿舍     : {task.dorm_name} {task.room_no}")
            print(f"      基准坐标 : ({task.location_lat}, {task.location_lng})")
            print(f"      需定位/拍照: {task.open_locate} / {task.open_take_photo}")
            one = client.today_record(task.task_id)
            st = one.get("signStatus")
            try:
                st_int = int(st)
            except (TypeError, ValueError):
                st_int = -1
            print(f"      今日状态 : {st} ({SIGN_STATUS_NAMES.get(st_int, '未知')})")
            ok = True
    except Exception as exc:  # noqa: BLE001
        print(f"  ✗ 校验失败：{exc}")
    finally:
        client.close()

    if args.save_to and ok:
        out = Path(args.save_to)
        out.write_text(
            json.dumps(
                [
                    {
                        "studentId": student_id,
                        "name": name,
                        "token": access,
                        "refreshToken": refresh,
                        "planSignTime": "",
                    }
                ],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\n  已另存为 {out}")

    print("\n" + "=" * 74)
    print("结论：✓ 该授权可用" if ok else "结论：✗ 该授权不可用")
    print("=" * 74)

    if not ok or not args.host:
        if ok and not args.host:
            print("\n提示：加 --host <你的服务器地址> 可直接导入服务器。")
        return 0 if ok else 1

    # ---------------------------------------------------------- 导入服务器 --
    print()
    print("=" * 74)
    print("导入服务器")
    print("=" * 74)

    password = os.environ.get(args.password_env, "")
    if not password:
        print(f"  请先设置环境变量 {args.password_env}")
        return 1

    # 写一个临时 JSON 给 import_token 用（用完立即删除）
    fd, tmp_name = tempfile.mkstemp(suffix=".json", prefix="asi-import-")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        tmp.write_text(
            json.dumps(
                [{
                    "studentId": student_id,
                    "name": name,
                    "token": access,
                    "refreshToken": refresh,
                    "planSignTime": "",
                }],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        import subprocess

        proc = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "import_token.py"),
             "--host", args.host, "--user", args.user, "--from-json", str(tmp)],
            timeout=900,
        )
        return proc.returncode
    finally:
        tmp.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
