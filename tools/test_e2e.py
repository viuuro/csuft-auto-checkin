#!/usr/bin/env python3
"""端到端测试：申请 → 审批 → 绑定（模拟学校接口）→ 看板 → 日历 → 管理操作。

使用 FastAPI TestClient，并用假的 FlySource 客户端替换真实网络调用，
因此不会触及 simp.csuft.edu.cn。

用法: python tools/test_e2e.py
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

import tempfile  # noqa: E402

# 必须在使用 app 之前指向临时数据目录
TMP = Path(tempfile.mkdtemp(prefix="autosinin-test-"))
import os  # noqa: E402

os.environ["DATA_DIR"] = str(TMP)
os.environ["SCHEDULER_DISABLED"] = "1"

from fastapi.testclient import TestClient  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.flysource import LoginResult, SignTask  # noqa: E402
from app.main import create_app  # noqa: E402
from app.security import hash_password  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  [PASS] {name}")
    else:
        FAILED.append(f"{name} {detail}")
        print(f"  [FAIL] {name} {detail}")


# --------------------------------------------------------------------------- #
# 假客户端
# --------------------------------------------------------------------------- #


class FakeClient:
    """模拟 FlySourceClient 的关键行为。"""

    refresh_calls = 0

    def __init__(self, *args, **kwargs) -> None:
        self.access_token = ""
        self.signed: list[dict] = []
        self.fail_login = False
        self.fail_refresh = False
        self.logged_out = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self) -> None:
        pass

    def get_captcha(self):
        return "fake-captcha-key", b"\x89PNG\r\n\x1a\n" + b"0" * 40

    def login(self, username, password, **kwargs):
        if self.fail_login:
            from app.flysource import FlySourceError

            raise FlySourceError("用户名或密码错误 ！")
        self.access_token = "fake-token-" + username
        return LoginResult(access_token=self.access_token, account_no=username)

    def login_auto(self, username, password, **kwargs):
        return self.login(username, password, **kwargs)

    def refresh_access_token(self, refresh_token):
        """模拟续期：返回新的 access/refresh（与真实服务端一样轮换）。"""
        if getattr(self, "fail_refresh", False):
            from app.flysource import FlySourceError

            raise FlySourceError("refresh_token 无效")
        FakeClient.refresh_calls += 1
        self.access_token = f"refreshed-{FakeClient.refresh_calls}"
        return LoginResult(
            access_token=self.access_token,
            refresh_token=f"refresh-{FakeClient.refresh_calls}",
        )

    def use_token(self, token: str) -> None:
        self.access_token = token

    def list_tasks(self, **kwargs):
        return [self._task()]

    def get_task(self, task_id):
        return self._task()

    def _task(self) -> SignTask:
        return SignTask(
            task_id="TASK-001",
            task_name="平安打卡",
            sign_start_time="21:00:00",
            sign_end_time="22:30:00",
            allow_late_sign_time="12:00",
            open_locate=1,
            open_take_photo=0,
            scan_type=0,
            dorm={
                "roomId": "ROOM-9",
                "dormName": "林苑 3 栋",
                "roomNo": "412",
                "locationLat": 28.1395,
                "locationLng": 112.9945,
                "locationAccuracy": 50,
                "locationState": 1,
            },
        )

    def month_records(self, task_id, month):
        year, mon = (int(x) for x in month.split("-"))
        out = {}
        for day in range(1, 29):
            date_str = f"{year:04d}-{mon:02d}-{day:02d}"
            out[date_str] = {"signStatus": 1 if day % 3 else 3, "dateTimeStr": date_str}
        return out

    def today_record(self, task_id, sign_date=""):
        return {"signStatus": 0}

    def sign(self, task, *, latitude, longitude, sign_date, **kwargs):
        self.signed.append(
            {"taskId": task.task_id, "lat": latitude, "lng": longitude, "date": sign_date}
        )
        return {"success": True, "msg": "打卡成功"}

    def logout(self) -> None:
        self.logged_out = True


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #


def main() -> int:
    imported = __import__("app.services", fromlist=["SignInService"])
    imported.FlySourceClient = FakeClient  # type: ignore[attr-defined]

    settings = get_settings(refresh=True)
    settings.admin_username = "admin"
    settings.admin_password_hash = hash_password("admin-pass-123")
    settings.data_dir = TMP
    settings.scheduler_enabled = False

    application = create_app()
    with TestClient(application) as client:
        # 用假客户端替换服务里的工厂
        service = application.state.app_state.service
        service.new_client = lambda: FakeClient()  # type: ignore[assignment]

        print("\n=== 1. 未登录状态 ===")
        r = client.get("/api/me").json()
        check("GET /api/me 未登录", r["success"] and r["loggedIn"] is False)

        print("\n=== 2. 新用户提交申请 ===")
        r = client.post(
            "/api/apply",
            json={"studentId": "20230101", "name": "张三", "phone": "13800000000"},
        ).json()
        check("提交申请成功", r["success"] and r["state"] == "submitted", str(r))

        r = client.post("/api/apply", json={"studentId": "20230101"}).json()
        check("重复提交识别为待审批", r["state"] == "already_pending", str(r))

        r = client.get("/api/apply/status?studentId=20230101").json()
        check("审批状态为 pending", r["state"] == "pending", str(r))

        print("\n=== 3. 未批准时不允许绑定 ===")
        r = client.post(
            "/api/bind",
            json={
                "studentId": "20230101",
                "password": "school-pw",
                "queryPassword": "1234",
            },
        )
        check("未批准绑定被拒绝", r.status_code == 400, str(r.json()))

        print("\n=== 4. 未批准时不允许查询 ===")
        r = client.post(
            "/api/login", json={"studentId": "20230101", "queryPassword": "1234"}
        )
        check("未批准登录被拒绝", r.status_code == 400, str(r.json()))

        print("\n=== 5. 管理员登录 ===")
        r = client.get("/admin/api/status").json()
        check("管理端已配置", r["configured"] is True, str(r))

        r = client.post(
            "/admin/api/login", json={"username": "admin", "password": "wrong"}
        )
        check("错误密码被拒绝", r.status_code == 400, str(r.json()))

        r = client.post(
            "/admin/api/login", json={"username": "admin", "password": "admin-pass-123"}
        ).json()
        check("管理员登录成功", r["success"] is True, str(r))

        print("\n=== 6. 管理员审批 ===")
        dash = client.get("/admin/api/dashboard").json()
        check("仪表盘有待审批用户", len(dash["pending"]) == 1, str(dash["pending"]))
        check("未配置学期时不执行签到", dash["today"]["shouldSign"] is False, str(dash["today"]))

        r = client.post("/admin/api/users/20230101/approve").json()
        check("批准成功", r["success"] and r["user"]["approvalState"] == "approved", str(r))

        dash = client.get("/admin/api/dashboard").json()
        check("批准后进入已批准列表", len(dash["users"]) == 1, str(dash["users"]))

        print("\n=== 7. 图形验证码 ===")
        cap = client.get("/api/captcha").json()
        check("验证码接口返回图片", cap.get("image", "").startswith("data:image/png;base64,"), str(cap)[:120])
        check("验证码携带 key", cap.get("captchaKey") == "fake-captcha-key", str(cap.get("captchaKey")))
        check("验证码是可解码的 base64",
              __import__("base64").b64decode(cap["image"].split(",", 1)[1])[:4] == b"\x89PNG",
              "解码失败")

        print("\n=== 8. 绑定学校账号 ===")
        r = client.post(
            "/api/bind",
            json={
                "studentId": "20230101",
                "password": "school-pw",
                "queryPassword": "my-secret",
            },
        ).json()
        check("绑定成功", r["success"] is True, str(r))
        check("回填了任务与宿舍", r["task"]["dormName"] == "林苑 3 栋", str(r.get("task")))
        check(
            "自动分配了窗口内签到时刻",
            "21:" <= r["user"]["planSignTime"] <= "22:30" if r["user"]["planSignTime"] else False,
            str(r["user"]["planSignTime"]),
        )

        overview = client.get("/api/overview").json()
        check("绑定后可直接查看概览", overview["success"] is True, str(overview))

        print("\n=== 9. 学号 + 查询口令 登录 ===")
        client.post("/api/logout")
        r = client.get("/api/me").json()
        check("退出后未登录", r["loggedIn"] is False)

        r = client.post(
            "/api/login", json={"studentId": "20230101", "queryPassword": "bad"}
        )
        check("错误口令被拒绝", r.status_code == 400, str(r.json()))

        r = client.post(
            "/api/login", json={"studentId": "20230101", "queryPassword": "my-secret"}
        ).json()
        check("正确口令登录成功", r["success"] is True, str(r))

        print("\n=== 10. 未配置学期时日历全部标记为无需签到 ===")
        today = dt.date.today()
        cal = client.get(f"/api/calendar?year={today.year}&month={today.month}").json()
        check("日历返回当月天数", len(cal["days"]) >= 28, str(len(cal["days"])))
        check("未配置学期时 planned=0", cal["summary"]["planned"] == 0, str(cal["summary"]))

        print("\n=== 11. 管理员配置校历 ===")
        first = today.replace(day=1)
        last = (first + dt.timedelta(days=32)).replace(day=1) - dt.timedelta(days=1)
        r = client.post(
            "/admin/api/terms",
            json={
                "name": "2025-2026-2 学期",
                "startDate": first.isoformat(),
                "endDate": last.isoformat(),
            },
        ).json()
        check("添加学期成功", r["success"] is True, str(r))

        r = client.post(
            "/admin/api/holidays",
            json={
                "name": "测试寒假",
                "kind": "vacation",
                "startDate": first.isoformat(),
                "endDate": (first + dt.timedelta(days=2)).isoformat(),
            },
        ).json()
        check("添加假期成功", r["success"] is True, str(r))

        r = client.post(
            "/admin/api/check-calendar", json={"date": today.isoformat()}
        ).json()
        check("今天应执行签到", r["shouldSign"] is True, str(r))

        terms = client.get("/admin/api/terms").json()["terms"]
        check("学期列表带区间天数", terms and terms[0].get("days") == (last - first).days + 1, str(terms))

        holidays = client.get("/admin/api/holidays").json()["holidays"]
        check("假期列表带区间天数", holidays and holidays[0].get("days") == 3, str(holidays))

        r = client.post(
            "/admin/api/check-calendar",
            json={"date": (first + dt.timedelta(days=1)).isoformat()},
        ).json()
        check("假期内不执行签到", r["shouldSign"] is False and r["holiday"], str(r))

        cal = client.get(f"/api/calendar?year={today.year}&month={today.month}").json()
        check("配置学期后 planned > 0", cal["summary"]["planned"] > 0, str(cal["summary"]))

        print("\n=== 12. 手动签到（走 stuSign 逻辑）===")
        r = client.post("/admin/api/users/20230101/sign").json()
        check("手动签到成功", r["success"] is True, str(r))

        overview = client.get("/api/overview").json()
        check(
            "当日记录为成功",
            (overview.get("today", {}).get("record") or {}).get("status") == "success",
            str(overview.get("today")),
        )

        print("\n=== 13. 全员自动签到 ===")
        r = client.post("/admin/api/run-daily", json={}).json()
        check("全员签到执行成功", r["success"] and r["total"] == 1, str(r))

        print("\n=== 14. 从服务端同步月记录 ===")
        r = client.get(f"/api/calendar?year={today.year}&month={today.month}&sync=1").json()
        check("同步后日历仍有数据", len(r["days"]) >= 28, str(len(r.get("days", []))))
        check("同步后 planned > 0", r["summary"]["planned"] > 0, str(r["summary"]))

        print("\n=== 15. 成员管理操作 ===")
        r = client.post(
            "/admin/api/users/20230101/enabled", json={"enabled": False}
        ).json()
        check("停用成功", r["user"]["enabled"] is False, str(r))

        r = client.post("/admin/api/randomize", json={}).json()
        check("重新随机时刻", r["success"] is True, str(r))

        r = client.post("/admin/api/users/20230101/reset-password").json()
        new_pw = r.get("password")
        check("重置口令返回新口令", bool(new_pw) and len(new_pw) == 6, str(r))

        r = client.post(
            "/api/login", json={"studentId": "20230101", "queryPassword": new_pw}
        ).json()
        check("新口令可登录", r["success"] is True, str(r))

        r = client.post(
            "/admin/api/users/20230101/enabled", json={"enabled": True}
        ).json()
        check("重新启用成功", r["user"]["enabled"] is True, str(r))

        print("\n=== 16. 登录失败限速与审计 ===")
        logs = client.get("/admin/api/logs?limit=200").json()["logs"]
        actions = {row["action"] for row in logs}
        check("记录了申请日志", "apply" in actions, str(sorted(actions)))
        check("记录了审批日志", "approve" in actions, str(sorted(actions)))
        check("记录了管理员登录失败", "admin_login_fail" in actions, str(sorted(actions)))
        check("记录了签到日志", "sign_ok" in actions, str(sorted(actions)))

        print("\n=== 17. 撤回与删除 ===")
        before = sum(
            1
            for row in client.get("/admin/api/logs?limit=500").json()["logs"]
            if row["action"] == "session_revoked"
        )
        r = client.post(
            "/admin/api/users/20230101/revoke", json={"reason": "测试撤回"}
        ).json()
        check("撤回成功", r["user"]["approvalState"] == "rejected", str(r))

        after = sum(
            1
            for row in client.get("/admin/api/logs?limit=500").json()["logs"]
            if row["action"] == "session_revoked"
        )
        check("撤回时注销了学校侧会话", after == before + 1, f"{before} -> {after}")

        r = client.post(
            "/api/login", json={"studentId": "20230101", "queryPassword": new_pw}
        )
        check("撤回后无法登录", r.status_code == 400, str(r.json()))

        r = client.request("DELETE", "/admin/api/users/20230101").json()
        check("删除成功", r["success"] is True, str(r))

        dash = client.get("/admin/api/dashboard").json()
        check("删除后成员为 0", dash["stats"]["totalUsers"] == 0, str(dash["stats"]))

        r = client.request("DELETE", "/admin/api/users/20230101")
        check("删除不存在的用户返回 400", r.status_code == 400, str(r.json()))

        print("\n=== 18. 静态页面与健康检查 ===")
        check("用户页可访问", client.get("/").status_code == 200)
        check("管理页可访问", client.get("/admin").status_code == 200)
        check("健康检查", client.get("/healthz").json()["ok"] is True)

        print("\n=== 19. 未授权访问管理接口 ===")
        client.post("/admin/api/logout")
        r = client.get("/admin/api/dashboard")
        check("未登录访问仪表盘被拒绝", r.status_code == 400, str(r.json()))

        # ------------------------------------------------------------------ #
        print("\n=== 20. token 模式（抓包录入的同学，没有密码）===")
        client.post("/admin/api/login", json={"username": "admin", "password": "admin-pass-123"})

        import base64 as _b64
        import json as _json

        def make_jwt(exp_offset_days: int) -> str:
            """构造 payload 含 exp 的假 JWT（本地只读 claims，不校验签名）。"""

            def seg(obj: dict) -> str:
                raw = _json.dumps(obj, separators=(",", ":")).encode()
                return _b64.urlsafe_b64encode(raw).decode().rstrip("=")

            exp = int((dt.datetime.now() + dt.timedelta(days=exp_offset_days)).timestamp())
            return f"{seg({'alg': 'HS256', 'typ': 'JWT'})}.{seg({'exp': exp, 'accountNo': '20239998'})}.sig"

        from app.flysource import token_days_left, token_is_expired

        fresh = make_jwt(10)
        stale = make_jwt(-1)
        check("JWT 未过期判断", token_is_expired(fresh) is False)
        check("JWT 剩余天数解析", 9.5 < (token_days_left(fresh) or 0) < 10.5, str(token_days_left(fresh)))
        check("JWT 已过期判断", token_is_expired(stale) is True)
        check("无 exp 不判过期", token_is_expired("a.b.c") is False)
        check("畸形 token 不判过期", token_is_expired("garbage") is False)

        r = client.post("/api/apply", json={"studentId": "20239998", "name": "王五"}).json()
        check("token 模式用户可创建", r["state"] == "submitted", str(r))
        client.post("/admin/api/users/20239998/approve")

        state_obj = application.state.app_state

        # 关键：只有 token，没有密码
        state_obj.db.update_user(
            "20239998",
            token_enc=state_obj.service.secrets.encrypt(fresh),
            credential_enc="",
            query_password_hash=hash_password("tok-pw"),
            credential_ok=1,
            trust_state="trusted",
            task_id="TASK-001",
        )
        check(
            "token 模式用户进入可签到列表",
            any(u["student_id"] == "20239998" for u in state_obj.db.list_signable_users()),
            "list_signable_users 应包含仅持 token 的用户",
        )

        outcome = state_obj.service.run_one("20239998", force=True)
        check("token 模式可签到（无需密码）", outcome.ok is True, f"{outcome.status} {outcome.message}")

        state_obj.db.update_user("20239998", token_enc=state_obj.service.secrets.encrypt(stale))
        outcome = state_obj.service.run_one("20239998", force=True)
        check(
            "过期 token 本地拦下并提示重新采集",
            outcome.ok is False and "过期" in outcome.message and "重新采集" in outcome.message,
            f"{outcome.status} {outcome.message}",
        )

        state_obj.db.update_user("20239998", token_enc="", credential_enc="")
        outcome = state_obj.service.run_one("20239998", force=True)
        check(
            "无凭据时给出明确错误",
            outcome.ok is False and "凭据" in outcome.message,
            f"{outcome.status} {outcome.message}",
        )

        # ------------------------------------------------------------------ #
        print("\n=== 21. 会话自动续期（refresh_token 滚动刷新）===")
        nearly_expired = make_jwt(2)  # 只剩 2 天，低于 5 天阈值 → 应触发续期
        state_obj.db.update_user(
            "20239998",
            token_enc=state_obj.service.secrets.encrypt(nearly_expired),
            refresh_token_enc=state_obj.service.secrets.encrypt("refresh-chain-0"),
            credential_enc="",
            query_password_hash=hash_password("tok-pw"),
            credential_ok=1,
            task_id="TASK-001",
        )

        before_calls = FakeClient.refresh_calls
        outcome = state_obj.service.run_one("20239998", force=True)
        check("临近过期的会话可自动续期并签到", outcome.ok is True, f"{outcome.status} {outcome.message}")
        check(
            "确实调用了续期接口",
            FakeClient.refresh_calls == before_calls + 1,
            f"{before_calls} -> {FakeClient.refresh_calls}",
        )

        stored = state_obj.db.get_user("20239998") or {}
        new_token = state_obj.service.secrets.decrypt(stored.get("token_enc") or "")
        new_refresh = state_obj.service.secrets.decrypt(stored.get("refresh_token_enc") or "")
        check("续期后 access_token 已更新", new_token == "refreshed-%d" % FakeClient.refresh_calls, new_token)
        check("续期后 refresh_token 已轮换", new_refresh == "refresh-%d" % FakeClient.refresh_calls, new_refresh)
        check("记录了续期时间", bool(stored.get("token_refreshed_at")), str(stored.get("token_refreshed_at")))

        logs = state_obj.db.recent_logs(20, "20239998")
        check(
            "续期成功写入日志",
            any(row["action"] == "token_refreshed" for row in logs),
            str([row["action"] for row in logs][:8]),
        )

        # 续期失败时应给出明确提示
        state_obj.db.update_user(
            "20239998",
            token_enc=state_obj.service.secrets.encrypt(make_jwt(-1)),  # 已过期
            refresh_token_enc=state_obj.service.secrets.encrypt("broken-refresh"),
            credential_enc="",
        )
        fresh_client_holder = {}

        def failing_factory():
            c = FakeClient()
            c.fail_refresh = True
            fresh_client_holder["c"] = c
            return c

        state_obj.service.new_token_client = failing_factory  # type: ignore[assignment]
        outcome = state_obj.service.run_one("20239998", force=True)
        check(
            "续期失败且已过期时提示重新采集",
            outcome.ok is False and "重新采集" in outcome.message,
            f"{outcome.status} {outcome.message}",
        )

        # ------------------------------------------------------------------
        print("\n=== 22. 重复提交不得抹掉已成功的签到 ===")
        # 真实事故：2026-10-08 21:11 签到成功，随后 21:12~21:16 的自动重试
        # 收到服务端「您今天已完成签到，请勿重复点击！」，把成功记录改写成了
        # 失败。这里锁死修复后的行为。
        import datetime as _dt

        from app.services import _is_already_done

        state_obj.db.update_user(
            "20239998",
            plan_sign_time="21:15",
            token_enc=state_obj.service.secrets.encrypt(make_jwt(30)),
            refresh_token_enc="",
            credential_enc="",
        )
        today = _dt.date.today().isoformat()

        # 1) 先制造一条「今天已签到成功」的记录
        state_obj.db.upsert_record(
            "20239998", today, status="success", message="签到成功",
            sign_status=1, sign_time="21:11:48", source="auto",
        )

        # 2) 「已完成签到」的提示必须被判定为幂等成功，而不是失败
        check(
            "「已完成签到」被识别为幂等成功",
            _is_already_done("您今天已完成签到，请勿重复点击！"),
            "关键词匹配",
        )
        check(
            "普通错误不被误判为已完成",
            not _is_already_done("签到失败，请重试！"),
            "关键词匹配",
        )

        # 3) 可重试失败不得覆盖成功记录
        state_obj.service._record_retryable(
            "20239998", today, "您今天已完成签到，请勿重复点击！",
            state_obj.db.get_user("20239998") or {}, source="auto",
        )
        kept = state_obj.db.get_record("20239998", today) or {}
        check(
            "重试失败后仍保留成功状态",
            kept.get("status") == "success",
            f"status={kept.get('status')} message={kept.get('message')}",
        )
        check(
            "保留了成功时的签到时刻",
            kept.get("sign_time") == "21:11:48",
            str(kept.get("sign_time")),
        )

        # 4) due_users 必须跳过今天已成功的用户（否则仍会白发请求）
        now = _dt.datetime.combine(_dt.date.today(), _dt.time(21, 16))
        due_ids = [u.get("student_id") for u in state_obj.service.due_users(now)]
        check(
            "due_users 跳过今天已签到的用户",
            "20239998" not in due_ids,
            f"due={due_ids}",
        )

        # 5) 尝试次数达上限后也不再纠缠
        state_obj.db.upsert_record(
            "20239998", today, status="failed", message="临时失败",
            source="auto", attempts=99,
        )
        due_ids = [u.get("student_id") for u in state_obj.service.due_users(now)]
        check(
            "due_users 在尝试次数达上限后跳过",
            "20239998" not in due_ids,
            f"due={due_ids}",
        )

        # ------------------------------------------------------------------
        print("\n=== 23. signStatus=0 不能被当成「未打卡」===")
        # 真实事故：服务端对已签到返回 signStatus=0 + signStatusName='正常'
        # + signTime 有值。只看状态码会把成功判成失败，导致日历上
        # 2026-10-07 / 10-08 明明签了却显示失败。
        from app.services import (
            _status_from_server,
            already_signed,
            skip_reason,
        )

        signed_rec = {
            "signStatus": 0,
            "signStatusName": "正常",
            "signTime": "21:11:48",
            "updateTime": "2026-10-08 21:11:48",
        }
        unsigned_rec = {"signStatus": 3, "signStatusName": "未签", "signTime": ""}
        leave_rec = {"signStatus": 5, "signStatusName": "离校", "signTime": ""}

        check(
            "signStatus=0 且有 signTime → 判定为已签到",
            already_signed(signed_rec),
            str(signed_rec),
        )
        check(
            "signStatus=3（未签）→ 判定为未签到",
            not already_signed(unsigned_rec),
            str(unsigned_rec),
        )
        check(
            "signStatus=5（离校）→ 视为已完成",
            already_signed(leave_rec),
            str(leave_rec),
        )

        st, msg = _status_from_server(0, _dt.date(2026, 10, 8), _dt.date(2026, 10, 9),
                                      signed_rec)
        check(
            "已签到的历史日期同步为 success",
            st == "success",
            f"{st} / {msg}",
        )
        st2, msg2 = _status_from_server(3, _dt.date(2026, 10, 9), _dt.date(2026, 10, 9),
                                        unsigned_rec)
        check(
            "今天未签 → 仍为待签到",
            st2 == "pending",
            f"{st2} / {msg2}",
        )
        st3, _ = _status_from_server(3, _dt.date(2026, 10, 1), _dt.date(2026, 10, 9),
                                     unsigned_rec)
        check(
            "过去某天确实没签 → failed",
            st3 == "failed",
            f"{st3}",
        )
        check(
            "已签到的说明文案带时刻",
            "21:11:48" in skip_reason(signed_rec),
            skip_reason(signed_rec),
        )

        # ------------------------------------------------------------------
        print("\n=== 24. 寒假留校不得被本地校历掐断 ===")
        # 真实场景：学校任务的打卡期间是 2026-09-05 ~ 2027-02-04（横跨寒假，
        # 寒假留校学生仍需打卡），而管理员本地配的学期是 2026-09-07 ~
        # 2027-01-15。早先版本把本地学期当硬门禁，2027-01-16 起会**静默
        # 漏签**。现在判定以学校任务期间为准，本地校历只作提示。
        from app.flysource import SignTask
        from app.services import SignInService

        task = SignTask(
            task_id="T1",
            task_name="【本科生】2026年秋季学期平安打卡",
            task_start_date="2026-09-05",
            task_end_date="2027-02-04",
            sign_week="星期一,星期二,星期三,星期四,星期五,星期六,星期日,",
            sign_start_time="21:00",
            sign_end_time="22:30",
            task_status=1,
            open_locate=0,
            open_take_photo=0,
        )

        # 任务期间覆盖的关键日期（本地学期都已结束）
        for label, day in (
            ("学期前 2026-09-05", _dt.date(2026, 9, 5)),
            ("学期末后 2027-01-16", _dt.date(2027, 1, 16)),
            ("寒假中 2027-01-25", _dt.date(2027, 1, 25)),
            ("任务末日 2027-02-04", _dt.date(2027, 2, 4)),
        ):
            ok, why = task.available_on(day)
            check(f"任务期间覆盖{label}", ok, why)

        # 任务期间之外
        ok, why = task.available_on(_dt.date(2027, 2, 5))
        check("超出任务期间则不打卡", not ok, why)
        ok, why = task.available_on(_dt.date(2026, 9, 4))
        check("早于任务期间则不打卡", not ok, why)

        # 停用的任务不打卡
        off = SignTask(task_id="T2", task_status=0)
        ok, why = off.available_on(_dt.date(2026, 10, 9))
        check("任务停用则不打卡", not ok, why)

        # signWeek 生效（只允许周一）
        monday_only = SignTask(
            task_id="T3", sign_week="星期一,",
            task_start_date="2026-09-01", task_end_date="2026-12-31",
        )
        check(
            "signWeek 含周一 → 周一可打卡",
            monday_only.available_on(_dt.date(2026, 10, 5))[0],  # 周一
            "2026-10-05 是周一",
        )
        check(
            "signWeek 不含周二 → 周二不打卡",
            not monday_only.available_on(_dt.date(2026, 10, 6))[0],
            "2026-10-06 是周二",
        )

        # 字段缺失时应宽松处理（不误拦）
        blank = SignTask(task_id="T4")
        check(
            "期间/星期字段缺失 → 不误拦",
            blank.available_on(_dt.date(2027, 1, 20))[0],
            "字段缺失时交由服务端判断",
        )

        # run_daily 不得因本地校历整天跳过
        code_src = (
            __import__("pathlib").Path(
                __import__("pathlib").Path(__file__).resolve().parents[1]
                / "app" / "services.py"
            ).read_text(encoding="utf-8")
        )
        check(
            "run_daily 不再因校历整天跳过",
            "daily_skip" not in code_src.split("def run_daily")[1].split("def ")[0],
            "run_daily 内应无 daily_skip 提前返回",
        )
        check(
            "sign_for_user 内不再有校历硬门禁",
            "return self._record(student_id, date_str, STATUS_SKIPPED, reason"
            not in code_src,
            "不应因 should_sign 为假直接 return",
        )

    print("\n" + "=" * 70)
    print(f"通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
    if FAILED:
        print("\n失败项：")
        for item in FAILED:
            print("  -", item)
    print("=" * 70)
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
