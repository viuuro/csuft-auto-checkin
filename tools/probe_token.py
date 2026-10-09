#!/usr/bin/env python3
"""用抓包得到的 access_token 直接验证整条接口链路（不需要密码）。

这是对"签名算法 + token 使用方式 + 业务接口"的一次真实环境验证。

用法::

    python tools/probe_token.py                    # 用抓到的 token（默认）
    python tools/probe_token.py --token "eyJ..."   # 指定 token
    python tools/probe_token.py --do-sign          # 真的发起一次签到
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from app.flysource import (  # noqa: E402
    SIGN_STATUS_NAMES,
    FlySourceClient,
    FlySourceError,
)

DEFAULT_TOKEN = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJwYXNzV29yZExldmVsIjowLCJhdmF0YXJVcmwiOiJjYWU2NWY0YmIxOTRmZDgwNjY5YzkzZWRjYzU5NTk2NSIs"
    "InVzZXJfbmFtZSI6IjIwMjQyNDE4IiwiYWNjb3VudFR5cGUiOjUsInVzZXJOYW1lIjoi5p2O6L2pIiwicm9sZVR5cGUiOiIzIiwidXNlcklkIjoiZTAyMTk2M2YwZDRhNWI1Zjg5MjY0MWE4YzZmYTIwNTYiLCJhdXRob3JpdGllcyI6WyJzdHVkZW50Il0sImNsaWVudF9pZCI6ImZseXNvdXJjZV93aXNlX3d4YXBwIiwibGFzdExvZ2luVGltZSI6IjIwMjYtMDktMjkgMjM6NTE6NDAiLCJvYXV0aElkIjpudWxsLCJzY29wZSI6WyJhbGwiXSwiYWNjb3VudE5vIjoiMjAyNDI0MTgiLCJ0ZW5hbnRJZCI6IjAwMDAwMCIsInJvbGVOYW1lIjoic3R1ZGVudCIsInVzZXJUeXBlIjoyLCJkZXRhaWwiOnsic3lzQXV0aFR5cGUiOiJzbXMiLCJpc1N5c1VzZXJTZWNvbmRBdXRoIjp0cnVlfSwiZXhwIjoxNzkyMzAyODY0LCJzY2hvb2xOYW1lIjoi5Lit5Y2X5p6X5Lia56eR5oqA5aSn5a2mIiwianRpIjoiZWY0YWNjNDMtYTM5NC00NmM3LTgyNjYtMDQ0YTM3YjZkZDE3In0."
    "yfXeIY0fiSLnyADLuBHRo2N6edDPA0XgEm7t6sao_JI"
)


def hr(title: str) -> None:
    print("\n" + "=" * 74)
    print(title)
    print("=" * 74)


def show(label: str, value: object) -> None:
    print(f"  {label}: {value}")


def _int(v: object) -> int:
    try:
        return int(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return -1


def main() -> int:
    parser = argparse.ArgumentParser(description="用 token 验证接口链路")
    parser.add_argument("--token", default="")
    parser.add_argument("--do-sign", action="store_true", help="真的发起签到（有副作用）")
    parser.add_argument(
        "--force-window",
        action="store_true",
        help="跳过本地签到时段检查，直接提交（用于观察服务端的真实拒绝原因）",
    )
    args = parser.parse_args()

    token = args.token.strip() or DEFAULT_TOKEN
    # 小程序用的就是 flysource_wise_wxapp 这套客户端凭据
    client = FlySourceClient(
        client_id="flysource_wise_wxapp",
        client_secret="DA788asdUDjnasd_flysource_wxappdsdadDAIUiuwqe",
    )
    client.access_token = token

    try:
        hr("0. Token 概览")
        import base64 as _b64

        parts = token.split(".")
        if len(parts) == 3:
            padded = parts[1] + "=" * (-len(parts[1]) % 4)
            claims = json.loads(_b64.urlsafe_b64decode(padded))
            show("accountNo", claims.get("accountNo"))
            show("userName", claims.get("userName"))
            show("schoolName", claims.get("schoolName"))
            show("client_id", claims.get("client_id"))
            exp = claims.get("exp")
            if exp:
                show("过期时间", dt.datetime.fromtimestamp(exp).strftime("%Y-%m-%d %H:%M:%S"))
                left = (dt.datetime.fromtimestamp(exp) - dt.datetime.now()).total_seconds() / 86400
                show("剩余", f"{left:.1f} 天")
            show("二次认证", claims.get("detail"))

        hr("1. 任务列表 getListForApp")
        tasks = client.list_tasks()
        if not tasks:
            show("结果", "没有任务")
            return 1
        for i, t in enumerate(tasks):
            print(f"  [{i}] {t.task_id}  {t.task_name}")
            show("签到时段", f"{t.sign_start_time} ~ {t.sign_end_time}")
            show("宿舍", f"{t.dorm_name} {t.room_no} (roomId={t.room_id})")
            show("基准坐标", f"({t.location_lat}, {t.location_lng})  允许误差 {t.location_accuracy}m")
            show("openLocate / openTakePhoto", f"{t.open_locate} / {t.open_take_photo}")
        print("\n  ★ 签名正确，任务列表拉取成功")

        task = tasks[0]
        today = dt.date.today().isoformat()

        hr(f"2. 任务详情 getTaskByIdForApp  taskId={task.task_id}")
        detail = client.get_task(task.task_id)
        for label, value in (            ("taskName", detail.task_name),
            ("signStartTime", detail.sign_start_time),
            ("signEndTime", detail.sign_end_time),
            ("allowLateSignTime", detail.allow_late_sign_time),
            ("isAllowLate", detail.is_allow_late),
            ("isAllowSpecialSign", detail.is_allow_special_sign),
            ("openLocate", detail.open_locate),
            ("openTakePhoto", detail.open_take_photo),
            ("scanType", detail.scan_type),
            ("roomId", detail.room_id),
            ("locationState", detail.location_state),
            ("dormitoryRegisterVO 字段", sorted(detail.dorm.keys())),
        ):
            show(label, value)

        # 列表接口不返回宿舍信息，详情才有 —— 后续签到必须用详情
        if detail.location_lat is not None:
            task = detail
            show("已切换到详情任务", f"基准坐标 ({task.location_lat}, {task.location_lng})")
        else:
            show("警告", "详情里也没有宿舍基准坐标")

        hr(f"3. 今日状态 getOne  ({today})")
        one = client.today_record(task.task_id)
        st = one.get("signStatus")
        show("signStatus", f"{st} ({SIGN_STATUS_NAMES.get(_int(st), '未知')})")
        show("signStatusName", one.get("signStatusName"))
        show("signLat / signLng", f"{one.get('signLat')} / {one.get('signLng')}")
        show("locationAccuracy", one.get("locationAccuracy"))
        show("全部字段", sorted(one.keys()))

        hr(f"4. 本月记录 getUserListAppByMonth  month={today[:7]}")
        month = client.month_records(task.task_id, today[:7])
        show("返回天数", len(month))
        for d in sorted(month)[:10]:
            rec = month[d]
            s = rec.get("signStatus") if isinstance(rec, dict) else None
            print(f"    {d}  status={s} ({SIGN_STATUS_NAMES.get(_int(s), '?')})")
        if month:
            k = sorted(month)[0]
            print("\n    样本记录:", json.dumps(month[k], ensure_ascii=False)[:600])

        if not args.do_sign:
            hr("5. 签到（已跳过）")
            print("  加 --do-sign 才会真正提交 stuSign。")
            print("  注意：服务端会校验定位，签到坐标需要用宿舍基准点。")
            return 0

        hr("5. 执行签到 stuSign")
        lat, lng = task.location_lat, task.location_lng
        if lat is None or lng is None:
            show("结果", "任务无基准坐标，无法签到")
            return 1
        show("使用坐标", f"({lat}, {lng})  （宿舍基准点）")

        # 本地时段检查（服务端在窗口外会返回 未到签到时间！）
        in_window, reason = task.in_window()
        show("签到时段", f"{task.sign_start_time} ~ {task.sign_end_time}")
        show("时段检查", reason)
        if not in_window and not args.force_window:
            print("\n  本地已拦下（未发请求）。若想看服务端的真实回应，加 --force-window。")
            return 0
        if not in_window:
            print("  --force-window 已指定，仍提交，观察服务端回应…")

        result = client.sign(
            task, latitude=lat, longitude=lng, sign_date=today,
            check_window=args.force_window is False,
        )
        show("success", result.get("success"))
        show("msg", result.get("msg"))
        print("  payload:", json.dumps(result, ensure_ascii=False)[:500])

        hr("6. 签到后复查")
        after = client.today_record(task.task_id)
        s = after.get("signStatus")
        show("signStatus", f"{s} ({SIGN_STATUS_NAMES.get(_int(s), '未知')})")
        return 0

    except FlySourceError as exc:
        hr("失败")
        show("错误", exc.message)
        if exc.payload:
            print("  payload:", json.dumps(exc.payload, ensure_ascii=False)[:500])
        return 1
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
