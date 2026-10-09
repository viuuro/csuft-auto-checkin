#!/usr/bin/env python3
"""一条命令把某个学号加为已批准用户，便于自测与代同学录入。

用法::

    # 只批准（同学随后自己去网页绑定）
    python tools/add_user.py 20240001 --name 张三

    # 批准并直接绑定学校账号，立刻开始自动签到
    python tools/add_user.py 20240001 --name 张三 --password '学校密码'

绑定成功后会打印查询口令（仅显示这一次）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from app.config import get_settings  # noqa: E402
from app.db import Database  # noqa: E402
from app.security import generate_password  # noqa: E402
from app.services import ServiceError, SignInService  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="添加/批准用户并可选地绑定学校账号")
    parser.add_argument("student_id", help="学号")
    parser.add_argument("--name", default="", help="姓名")
    parser.add_argument("--phone", default="", help="手机号")
    parser.add_argument("--major", default="", help="专业/班级")
    parser.add_argument("--remark", default="", help="备注")
    parser.add_argument("--password", default="", help="学校打卡账号密码；给了就直接绑定")
    parser.add_argument("--query-password", default="", help="指定查询口令；默认随机生成")
    parser.add_argument("--plan-time", default="", help="计划签到时刻 HH:MM；默认窗口内随机")
    args = parser.parse_args()

    settings = get_settings(refresh=True)
    db = Database(settings.db_path)
    service = SignInService(db, settings)

    student_id = args.student_id.strip()
    if not student_id:
        print("学号不能为空", file=sys.stderr)
        return 1

    existing = db.get_user(student_id)
    if existing:
        print(f"用户已存在：{student_id}（状态 {existing['approval_state']}）")
        if existing["approval_state"] != "approved":
            service.approve(student_id, "cli")
            print("已批准。")
    else:
        service.submit_application(
            student_id,
            name=args.name,
            phone=args.phone,
            remark=args.remark,
            major=args.major,
        )
        service.approve(student_id, "cli")
        print(f"已创建并批准：{student_id}")

    if not args.password:
        print(f"\n下一步：让该同学打开用户端，用学号 {student_id} 完成“首次绑定学校账号”。")
        return 0

    query_password = args.query_password or generate_password()
    print(f"\n正在用学校账号 {student_id} 登录并绑定……")
    try:
        result = service.bind_credentials(
            student_id,
            args.password,
            query_password,
            plan_sign_time=args.plan_time,
        )
    except ServiceError as exc:
        print(f"\n绑定失败：{exc}", file=sys.stderr)
        print("（凭据未写入，可以用正确密码重试）", file=sys.stderr)
        return 1

    user = result["user"]
    task = result["task"]
    print("\n绑定成功")
    print(f"  学号      : {student_id}")
    print(f"  查询口令  : {query_password}    ← 请立即记录，仅显示这一次")
    print(f"  签到时刻  : {user.get('planSignTime')}（窗口 {settings.sign_window_start}-{settings.sign_window_end}）")
    print(f"  打卡任务  : {task.get('taskName') or task.get('taskId')}")
    print(f"  宿舍      : {task.get('dormName')} {task.get('roomNo')}")
    print(f"  基准坐标  : ({task.get('locationLat')}, {task.get('locationLng')}) 允许误差 {task.get('locationAccuracy')}m")
    print("\n下一步：在管理端点一次“立即执行今日签到”，即可验证能否真正签到成功。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
