"""业务服务层：申请/审批、凭据绑定、签到执行、日历数据。"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

from .calendar_rules import CalendarRules
from .config import Settings
from .db import Database, parse_date, parse_time
from .flysource import (
    SIGN_STATUS_NAMES,
    WX_CLIENT_ID,
    WX_CLIENT_SECRET,
    AuthExpired,
    CaptchaRequired,
    FlySourceClient,
    FlySourceError,
    SignTask,
    TokenExpired,
    token_days_left,
    token_is_expired,
)
from .security import SecretBox

# 记录状态
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
STATUS_PENDING = "pending"
STATUS_SKIPPED = "skipped"
STATUS_NO_TASK = "no_task"

# 审批状态
APPROVAL_PENDING = "pending"
APPROVAL_APPROVED = "approved"
APPROVAL_REJECTED = "rejected"

# 签到状态码里代表"已经无需打卡"的
BENIGN_STATUS = {1, 2, 4, 5, 6}

# access_token 剩余天数低于此值就主动续期。
# 实测 refresh_token 支持滚动续期，所以阈值取大一些，避免临时失败导致过期。
TOKEN_REFRESH_THRESHOLD_DAYS = 5.0

# 随机分配签到时刻时，窗口首尾各留出的余量（分钟）。
# 不留余量的话可能被分到 22:29 这种窗口末尾 —— 一旦稍有延迟就被服务端拒绝。
SIGN_SAFETY_MARGIN_MINUTES = 10

# 单个用户每天最多尝试签到的次数。
# 服务端对重复提交会回「您今天已完成签到，请勿重复点击！」，
# 无节制重试既没用，又会把已经成功的记录覆盖成失败。
MAX_SIGN_ATTEMPTS_PER_DAY = 3

# 服务端这些提示**语义上等于成功**（幂等），不能当成失败。
# 否则重复提交会把「已签到」改写成「签到失败」，污染日历看板。
ALREADY_DONE_HINTS = (
    "已完成签到",
    "请勿重复",
    "已经签到",
    "重复打卡",
    "已打卡",
)


def _is_already_done(message: str) -> bool:
    """服务端返回是否表示「今天已经签过了」。"""
    return any(hint in message for hint in ALREADY_DONE_HINTS)


# signStatusName 里出现这些词，说明当天已经完成（正常/已签到）
SIGNED_STATUS_NAMES = ("正常", "已签", "已打卡", "已完成", "签到成功")


def already_signed(record: dict[str, Any] | None) -> bool:
    """判断服务端记录是否表示「今天已经签到完成」。

    **不能只看 signStatus** —— 实测 ``signStatus=0`` 配 ``signStatusName='正常'``
    且 ``signTime`` 有值才是已签到；而 ``signStatus=3``（未签）才是没签。
    早先按状态码判断，把成功当成了失败。

    判定顺序：

    1. ``signTime`` 有值 → 已签到（最可靠，是签到动作留下的时间戳）
    2. ``signStatusName`` 含"正常/已签/已打卡" → 已签到
    3. 状态码在免打卡集合里（离校/请假/走读/外宿）→ 无需打卡，也算完成
    """
    if not record:
        return False
    if str(record.get("signTime") or "").strip():
        return True
    name = str(record.get("signStatusName") or "").strip()
    if name and any(hint in name for hint in SIGNED_STATUS_NAMES):
        return True
    status_code = _int_or_none(record.get("signStatus"))
    return status_code in BENIGN_STATUS


def skip_reason(record: dict[str, Any] | None) -> str:
    """已签到时给出的说明文案。"""
    if not record:
        return "已完成"
    name = str(record.get("signStatusName") or "").strip()
    moment = str(record.get("signTime") or "").strip()
    if name and moment:
        return f"今日已签到（{name} {moment}）"
    if name:
        return f"无需重复打卡（{name}）"
    if moment:
        return f"今日已签到（{moment}）"
    return "无需重复打卡"


class ServiceError(Exception):
    """带用户可读信息的业务异常。"""


@dataclass
class SignOutcome:
    ok: bool
    status: str
    message: str
    sign_status: int | None = None


class SignInService:
    """所有业务动作的入口。"""

    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self.calendar = CalendarRules(db)
        self.secrets = SecretBox(settings.encryption_key)

    # ------------------------------------------------------------------ #
    # 客户端工厂
    # ------------------------------------------------------------------ #

    def new_client(self) -> FlySourceClient:
        """网页端凭据的客户端（可用于账号密码登录）。"""
        return FlySourceClient(
            self.settings.flysource_base_url,
            tenant_id=self.settings.tenant_id,
            timeout=self.settings.request_timeout,
            verify_tls=self.settings.verify_tls,
        )

    def new_token_client(self) -> FlySourceClient:
        """小程序端凭据的客户端（token 由抓包录入的用户必须用它）。"""
        return FlySourceClient(
            self.settings.flysource_base_url,
            tenant_id=self.settings.tenant_id,
            client_id=WX_CLIENT_ID,
            client_secret=WX_CLIENT_SECRET,
            timeout=self.settings.request_timeout,
            verify_tls=self.settings.verify_tls,
        )

    def _authenticate(
        self, client: FlySourceClient, user: dict[str, Any], student_id: str
    ) -> str:
        """让客户端取得可用会话。

        三种凭据模式，按优先级：

        * **token + refresh_token**：access 快过期时先自动续期，再使用。
        * **token 模式**（抓包录入）：直接用存下来的 access_token。
        * **密码模式**（网页绑定）：用账号密码换新 token。

        返回实际使用的模式名，便于记录日志。
        """
        token = self.secrets.decrypt(user.get("token_enc") or "")
        refresh = self.secrets.decrypt(user.get("refresh_token_enc") or "")
        password = self.secrets.decrypt(user.get("credential_enc") or "")

        # 优先尝试自动续期（有 refresh_token 且 access 即将过期时）
        if token and refresh:
            days = token_days_left(token)
            if days is None or days < TOKEN_REFRESH_THRESHOLD_DAYS:
                if self._renew_token(client, user, student_id, refresh):
                    return "refresh"
                # 续期失败则继续走下面的兜底逻辑
                token = self.secrets.decrypt(
                    (self.db.get_user(student_id) or {}).get("token_enc") or ""
                )

        if token and not token_is_expired(token):
            client.access_token = token
            return "token"

        if token and token_is_expired(token):
            days = token_days_left(token)
            if not password:
                raise TokenExpired(
                    "登录凭据已过期"
                    + (f"（{abs(days):.0f} 天前到期）" if days is not None else "")
                    + (
                        "，且续期失败，需要重新采集"
                        if refresh
                        else "，需要重新采集"
                    )
                )

        if password:
            client.login(student_id, password)
            self.db.update_user(
                student_id,
                token_enc=self.secrets.encrypt(client.access_token),
                token_saved_at=dt.datetime.now().replace(microsecond=0).isoformat(sep=" "),
            )
            return "password"

        raise FlySourceError("该用户没有可用凭据，请先完成授权采集或账号绑定")

    def _renew_token(
        self,
        client: FlySourceClient,
        user: dict[str, Any],
        student_id: str,
        refresh_token: str,
    ) -> bool:
        """用 refresh_token 续期；成功则把新 token 落库（含轮换后的 refresh）。"""
        try:
            result = client.refresh_access_token(refresh_token)
        except FlySourceError as exc:
            self.db.log(
                "token_refresh_failed",
                f"{student_id} 会话续期失败：{exc.message}",
                student_id=student_id,
                level="warn",
            )
            return False

        stamp = dt.datetime.now().replace(microsecond=0).isoformat(sep=" ")
        self.db.update_user(
            student_id,
            token_enc=self.secrets.encrypt(result.access_token),
            refresh_token_enc=self.secrets.encrypt(result.refresh_token),
            token_saved_at=stamp,
            token_refreshed_at=stamp,
            credential_ok=1,
            last_error="",
        )
        self.db.log(
            "token_refreshed",
            f"{student_id} 会话自动续期成功（剩余 {token_days_left(result.access_token):.1f} 天）"
            if token_days_left(result.access_token) is not None
            else f"{student_id} 会话自动续期成功",
            student_id=student_id,
        )
        return True

    # ------------------------------------------------------------------ #
    # 用户申请
    # ------------------------------------------------------------------ #

    def submit_application(
        self,
        student_id: str,
        *,
        name: str = "",
        phone: str = "",
        remark: str = "",
        major: str = "",
    ) -> dict[str, Any]:
        """提交使用申请（首次使用）。"""
        student_id = student_id.strip()
        if not student_id:
            raise ServiceError("学号不能为空")

        existing = self.db.get_user(student_id)
        if existing:
            state = existing.get("approval_state")
            if state == APPROVAL_APPROVED:
                return {"state": "already_approved", "user": _public_user(existing)}
            if state == APPROVAL_PENDING:
                return {"state": "already_pending", "user": _public_user(existing)}
            # 曾被拒绝/撤回：允许重新申请，覆盖资料
            self.db.update_user(
                student_id,
                approval_state=APPROVAL_PENDING,
                reject_reason="",
                name=name or existing.get("name") or "",
                phone=phone or existing.get("phone") or "",
                remark=remark or existing.get("remark") or "",
                major=major or existing.get("major") or "",
            )
            self.db.log("apply", f"{student_id} 重新提交申请", student_id=student_id)
            return {"state": "resubmitted", "user": _public_user(self.db.get_user(student_id) or {})}

        self.db.create_user(
            student_id,
            name=name.strip(),
            phone=phone.strip(),
            remark=remark.strip(),
            major=major.strip(),
            approval_state=APPROVAL_PENDING,
            trust_state="unbound",
        )
        self.db.log("apply", f"{student_id} 提交使用申请", student_id=student_id)
        return {"state": "submitted", "user": _public_user(self.db.get_user(student_id) or {})}

    def application_status(self, student_id: str) -> dict[str, Any]:
        user = self.db.get_user(student_id)
        if not user:
            return {"state": "not_found"}
        return {"state": user["approval_state"], "user": _public_user(user)}

    # ------------------------------------------------------------------ #
    # 审批（管理员）
    # ------------------------------------------------------------------ #

    def approve(self, student_id: str, admin: str) -> dict[str, Any]:
        user = self.db.get_user(student_id)
        if not user:
            raise ServiceError("用户不存在")
        self.db.update_user(
            student_id,
            approval_state=APPROVAL_APPROVED,
            reject_reason="",
            approved_at=dt.datetime.now().replace(microsecond=0).isoformat(sep=" "),
            approved_by=admin,
            enabled=1,
        )
        self.db.log("approve", f"管理员 {admin} 批准了 {student_id}", student_id=student_id)
        return _public_user(self.db.get_user(student_id) or {})

    def reject(self, student_id: str, admin: str, reason: str = "") -> dict[str, Any]:
        user = self.db.get_user(student_id)
        if not user:
            raise ServiceError("用户不存在")
        self.db.update_user(
            student_id,
            approval_state=APPROVAL_REJECTED,
            reject_reason=reason.strip(),
            enabled=0,
        )
        self.db.log(
            "reject",
            f"管理员 {admin} 拒绝了 {student_id}：{reason or '未填写原因'}",
            student_id=student_id,
            level="warn",
        )
        return _public_user(self.db.get_user(student_id) or {})

    def revoke(self, student_id: str, admin: str, reason: str = "") -> dict[str, Any]:
        """撤回已批准的用户（注销其学校侧会话并停用）。"""
        user = self.db.get_user(student_id)
        if not user:
            raise ServiceError("用户不存在")
        self._invalidate_school_session(user, student_id)
        self.db.update_user(
            student_id,
            approval_state=APPROVAL_REJECTED,
            reject_reason=reason.strip() or "管理员撤回授权",
            enabled=0,
        )
        self.db.log(
            "revoke",
            f"管理员 {admin} 撤回了 {student_id} 的授权",
            student_id=student_id,
            level="warn",
        )
        return _public_user(self.db.get_user(student_id) or {})

    def _invalidate_school_session(self, user: dict[str, Any], student_id: str) -> None:
        """尽力让学校侧的会话失效（停用/撤回用户时调用）。

        失败不影响本地状态：本地凭据被清掉后，调度器不会再为该用户签到。
        """
        password = self.secrets.decrypt(user.get("credential_enc") or "")
        if not password:
            return
        try:
            with self.new_client() as client:
                client.login(student_id, password)
                client.logout()
            self.db.log(
                "session_revoked",
                f"已注销 {student_id} 的学校侧会话",
                student_id=student_id,
            )
        except FlySourceError as exc:
            self.db.log(
                "session_revoke_failed",
                f"注销 {student_id} 的学校侧会话失败（不影响本地停用）：{exc.message}",
                student_id=student_id,
                level="warn",
            )

    def set_enabled(self, student_id: str, enabled: bool, admin: str) -> dict[str, Any]:
        user = self.db.get_user(student_id)
        if not user:
            raise ServiceError("用户不存在")
        if not enabled:
            self._invalidate_school_session(user, student_id)
        self.db.update_user(student_id, enabled=1 if enabled else 0)
        self.db.log(
            "toggle",
            f"管理员 {admin} {'启用' if enabled else '停用'}了 {student_id} 的自动签到",
            student_id=student_id,
        )
        return _public_user(self.db.get_user(student_id) or {})

    def delete_user(self, student_id: str, admin: str) -> None:
        """删除用户，并尽力注销学校侧会话后清除本地数据。"""
        user = self.db.get_user(student_id)
        if not user:
            raise ServiceError("用户不存在")
        self._invalidate_school_session(user, student_id)
        self.db.delete_user(student_id)
        self.db.log(
            "delete_user",
            f"管理员 {admin} 删除了 {student_id}",
            level="warn",
        )

    def update_user_profile(self, student_id: str, **fields: Any) -> dict[str, Any]:
        user = self.db.get_user(student_id)
        if not user:
            raise ServiceError("用户不存在")
        clean = {k: v for k, v in fields.items() if v is not None}
        if clean:
            self.db.update_user(student_id, **clean)
        return _public_user(self.db.get_user(student_id) or {})

    def reset_query_password(self, student_id: str, admin: str) -> str:
        """管理员重置用户的查询口令，返回新口令明文（仅此一次可见）。"""
        from .security import generate_password

        user = self.db.get_user(student_id)
        if not user:
            raise ServiceError("用户不存在")
        password = generate_password()
        from .security import hash_password

        self.db.update_user(
            student_id,
            query_password_hash=hash_password(password),
            trust_state="trusted" if user.get("credential_ok") else "unbound",
        )
        self.db.log(
            "reset_password",
            f"管理员 {admin} 重置了 {student_id} 的查询口令",
            student_id=student_id,
        )
        return password

    # ------------------------------------------------------------------ #
    # 用户首次绑定
    # ------------------------------------------------------------------ #

    def get_captcha(self) -> dict[str, Any]:
        """为绑定流程获取图形验证码。"""
        with self.new_client() as client:
            key, image = client.get_captcha()
        import base64

        return {
            "captchaKey": key,
            "image": "data:image/png;base64," + base64.b64encode(image).decode("ascii"),
        }

    def bind_credentials(
        self,
        student_id: str,
        password: str,
        query_password: str,
        *,
        captcha_key: str = "",
        captcha_code: str = "",
        plan_sign_time: str = "",
        task_id: str = "",
    ) -> dict[str, Any]:
        """首次使用：验证学校账号密码并绑定。

        成功后写入凭据与查询口令，并返回可用的查询会话。
        """
        student_id = student_id.strip()
        if not student_id:
            raise ServiceError("学号不能为空")
        if not password:
            raise ServiceError("学校打卡账号密码不能为空")
        if len(query_password) < 4:
            raise ServiceError("查询口令至少 4 位")

        user = self.db.get_user(student_id)
        if not user:
            raise ServiceError("请先提交使用申请，等待管理员批准")
        if user["approval_state"] == APPROVAL_PENDING:
            raise ServiceError("申请正在等待管理员批准，批准后即可绑定")
        if user["approval_state"] != APPROVAL_APPROVED:
            raise ServiceError("申请未被批准，无法绑定")

        if not task_id:
            task_id = user.get("task_id") or ""

        task = self._verify_credentials(
            student_id, password, captcha_key=captcha_key, captcha_code=captcha_code
        )

        from .security import hash_password

        self.db.update_user(
            student_id,
            credential_enc=self.secrets.encrypt(password),
            credential_ok=1,
            query_password_hash=hash_password(query_password),
            trust_state="trusted",
            last_error="",
            task_id=task.task_id,
            task_name=task.task_name,
            dorm_name=task.dorm_name,
            room_no=task.room_no,
            location_lat=task.location_lat,
            location_lng=task.location_lng,
            location_accuracy=task.location_accuracy or self.settings.location_accuracy,
        )
        if plan_sign_time:
            self.set_plan_sign_time(student_id, plan_sign_time)
        elif not user.get("plan_sign_time"):
            self.set_plan_sign_time(student_id, self._default_plan_time())

        self.db.log(
            "bind",
            f"{student_id} 绑定成功（任务 {task.task_name or task.task_id}，宿舍 {task.dorm_name} {task.room_no}）",
            student_id=student_id,
        )
        return {
            "user": _public_user(self.db.get_user(student_id) or {}),
            "task": task.summary(),
        }

    def _verify_credentials(
        self,
        student_id: str,
        password: str,
        *,
        captcha_key: str = "",
        captcha_code: str = "",
    ) -> SignTask:
        """登录并取回默认打卡任务，用于校验账号是否可用。"""
        with self.new_client() as client:
            try:
                client.login_auto(
                    student_id,
                    password,
                    captcha_key=captcha_key,
                    captcha_code=captcha_code,
                )
            except CaptchaRequired:
                raise ServiceError("需要图形验证码，请刷新验证码后重试") from None
            except AuthExpired:
                raise ServiceError("登录状态异常，请重试") from None
            except FlySourceError as exc:
                raise ServiceError(f"学校账号登录失败：{exc.message}") from exc

            try:
                tasks = client.list_tasks()
            except FlySourceError as exc:
                raise ServiceError(f"获取打卡任务失败：{exc.message}") from exc

            if not tasks:
                raise ServiceError("未找到打卡任务，请确认学号是否正确或稍后再试")

            task = tasks[0]
            try:
                detail = client.get_task(task.task_id)
                if detail.task_id:
                    task = detail
            except FlySourceError:
                pass
            return task

    def set_plan_sign_time(self, student_id: str, plan_sign_time: str) -> str:
        """设置每日计划签到时刻（HH:MM）。"""
        moment = parse_time(plan_sign_time)
        if moment is None:
            raise ServiceError("计划签到时刻格式应为 HH:MM")
        start = parse_time(self.settings.sign_window_start) or dt.time(21, 0)
        end = parse_time(self.settings.sign_window_end) or dt.time(22, 30)
        if not (start <= moment <= end):
            raise ServiceError(
                f"计划签到时刻需在 {start.strftime('%H:%M')} - {end.strftime('%H:%M')} 之间"
            )
        text = moment.strftime("%H:%M")
        self.db.update_user(student_id, plan_sign_time=text)
        return text

    def _default_plan_time(self) -> str:
        """在签到窗口内随机取一个分钟粒度的时刻。"""
        start = parse_time(self.settings.sign_window_start) or dt.time(21, 0)
        end = parse_time(self.settings.sign_window_end) or dt.time(22, 30)
        start_s = start.hour * 3600 + start.minute * 60
        end_s = end.hour * 3600 + end.minute * 60
        if end_s <= start_s:
            end_s = start_s + 60

        import random

        offset = random.SystemRandom().randrange(start_s, end_s + 1)
        offset -= offset % 60
        return f"{offset // 3600:02d}:{(offset % 3600) // 60:02d}"

    def randomize_plan_times(self, student_ids: list[str] | None = None) -> int:
        """为（全部或指定）用户重新随机分配窗口内的签到时刻。

        分配区间会**向内收缩** ``SIGN_SAFETY_MARGIN_MINUTES`` 分钟，
        避免把时刻分到窗口首尾 —— 否则一旦超时重试就没有余量，
        或者刚好卡在窗口末尾被服务端拒绝。
        """
        users = self.db.list_signable_users()
        if student_ids:
            wanted = set(student_ids)
            users = [u for u in users if u["student_id"] in wanted]

        start = parse_time(self.settings.sign_window_start) or dt.time(21, 0)
        end = parse_time(self.settings.sign_window_end) or dt.time(22, 30)
        start_s = start.hour * 3600 + start.minute * 60
        end_s = end.hour * 3600 + end.minute * 60
        if end_s <= start_s:
            end_s = start_s + 60

        # 向内收缩，留出重试余量
        margin = SIGN_SAFETY_MARGIN_MINUTES * 60
        safe_start = start_s + margin
        safe_end = end_s - margin
        if safe_end <= safe_start:
            # 窗口太窄，退回不收缩
            safe_start, safe_end = start_s, end_s

        import random

        rng = random.SystemRandom()
        count = 0
        for user in users:
            offset = rng.randrange(safe_start, safe_end + 1)
            # 分钟粒度，避免整点扎堆
            offset -= offset % 60
            text = f"{offset // 3600:02d}:{(offset % 3600) // 60:02d}"
            self.db.update_user(user["student_id"], plan_sign_time=text)
            count += 1

        if count:
            lo = f"{safe_start // 3600:02d}:{(safe_start % 3600) // 60:02d}"
            hi = f"{safe_end // 3600:02d}:{(safe_end % 3600) // 60:02d}"
            self.db.log(
                "randomize",
                f"已为 {count} 位用户在 {lo}-{hi} 内重新随机分配签到时刻"
                f"（窗口 {self.settings.sign_window_start}-{self.settings.sign_window_end}，"
                f"首尾各留 {SIGN_SAFETY_MARGIN_MINUTES} 分钟余量）",
            )
        return count

    # ------------------------------------------------------------------ #
    # 用户查询
    # ------------------------------------------------------------------ #

    def user_overview(self, student_id: str) -> dict[str, Any]:
        user = self.db.get_user(student_id)
        if not user:
            raise ServiceError("未找到该学号")
        today = dt.date.today()
        should, reason = self.calendar.should_sign(today)
        record = self.db.get_record(student_id, today.isoformat())
        return {
            "user": _public_user(user),
            "today": {
                "date": today.isoformat(),
                "shouldSign": should,
                "reason": reason,
                "record": _public_record(record),
            },
            "planSignTime": user.get("plan_sign_time") or "",
            "task": {
                "taskId": user.get("task_id") or "",
                "taskName": user.get("task_name") or "",
                "dormName": user.get("dorm_name") or "",
                "roomNo": user.get("room_no") or "",
            },
        }

    def calendar_payload(self, student_id: str, year: int, month: int) -> dict[str, Any]:
        """日历看板数据：本地记录 + 校历判定 + 服务端状态。"""
        user = self.db.get_user(student_id)
        if not user:
            raise ServiceError("未找到该学号")

        from .db import month_bounds

        start, end = month_bounds(year, month)
        records = self.db.record_map(student_id, start, end)
        rules = self.calendar.describe_month(year, month)

        days: list[dict[str, Any]] = []
        cursor = start
        today = dt.date.today()
        while cursor <= end:
            key = cursor.isoformat()
            rec = records.get(key)
            should = rules.get(key) == "sign"
            if rec:
                status = rec.get("status")
                message = rec.get("message") or ""
                sign_status = rec.get("sign_status")
            elif cursor > today:
                status, message, sign_status = "future", "", None
            elif not should:
                status, message, sign_status = "skipped", "非签到日（假期/学期外）", None
            else:
                status, message, sign_status = "unknown", "暂无记录", None

            days.append(
                {
                    "date": key,
                    "day": cursor.day,
                    "weekday": cursor.weekday(),
                    "isToday": cursor == today,
                    "shouldSign": should,
                    "status": status,
                    "statusName": _status_name(status, sign_status),
                    "message": message,
                    "signStatus": sign_status,
                    "signTime": rec.get("sign_time") if rec else "",
                }
            )
            cursor += dt.timedelta(days=1)

        summary = {
            "success": sum(1 for d in days if d["status"] == STATUS_SUCCESS),
            "failed": sum(1 for d in days if d["status"] == STATUS_FAILED),
            "skipped": sum(1 for d in days if d["status"] == STATUS_SKIPPED),
            "planned": sum(1 for d in days if d["shouldSign"]),
            "unknown": sum(1 for d in days if d["status"] == "unknown"),
        }
        return {
            "year": year,
            "month": month,
            "days": days,
            "summary": summary,
            "terms": [_term_brief(t) for t in self.db.list_terms()],
            "holidays": [_holiday_brief(h) for h in self.db.list_holidays()],
        }

    def sync_month_from_server(self, student_id: str, year: int, month: int) -> int:
        """从学校服务端拉取某月记录并写入本地（日历的权威数据源）。"""
        user = self.db.get_user(student_id)
        if not user:
            raise ServiceError("未找到该学号")
        password = self.secrets.decrypt(user.get("credential_enc") or "")
        if not password:
            raise ServiceError("尚未绑定学校账号，无法同步")

        month_str = f"{year:04d}-{month:02d}"
        with self.new_client() as client:
            client.login(student_id, password)
            task_id = user.get("task_id") or ""
            if not task_id:
                tasks = client.list_tasks()
                if not tasks:
                    raise ServiceError("未找到打卡任务")
                task_id = tasks[0].task_id
                self.db.update_user(student_id, task_id=task_id)
            data = client.month_records(task_id, month_str)

        today = dt.date.today()
        count = 0
        for date_str, rec in data.items():
            day = parse_date(date_str)
            if day is None or not isinstance(rec, dict):
                continue
            raw_status = rec.get("signStatus")
            sign_status = _int_or_none(raw_status)
            status, message = _status_from_server(sign_status, day, today, rec)
            self.db.upsert_record(
                student_id,
                day.isoformat(),
                status=status,
                message=message,
                sign_status=sign_status,
                sign_time=str(rec.get("signTime") or rec.get("signInTime") or ""),
                source="server",
                attempts=1,
            )
            count += 1
        self.db.log(
            "sync",
            f"{student_id} 同步 {month_str} 记录 {count} 条",
            student_id=student_id,
        )
        return count

    # ------------------------------------------------------------------ #
    # 签到执行
    # ------------------------------------------------------------------ #

    def sign_for_user(
        self,
        user: dict[str, Any],
        *,
        day: dt.date | None = None,
        force: bool = False,
        source: str = "auto",
    ) -> SignOutcome:
        """为单个用户执行一次签到。"""
        student_id = str(user.get("student_id") or "")
        target = day or dt.date.today()
        date_str = target.isoformat()

        if not force:
            if user.get("approval_state") != APPROVAL_APPROVED:
                return self._record(student_id, date_str, STATUS_SKIPPED, "未获批准", source=source)
            if not int(user.get("enabled") or 0):
                return self._record(student_id, date_str, STATUS_SKIPPED, "已被管理员停用", source=source)

            # 注意：这里**不再**用本地校历做硬门禁。
            #
            # 学校任务的 taskStartDate/taskEndDate 才是权威的打卡期间，而且
            # 会覆盖寒暑假（实测 2026-09-05 ~ 2027-02-04 横跨整个寒假，
            # 寒假留校学生仍需打卡）。早先版本把本地学期当门禁，学期区间
            # 一旦比学校任务短，那段时间就会**静默漏签**。
            #
            # 本地校历现在只用于日历着色与展示，判定交给学校任务。
            should, reason = self.calendar.should_sign(target)
            if not should:
                self.db.log(
                    "calendar_note",
                    f"{date_str} 本地校历判定为「{reason}」，"
                    f"但仍按学校任务期间判定（避免漏签）",
                    student_id=student_id,
                )

        try:
            with self.new_token_client() as client:
                mode = self._authenticate(client, user, student_id)
                if mode == "token":
                    self.db.log(
                        "auth_token",
                        f"{student_id} 使用已保存的会话签到",
                        student_id=student_id,
                    )
                task = self._resolve_task(client, user, target)
                if task is None:
                    return self._record(
                        student_id, date_str, STATUS_NO_TASK, "未找到打卡任务", source=source
                    )

                # 以学校任务期间为准判定今天要不要打卡。
                # （taskStartDate ~ taskEndDate + signWeek + 每日时段）
                available, why = task.available_on(target)
                if not available:
                    return self._record(
                        student_id, date_str, STATUS_SKIPPED, why, source=source
                    )
                self.db.update_user(
                    student_id,
                    task_id=task.task_id,
                    task_name=task.task_name,
                )

                # 已签到就不再重复提交。
                # 注意：必须用 already_signed() 判断，不能只看 signStatus ——
                # signStatus=0 表示「正常」，signTime 有值才是真的签过了。
                try:
                    existing = client.today_record(task.task_id)
                except FlySourceError:
                    existing = {}
                if already_signed(existing):
                    status_code = _int_or_none(existing.get("signStatus"))
                    return self._record(
                        student_id,
                        date_str,
                        STATUS_SUCCESS,
                        skip_reason(existing),
                        sign_status=status_code,
                        sign_time=str(existing.get("signTime") or ""),
                        source=source,
                    )

                if task.open_locate == 1:
                    # 签到坐标 = 学校自己登记的宿舍定位基准点。
                    # 用它自身作为签到点，到基准点的距离为 0 米，
                    # 必然通过服务端的定位校验 —— 无需伪造 GPS。
                    lat, lng = task.location_lat, task.location_lng
                    if lat is None or lng is None:
                        # 本次任务详情没带坐标时，退回上一次成功使用过的坐标
                        stored_lat = _to_float(user.get("location_lat"))
                        stored_lng = _to_float(user.get("location_lng"))
                        if stored_lat is not None and stored_lng is not None:
                            lat, lng = stored_lat, stored_lng
                            self.db.log(
                                "coord_fallback",
                                f"{student_id} 本次任务未返回坐标，改用上次记录的宿舍基准点",
                                student_id=student_id,
                                level="warn",
                            )
                    if lat is None or lng is None:
                        return self._record(
                            student_id,
                            date_str,
                            STATUS_FAILED,
                            "任务要求定位，但学校未配置该同学的宿舍基准坐标"
                            "（请在管理端检查其宿舍信息）",
                            source=source,
                        )
                else:
                    # 不校验定位时，小程序上传 0/0
                    lat, lng = 0.0, 0.0

                if task.needs_photo:
                    return self._record(
                        student_id,
                        date_str,
                        STATUS_FAILED,
                        "该任务要求现场拍照，网页端无法完成，请手动在小程序打卡",
                        source=source,
                    )

                result = client.sign(
                    task,
                    latitude=lat,
                    longitude=lng,
                    sign_date=date_str,
                    late=False,
                )

                # 复查一次，确认服务端确实记录了
                verified = False
                verify_status: int | None = None
                verify_time = ""
                try:
                    after = client.today_record(task.task_id)
                    verify_status = _int_or_none(after.get("signStatus"))
                    verify_time = str(after.get("signTime") or "")
                    verified = already_signed(after)
                except FlySourceError:
                    pass

                # 复查成功，或服务端明确返回 success —— 都算签到成功
                if verified or bool(result.get("success", True)):
                    self.db.update_user(
                        student_id,
                        task_id=task.task_id,
                        task_name=task.task_name,
                        dorm_name=task.dorm_name,
                        room_no=task.room_no,
                        location_lat=task.location_lat,
                        location_lng=task.location_lng,
                        location_accuracy=task.location_accuracy or self.settings.location_accuracy,
                        last_error="",
                    )
                    return self._record(
                        student_id,
                        date_str,
                        STATUS_SUCCESS,
                        "签到成功",
                        sign_status=verify_status,
                        sign_time=verify_time or dt.datetime.now().strftime("%H:%M:%S"),
                        source=source,
                    )

                message = str(result.get("msg") or "服务端未确认签到结果")
                if _is_already_done(message):
                    # 「已完成签到」是幂等成功，不是失败 —— 别把日历上的
                    # 已签到改写成签到失败
                    self.db.log(
                        "sign_ok",
                        f"{student_id} 今天已签到（{message}）",
                        student_id=student_id,
                    )
                    return self._record(
                        student_id,
                        date_str,
                        STATUS_SUCCESS,
                        message,
                        sign_status=verify_status,
                        source=source,
                    )
                return self._record(
                    student_id, date_str, STATUS_FAILED, message, source=source
                )

        except TokenExpired as exc:
            return self._record(
                student_id, date_str, STATUS_FAILED, exc.message, source=source
            )
        except CaptchaRequired:
            return self._record_retryable(
                student_id, date_str, "学校要求图形验证码，请在网页重新登录", user, source=source
            )
        except AuthExpired:
            return self._record_retryable(
                student_id, date_str, "登录已过期，将在下次自动重试", user, source=source
            )
        except FlySourceError as exc:
            return self._record_retryable(student_id, date_str, exc.message, user, source=source)
        except Exception as exc:  # noqa: BLE001 - 调度任务不能因单点异常中断
            return self._record_retryable(
                student_id, date_str, f"未预期错误：{exc}", user, source=source, level="error"
            )

    def _record_retryable(
        self,
        student_id: str,
        date_str: str,
        message: str,
        user: dict[str, Any],
        *,
        source: str,
        level: str = "warn",
    ) -> SignOutcome:
        self.db.update_user(student_id, last_error=message[:500])
        self.db.log("sign_fail", message, student_id=student_id, level=level)
        # 「可重试」意味着这是一次临时失败（网络抖动、会话过期、服务端
        # 暂时拒绝）。如果今天其实已经签到成功，就不该把它抹成失败 ——
        # 这正是 21:12~21:16 那批重复请求制造出来的假失败。
        return self._record(
            student_id,
            date_str,
            STATUS_FAILED,
            message,
            source=source,
            keep_success=True,
        )

    def _record(
        self,
        student_id: str,
        date_str: str,
        status: str,
        message: str,
        *,
        sign_status: int | None = None,
        sign_time: str = "",
        source: str = "auto",
        keep_success: bool = False,
    ) -> SignOutcome:
        # 已经签到成功的记录，不应被**重试类**失败改写 —— 否则日历看板
        # 会把「已签到」显示成「签到失败」。
        # 只对重试路径生效（keep_success=True）；其余失败照常覆盖。
        if status == STATUS_FAILED and keep_success:
            existing = self.db.get_record(student_id, date_str)
            if existing and str(existing.get("status")) == STATUS_SUCCESS:
                kept = str(existing.get("message") or "已签到")
                self.db.log(
                    "sign_keep",
                    f"{student_id} 今日已签到成功，保留成功记录（本次：{message}）",
                    student_id=student_id,
                )
                return SignOutcome(
                    ok=True,
                    status=STATUS_SUCCESS,
                    message=kept,
                    sign_status=_int_or_none(existing.get("sign_status")),
                )

        self.db.upsert_record(
            student_id,
            date_str,
            status=status,
            message=message,
            sign_status=sign_status,
            sign_time=sign_time,
            source=source,
        )
        if status == STATUS_SUCCESS:
            self.db.log("sign_ok", f"{student_id} {message}", student_id=student_id)
        return SignOutcome(ok=status == STATUS_SUCCESS, status=status, message=message, sign_status=sign_status)

    def _resolve_task(
        self,
        client: FlySourceClient,
        user: dict[str, Any],
        day: dt.date | None = None,
    ) -> SignTask | None:
        """挑出**今天适用**的打卡任务。

        学校可能同时存在多个任务（例如学期任务 + 寒假留校任务），
        因此不能简单地取第一个 —— 要选期间覆盖今天、且今天需要打卡的那个。

        选择顺序：

        1. 已保存的 task_id（若其期间仍覆盖今天）
        2. 任务列表里期间覆盖今天的（优先留校任务）
        3. 兜底：列表第一个
        """
        target = day or dt.date.today()

        def usable(task: SignTask) -> bool:
            return bool(task.task_id) and task.is_active and task.covers_date(target)

        task_id = str(user.get("task_id") or "")
        if task_id:
            try:
                task = client.get_task(task_id)
                if usable(task):
                    return task
            except FlySourceError:
                pass

        try:
            tasks = client.list_tasks()
        except FlySourceError:
            return None
        if not tasks:
            return None

        # 期间覆盖今天的任务里，优先「留校任务」—— 寒暑假期间生效的通常是它
        covering = [t for t in tasks if usable(t)]
        if covering:
            stay = [
                t for t in covering
                if str(t.is_stay_school_task or "") in ("1", "True", "true")
            ]
            return (stay or covering)[0]

        # 没有任何任务覆盖今天：返回第一个，让上层给出明确原因
        return tasks[0]

    # ------------------------------------------------------------------ #
    # 调度入口
    # ------------------------------------------------------------------ #

    def due_users(self, now: dt.datetime, window_minutes: int = 5) -> list[dict[str, Any]]:
        """选出此刻应当签到的用户。

        会跳过两类用户，避免无意义的重复请求：

        * **今天已经签到成功**的（服务端会拒绝重复提交，而且重试会把
          成功记录覆盖成失败）
        * 今天尝试次数已达上限的
        """
        limit = (now + dt.timedelta(minutes=window_minutes)).strftime("%H:%M")
        current = now.strftime("%H:%M")
        today = now.date().isoformat()
        out: list[dict[str, Any]] = []
        for user in self.db.list_signable_users():
            plan = str(user.get("plan_sign_time") or "")
            if plan and not (current <= plan <= limit):
                continue

            student_id = str(user.get("student_id") or "")
            record = self.db.get_record(student_id, today)
            if record:
                if str(record.get("status")) == STATUS_SUCCESS:
                    continue  # 今天已完成
                attempts = int(record.get("attempts") or 0)
                if attempts >= MAX_SIGN_ATTEMPTS_PER_DAY:
                    continue  # 今天试够了，不再纠缠
            out.append(user)
        return out

    def run_daily(self, day: dt.date | None = None, *, force: bool = False) -> dict[str, Any]:
        """对当天所有符合条件的用户执行签到（供调度器与手动触发）。

        注意：**不再因本地校历而整天跳过**。校历只作提示，是否要打卡由
        学校任务的期间决定（见 ``sign_for_user``）。否则学期区间一旦比
        学校任务短（例如寒假留校阶段），整天都会被静默跳过。
        """
        target = day or dt.date.today()
        should, reason = self.calendar.should_sign(target)
        if not should:
            self.db.log(
                "calendar_note",
                f"{target.isoformat()} 本地校历判定为「{reason}」，"
                f"仍按学校任务期间执行（避免寒假留校等场景漏签）",
            )

        users = self.db.list_signable_users()
        results = []
        for user in users:
            outcome = self.sign_for_user(user, day=target, force=force, source="auto")
            results.append(
                {
                    "studentId": user.get("student_id"),
                    "status": outcome.status,
                    "message": outcome.message,
                }
            )

        ok = sum(1 for r in results if r["status"] == STATUS_SUCCESS)
        self.db.log(
            "daily_run",
            f"{target.isoformat()} 自动签到完成：成功 {ok}/{len(results)}",
            detail=results,
        )
        return {
            "date": target.isoformat(),
            "skipped": False,
            "total": len(results),
            "success": ok,
            "results": results,
        }

    def run_one(self, student_id: str, *, force: bool = True) -> SignOutcome:
        user = self.db.get_user(student_id)
        if not user:
            raise ServiceError("未找到该学号")
        return self.sign_for_user(user, force=force, source="manual")

    def retry_failed(self, days: int = 3) -> dict[str, Any]:
        """重试最近若干天失败/挂起的记录。

        只重试**确实失败**的记录。不在本地校历区间内也照样重试 ——
        那时的失败可能正是漏签造成的，应当补上。
        """
        today = dt.date.today()
        results = []
        for offset in range(1, max(1, days) + 1):
            day = today - dt.timedelta(days=offset)
            rows = self.db.query(
                "SELECT student_id FROM sign_records WHERE sign_date = ? AND status = 'failed'",
                (day.isoformat(),),
            )
            for row in rows:
                user = self.db.get_user(str(row["student_id"]))
                if not user:
                    continue
                outcome = self.sign_for_user(user, day=day, force=True, source="retry")
                results.append(
                    {
                        "studentId": row["student_id"],
                        "date": day.isoformat(),
                        "status": outcome.status,
                        "message": outcome.message,
                    }
                )
        return {"retried": len(results), "results": results}


# --------------------------------------------------------------------------- #
# 序列化辅助
# --------------------------------------------------------------------------- #


def _public_user(user: dict[str, Any]) -> dict[str, Any]:
    if not user:
        return {}
    return {
        "studentId": user.get("student_id", ""),
        "name": user.get("name", ""),
        "phone": user.get("phone", ""),
        "major": user.get("major", ""),
        "remark": user.get("remark", ""),
        "approvalState": user.get("approval_state", ""),
        "rejectReason": user.get("reject_reason", ""),
        "approvedAt": user.get("approved_at") or "",
        "approvedBy": user.get("approved_by") or "",
        "enabled": bool(user.get("enabled")),
        "trustState": user.get("trust_state", "unbound"),
        "hasQueryPassword": bool(user.get("query_password_hash")),
        "credentialOk": bool(user.get("credential_ok")),
        "lastError": user.get("last_error") or "",
        "planSignTime": user.get("plan_sign_time") or "",
        "taskId": user.get("task_id") or "",
        "taskName": user.get("task_name") or "",
        "dormName": user.get("dorm_name") or "",
        "roomNo": user.get("room_no") or "",
        "createdAt": user.get("created_at") or "",
        "updatedAt": user.get("updated_at") or "",
    }


def _public_record(record: dict[str, Any] | None) -> dict[str, Any] | None:
    if not record:
        return None
    return {
        "date": record.get("sign_date"),
        "status": record.get("status"),
        "statusName": _status_name(str(record.get("status")), _int_or_none(record.get("sign_status"))),
        "message": record.get("message") or "",
        "signTime": record.get("sign_time") or "",
        "signStatus": record.get("sign_status"),
        "source": record.get("source") or "",
    }


def _status_name(status: str, sign_status: int | None) -> str:
    if status == STATUS_SUCCESS:
        if sign_status in (0, 1) or sign_status is None:
            return "已签到"
        if sign_status in SIGN_STATUS_NAMES:
            return SIGN_STATUS_NAMES[sign_status]
        return "已签到"
    if status == STATUS_FAILED:
        return "签到失败"
    if status == STATUS_PENDING:
        return "待签到"
    if status == STATUS_SKIPPED:
        return "无需签到"
    if status == STATUS_NO_TASK:
        return "无打卡任务"
    if status == "future":
        return "未到日期"
    return "暂无记录"


def _status_from_server(
    sign_status: int | None, day: dt.date, today: dt.date, rec: dict[str, Any]
) -> tuple[str, str]:
    """把服务端记录翻译成本地状态。

    判定依据依次为：``signTime`` 有值 → ``signStatusName`` → 状态码。
    实测 ``signStatus=0``（name='正常'）配 ``signTime`` 有值就是已签到，
    只看状态码会把成功判成失败。
    """
    if already_signed(rec):
        name = str(rec.get("signStatusName") or "").strip()
        moment = str(rec.get("signTime") or "").strip()
        label = name or SIGN_STATUS_NAMES.get(sign_status or 0, "已完成")
        if moment:
            return STATUS_SUCCESS, f"{label}（{moment}）"
        return STATUS_SUCCESS, label

    if day > today:
        return STATUS_PENDING, "未到日期"
    if day == today:
        return STATUS_PENDING, "待签到"
    # 过去的日子且没有签到痕迹
    name = str(rec.get("signStatusName") or "").strip()
    return STATUS_FAILED, name or "未签到"


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _term_brief(term: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": term.get("id"),
        "name": term.get("name"),
        "startDate": term.get("start_date"),
        "endDate": term.get("end_date"),
    }


def _holiday_brief(holiday: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": holiday.get("id"),
        "name": holiday.get("name"),
        "startDate": holiday.get("start_date"),
        "endDate": holiday.get("end_date"),
        "kind": holiday.get("kind"),
    }
