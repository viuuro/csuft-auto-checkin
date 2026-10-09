#!/usr/bin/env python3
"""对着真实服务器验证逆向出来的接口链路。

用法::

    python tools/probe.py --username 学号 --password 密码
    python tools/probe.py --username 学号 --password 密码 --do-sign

默认是**只读**探测：登录、列任务、查任务详情、查今日状态、查本月记录。
只有显式加上 ``--do-sign`` 才会真的发起签到请求。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import traceback

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

from app.flysource import (  # noqa: E402
    AuthExpired,
    CaptchaRequired,
    FlySourceClient,
    FlySourceError,
    SIGN_STATUS_NAMES,
)


def hr(title: str) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def show(label: str, value: object) -> None:
    print(f"  {label}: {value}")


def main() -> int:
    parser = argparse.ArgumentParser(description="中南林学工签到接口探针")
    parser.add_argument("--username", required=True, help="学号")
    parser.add_argument("--password", required=True, help="密码（明文，仅本地使用）")
    parser.add_argument("--tenant-id", default="000000")
    parser.add_argument("--base-url", default="https://simp.csuft.edu.cn")
    parser.add_argument("--captcha-code", default="", help="若服务端要求验证码，填入验证码")
    parser.add_argument("--captcha-key", default="", help="配合 --captcha-code 使用")
    parser.add_argument("--do-sign", action="store_true", help="真的执行签到（有副作用）")
    parser.add_argument("--insecure", action="store_true", help="跳过 TLS 校验")
    args = parser.parse_args()

    client = FlySourceClient(
        args.base_url, tenant_id=args.tenant_id, verify_tls=not args.insecure
    )

    try:
        # ---------------------------------------------------------------- 1
        hr("1. 获取图形验证码")
        try:
            key, image = client.get_captcha()
            show("captcha_key", key or "(服务端未返回 key)")
            show("image_bytes", len(image))
            ext = "jpg" if image[:2] == b"\xff\xd8" else "png" if image[:4] == b"\x89PNG" else "bin"
            out = f"captcha_sample.{ext}"
            with open(out, "wb") as fh:
                fh.write(image)
            show("已保存", out)
        except FlySourceError as exc:
            show("结果", f"失败或不需要：{exc}")

        # ---------------------------------------------------------------- 2
        hr("2. 账号密码登录")
        try:
            result = client.login(
                args.username,
                args.password,
                captcha_key=args.captcha_key,
                captcha_code=args.captcha_code,
            )
        except CaptchaRequired as exc:
            show("结果", f"服务端要求图形验证码：{exc}")
            show("captcha_key", exc.captcha_key)
            print("\n  -> 需要用 --captcha-key/--captcha-code 重试")
            return 2
        except FlySourceError as exc:
            show("结果", f"登录失败：{exc}")
            if exc.payload:
                print("  payload:", json.dumps(exc.payload, ensure_ascii=False)[:600])
            return 1

        show("access_token", result.access_token[:24] + "..." if result.access_token else "(空)")
        show("token 长度", len(result.access_token))
        show("expires_in", result.expires_in)
        show("accountNo", result.account_no)
        show("userName", result.user_name)
        show("schoolName", result.school_name)
        show("tenantId", result.tenant_id)
        print("\n  raw 字段名:", sorted(result.raw.keys()))

        # ---------------------------------------------------------------- 3
        hr("3. 打卡任务列表 getListForApp")
        try:
            tasks = client.list_tasks()
        except AuthExpired as exc:
            show("结果", f"token 被判失效：{exc}")
            return 1
        if not tasks:
            show("结果", "没有返回任何任务（可能当前无打卡任务）")
            return 0
        for i, task in enumerate(tasks):
            print(f"  [{i}] {task.task_id}  {task.task_name or '(无名称)'}")
            show("签到时段", f"{task.sign_start_time} ~ {task.sign_end_time}")
            show("宿舍", f"{task.dorm_name} {task.room_no} roomId={task.room_id}")
            show("基准坐标", f"({task.location_lat}, {task.location_lng}) 允许误差={task.location_accuracy}m")
            show("openLocate / openTakePhoto", f"{task.open_locate} / {task.open_take_photo}")

        task = tasks[0]
        today = dt.date.today().isoformat()

        # ---------------------------------------------------------------- 4
        hr(f"4. 任务详情 getTaskByIdForApp  taskId={task.task_id}")
        try:
            detail = client.get_task(task.task_id)
            show("taskName", detail.task_name)
            show("signStartTime", detail.sign_start_time)
            show("signEndTime", detail.sign_end_time)
            show("allowLateSignTime", detail.allow_late_sign_time)
            show("isAllowLate", detail.is_allow_late)
            show("isAllowSpecialSign", detail.is_allow_special_sign)
            show("openLocate", detail.open_locate)
            show("openTakePhoto", detail.open_take_photo)
            show("scanType", detail.scan_type)
            show("roomId", detail.room_id)
            show("基准坐标", f"({detail.location_lat}, {detail.location_lng})")
            show("locationState", detail.location_state)
            show("locationAccuracy", detail.location_accuracy)
            show("dormitoryRegisterVO 字段", sorted(detail.dorm.keys()))
        except FlySourceError as exc:
            show("结果", f"失败：{exc}")

        # ---------------------------------------------------------------- 5
        hr(f"5. 今日状态 getOne  ({today})")
        try:
            one = client.today_record(task.task_id)
            status = one.get("signStatus")
            show("signStatus", f"{status} ({SIGN_STATUS_NAMES.get(_int(status), '未知')})")
            show("signStatusName", one.get("signStatusName"))
            show("signLat / signLng", f"{one.get('signLat')} / {one.get('signLng')}")
            show("locationAccuracy", one.get("locationAccuracy"))
            show("isLateSign", one.get("isLateSign"))
            show("全部字段", sorted(one.keys()))
        except FlySourceError as exc:
            show("结果", f"失败：{exc}")

        # ---------------------------------------------------------------- 6
        hr(f"6. 本月记录 getUserListAppByMonth  month={today[:7]}")
        try:
            month = client.month_records(task.task_id, today[:7])
            show("返回天数", len(month))
            for date_str in sorted(month)[:8]:
                rec = month[date_str]
                st = rec.get("signStatus") if isinstance(rec, dict) else None
                print(f"    {date_str}  status={st} ({SIGN_STATUS_NAMES.get(_int(st), '?')})")
            sample_key = sorted(month)[0] if month else None
            if sample_key:
                print("\n    样本记录字段:", json.dumps(month[sample_key], ensure_ascii=False)[:700])
        except FlySourceError as exc:
            show("结果", f"失败：{exc}")

        # ---------------------------------------------------------------- 7
        if not args.do_sign:
            hr("7. 签到（已跳过）")
            print("  加 --do-sign 才会真正发起 stuSign 请求。")
            return 0

        hr("7. 执行签到 stuSign")
        lat, lng = task.location_lat, task.location_lng
        if lat is None or lng is None:
            show("结果", "任务没有基准坐标，无法签到")
            return 1
        show("使用坐标（宿舍基准点）", f"({lat}, {lng})")
        try:
            result = client.sign(task, latitude=lat, longitude=lng, sign_date=today)
            show("success", result.get("success"))
            show("msg", result.get("msg"))
            print("  payload:", json.dumps(result, ensure_ascii=False)[:600])
        except FlySourceError as exc:
            show("结果", f"签到失败：{exc}")
            return 1

        hr("8. 签到后复查")
        try:
            one = client.today_record(task.task_id)
            status = one.get("signStatus")
            show("signStatus", f"{status} ({SIGN_STATUS_NAMES.get(_int(status), '未知')})")
            show("signStatusName", one.get("signStatusName"))
        except FlySourceError as exc:
            show("结果", f"失败：{exc}")
        return 0

    except Exception:  # noqa: BLE001 - 探针脚本，打印完整栈更便于排查
        hr("未预期的异常")
        traceback.print_exc()
        return 1
    finally:
        client.close()


def _int(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return -1


if __name__ == "__main__":
    raise SystemExit(main())
