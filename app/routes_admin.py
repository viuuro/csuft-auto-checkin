"""管理员端路由。

需求约束：
* 有且仅有管理员自己的账号能登录管理端；
* 新用户的首次使用请求必须由管理员亲自批准后才能使用自动签到；
* 提供成员管理（启用/停用、删除、重置查询口令、编辑资料）；
* 提供校历（学期/假期）维护，用于跳过寒暑假。
"""

from __future__ import annotations

import datetime as dt
import time
from typing import Any

from fastapi import APIRouter, Body, Request, Response

from .calendar_rules import summarize_holidays, summarize_terms
from .constants import ADMIN_COOKIE, ADMIN_SESSION_TTL
from .db import parse_date
from .security import hash_password, mask_student_id, verify_password
from .services import ServiceError
from .flysource import token_days_left

router = APIRouter(prefix="/admin", tags=["admin"])

# 简易登录限速：最多 8 次失败 / 10 分钟
_MAX_LOGIN_FAILURES = 8
_LOGIN_WINDOW_SECONDS = 600
_failures: dict[str, list[float]] = {}


# --------------------------------------------------------------------------- #
# 辅助
# --------------------------------------------------------------------------- #


def _state(request: Request):
    return request.app.state.app_state


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _check_rate_limit(ip: str) -> None:
    now = time.time()
    stamps = [t for t in _failures.get(ip, []) if now - t < _LOGIN_WINDOW_SECONDS]
    _failures[ip] = stamps
    if len(stamps) >= _MAX_LOGIN_FAILURES:
        raise ServiceError("尝试次数过多，请 10 分钟后再试")


def _record_failure(ip: str) -> None:
    _failures.setdefault(ip, []).append(time.time())


def _current_admin(request: Request) -> str:
    state = _state(request)
    data = state.sessions.loads(request.cookies.get(ADMIN_COOKIE))
    if not data or data.get("kind") != "admin":
        return ""
    username = str(data.get("user") or "")
    settings = state.settings
    if not settings.is_admin_configured():
        return ""
    if username != settings.admin_username:
        return ""
    return username


def _require_admin(request: Request) -> str:
    admin = _current_admin(request)
    if not admin:
        raise ServiceError("管理员会话已过期，请重新登录")
    return admin


def _set_admin_cookie(response: Response, state, username: str) -> None:
    token = state.sessions.dumps({"kind": "admin", "user": username}, ADMIN_SESSION_TTL)
    response.set_cookie(
        ADMIN_COOKIE,
        token,
        max_age=ADMIN_SESSION_TTL,
        httponly=True,
        samesite="strict",
        path="/",
    )


# --------------------------------------------------------------------------- #
# 登录
# --------------------------------------------------------------------------- #


@router.get("/api/status")
def admin_status(request: Request) -> dict[str, Any]:
    state = _state(request)
    settings = state.settings
    admin = _current_admin(request)
    return {
        "success": True,
        "configured": settings.is_admin_configured(),
        "loggedIn": bool(admin),
        "username": admin,
        "siteName": settings.site_name,
    }


@router.post("/api/setup")
def admin_setup(
    request: Request, response: Response, payload: dict[str, Any] = Body(...)
) -> dict[str, Any]:
    """首次设置管理员账号（仅当尚未配置时可用）。

    如果 ``ADMIN_USERNAME``/``ADMIN_PASSWORD_HASH`` 已通过配置提供，此接口会被拒绝。
    """
    state = _state(request)
    settings = state.settings
    if settings.is_admin_configured():
        raise ServiceError("管理员账号已配置，无法重复设置")

    username = str(payload.get("username") or "").strip()
    password = str(payload.get("password") or "")
    if len(username) < 3:
        raise ServiceError("用户名至少 3 位")
    if len(password) < 8:
        raise ServiceError("密码至少 8 位")

    from .config import persist

    password_hash = hash_password(password)
    persist("admin_username", username)
    persist("admin_password_hash", password_hash)
    settings.admin_username = username
    settings.admin_password_hash = password_hash

    state.db.log("admin_setup", f"管理员账号已设置：{username}", level="warn")
    _set_admin_cookie(response, state, username)
    return {"success": True, "message": "管理员账号已创建"}


@router.post("/api/login")
def admin_login(
    request: Request, response: Response, payload: dict[str, Any] = Body(...)
) -> dict[str, Any]:
    state = _state(request)
    settings = state.settings
    ip = _client_ip(request)
    _check_rate_limit(ip)

    if not settings.is_admin_configured():
        raise ServiceError("管理员账号尚未配置")

    username = str(payload.get("username") or "").strip()
    password = str(payload.get("password") or "")

    ok = username == settings.admin_username and verify_password(
        password, settings.admin_password_hash
    )
    if not ok:
        _record_failure(ip)
        state.db.log(
            "admin_login_fail",
            f"管理员登录失败：{username or '(空)'}",
            level="warn",
            detail={"ip": ip},
        )
        raise ServiceError("用户名或密码错误")

    _failures.pop(ip, None)
    _set_admin_cookie(response, state, username)
    state.db.log("admin_login", f"管理员 {username} 登录成功", detail={"ip": ip})
    return {"success": True, "message": "登录成功", "username": username}


@router.post("/api/logout")
def admin_logout(response: Response) -> dict[str, Any]:
    response.delete_cookie(ADMIN_COOKIE, path="/admin")
    response.delete_cookie(ADMIN_COOKIE, path="/")
    return {"success": True}


# --------------------------------------------------------------------------- #
# 仪表盘
# --------------------------------------------------------------------------- #


@router.get("/api/dashboard")
def admin_dashboard(request: Request) -> dict[str, Any]:
    state = _state(request)
    _require_admin(request)
    today = dt.date.today()

    pending = state.db.list_users("pending")
    users = state.db.list_users("approved")
    should, reason = state.service.calendar.should_sign(today)

    return {
        "success": True,
        "stats": state.db.stats(today),
        "today": {
            "date": today.isoformat(),
            "shouldSign": should,
            "reason": reason,
        },
        "settings": state.settings.as_public_dict(),
        "scheduler": {
            "running": state.scheduler.running,
            "jobs": state.scheduler.jobs_status(),
        },
        "pending": [_user_row(u, state) for u in pending],
        "users": [_user_row(u, state) for u in users],
        "terms": summarize_terms(state.db),
        "holidays": summarize_holidays(state.db),
        "logs": state.db.recent_logs(60),
    }


# --------------------------------------------------------------------------- #
# 审批
# --------------------------------------------------------------------------- #


@router.get("/api/users")
def admin_users(request: Request, state_filter: str = "") -> dict[str, Any]:
    app_state = _state(request)
    _require_admin(request)
    users = app_state.db.list_users(state_filter)
    return {"success": True, "users": [_user_row(u, app_state) for u in users]}


@router.post("/api/users/{student_id}/approve")
def admin_approve(request: Request, student_id: str) -> dict[str, Any]:
    state = _state(request)
    admin = _require_admin(request)
    user = state.service.approve(student_id, admin)
    return {"success": True, "message": f"已批准 {student_id}", "user": user}


@router.post("/api/users/{student_id}/reject")
def admin_reject(
    request: Request, student_id: str, payload: dict[str, Any] = Body(default={})
) -> dict[str, Any]:
    state = _state(request)
    admin = _require_admin(request)
    user = state.service.reject(student_id, admin, str(payload.get("reason") or ""))
    return {"success": True, "message": f"已拒绝 {student_id}", "user": user}


@router.post("/api/users/{student_id}/revoke")
def admin_revoke(
    request: Request, student_id: str, payload: dict[str, Any] = Body(default={})
) -> dict[str, Any]:
    state = _state(request)
    admin = _require_admin(request)
    user = state.service.revoke(student_id, admin, str(payload.get("reason") or ""))
    return {"success": True, "message": f"已撤回 {student_id} 的授权", "user": user}


@router.post("/api/users/{student_id}/enabled")
def admin_set_enabled(
    request: Request, student_id: str, payload: dict[str, Any] = Body(...)
) -> dict[str, Any]:
    state = _state(request)
    admin = _require_admin(request)
    enabled = bool(payload.get("enabled"))
    user = state.service.set_enabled(student_id, enabled, admin)
    return {
        "success": True,
        "message": f"已{'启用' if enabled else '停用'} {student_id}",
        "user": user,
    }


@router.post("/api/users/{student_id}/reset-password")
def admin_reset_password(request: Request, student_id: str) -> dict[str, Any]:
    state = _state(request)
    admin = _require_admin(request)
    password = state.service.reset_query_password(student_id, admin)
    return {
        "success": True,
        "password": password,
        "message": f"已重置，新查询口令：{password}（请立即告知该同学，仅显示这一次）",
    }


@router.delete("/api/users/{student_id}")
def admin_delete_user(request: Request, student_id: str) -> dict[str, Any]:
    state = _state(request)
    admin = _require_admin(request)
    state.service.delete_user(student_id, admin)
    return {"success": True, "message": f"已删除 {student_id} 及其签到记录"}


@router.patch("/api/users/{student_id}")
def admin_update_user(
    request: Request, student_id: str, payload: dict[str, Any] = Body(...)
) -> dict[str, Any]:
    state = _state(request)
    _require_admin(request)
    allowed = {"name", "phone", "remark", "major"}
    fields = {k: str(v) for k, v in payload.items() if k in allowed}
    user = state.service.update_user_profile(student_id, **fields)
    return {"success": True, "user": user}


# --------------------------------------------------------------------------- #
# 签到操作
# --------------------------------------------------------------------------- #


@router.post("/api/users/{student_id}/sign")
def admin_sign_user(request: Request, student_id: str) -> dict[str, Any]:
    """立即为某个用户手动签到（用于排障）。"""
    state = _state(request)
    admin = _require_admin(request)
    outcome = state.service.run_one(student_id, force=True)
    state.db.log(
        "admin_sign",
        f"管理员 {admin} 手动为 {student_id} 执行签到：{outcome.message}",
        student_id=student_id,
        level="info" if outcome.ok else "warn",
    )
    return {
        "success": outcome.ok,
        "status": outcome.status,
        "message": outcome.message,
    }


@router.post("/api/run-daily")
def admin_run_daily(
    request: Request, payload: dict[str, Any] = Body(default={})
) -> dict[str, Any]:
    """手动触发一轮全员签到。``force`` 可绕过校历判定（排障用）。"""
    state = _state(request)
    _require_admin(request)
    day_text = str(payload.get("date") or "")
    day = parse_date(day_text) if day_text else dt.date.today()
    result = state.service.run_daily(day, force=bool(payload.get("force")))
    return {"success": True, **result}


@router.post("/api/randomize")
def admin_randomize(
    request: Request, payload: dict[str, Any] = Body(default={})
) -> dict[str, Any]:
    """重新在签到窗口内随机分配各用户的签到时刻。"""
    state = _state(request)
    _require_admin(request)
    ids = payload.get("studentIds")
    count = state.service.randomize_plan_times(list(ids) if ids else None)
    return {
        "success": True,
        "count": count,
        "message": f"已为 {count} 位用户重新随机分配签到时刻",
    }


@router.post("/api/retry-failed")
def admin_retry(request: Request, payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    state = _state(request)
    _require_admin(request)
    days = int(payload.get("days") or 1)
    result = state.service.retry_failed(days=max(1, min(days, 7)))
    return {"success": True, **result}


@router.post("/api/check-calendar")
def admin_check_calendar(
    request: Request, payload: dict[str, Any] = Body(default={})
) -> dict[str, Any]:
    """查询某天是否会执行自动签到（用于验证校历配置）。"""
    state = _state(request)
    _require_admin(request)
    day = parse_date(str(payload.get("date") or "")) or dt.date.today()
    should, reason = state.service.calendar.should_sign(day)
    term = state.service.calendar.term_for(day)
    holiday = state.service.calendar.holiday_for(day)
    return {
        "success": True,
        "date": day.isoformat(),
        "shouldSign": should,
        "reason": reason,
        "term": dict(term) if term else None,
        "holiday": dict(holiday) if holiday else None,
    }


# --------------------------------------------------------------------------- #
# 校历：学期 / 假期
# --------------------------------------------------------------------------- #


@router.get("/api/terms")
def admin_terms(request: Request) -> dict[str, Any]:
    state = _state(request)
    _require_admin(request)
    return {"success": True, "terms": summarize_terms(state.db)}


@router.post("/api/terms")
def admin_add_term(request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    state = _state(request)
    _require_admin(request)
    name = str(payload.get("name") or "").strip()
    start = parse_date(str(payload.get("startDate") or ""))
    end = parse_date(str(payload.get("endDate") or ""))
    if not name:
        raise ServiceError("请填写学期名称")
    if not start or not end:
        raise ServiceError("请填写正确的起止日期")
    if end < start:
        raise ServiceError("结束日期不能早于开始日期")
    term_id = state.db.add_term(name, start.isoformat(), end.isoformat(), str(payload.get("note") or ""))
    state.db.log("term_add", f"新增学期 {name}（{start} ~ {end}）")
    return {"success": True, "id": term_id, "message": f"已添加学期 {name}"}


@router.delete("/api/terms/{term_id}")
def admin_delete_term(request: Request, term_id: int) -> dict[str, Any]:
    state = _state(request)
    _require_admin(request)
    state.db.delete_term(term_id)
    state.db.log("term_delete", f"删除学期 #{term_id}")
    return {"success": True, "message": "已删除"}


@router.get("/api/holidays")
def admin_holidays(request: Request) -> dict[str, Any]:
    state = _state(request)
    _require_admin(request)
    return {"success": True, "holidays": summarize_holidays(state.db)}


@router.post("/api/holidays")
def admin_add_holiday(request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    state = _state(request)
    _require_admin(request)
    name = str(payload.get("name") or "").strip()
    start = parse_date(str(payload.get("startDate") or ""))
    end = parse_date(str(payload.get("endDate") or ""))
    kind = str(payload.get("kind") or "vacation")
    if kind not in {"vacation", "holiday", "makeup"}:
        raise ServiceError("假期类型不正确")
    if not name:
        raise ServiceError("请填写名称")
    if not start or not end:
        raise ServiceError("请填写正确的起止日期")
    if end < start:
        raise ServiceError("结束日期不能早于开始日期")
    holiday_id = state.db.add_holiday(
        name, start.isoformat(), end.isoformat(), kind, str(payload.get("note") or "")
    )
    state.db.log("holiday_add", f"新增{_kind_name(kind)} {name}（{start} ~ {end}）")
    return {"success": True, "id": holiday_id, "message": f"已添加 {name}"}


@router.delete("/api/holidays/{holiday_id}")
def admin_delete_holiday(request: Request, holiday_id: int) -> dict[str, Any]:
    state = _state(request)
    _require_admin(request)
    state.db.delete_holiday(holiday_id)
    state.db.log("holiday_delete", f"删除假期 #{holiday_id}")
    return {"success": True, "message": "已删除"}


# --------------------------------------------------------------------------- #
# 日志
# --------------------------------------------------------------------------- #


@router.get("/api/logs")
def admin_logs(request: Request, limit: int = 200, studentId: str = "") -> dict[str, Any]:
    state = _state(request)
    _require_admin(request)
    logs = state.db.recent_logs(min(max(limit, 1), 1000), studentId)
    return {"success": True, "logs": logs}


# --------------------------------------------------------------------------- #
# 序列化
# --------------------------------------------------------------------------- #


def _kind_name(kind: str) -> str:
    return {"vacation": "假期", "holiday": "节假日", "makeup": "调休上课"}.get(kind, "假期")


def _credential_info(state: Any, user: dict[str, Any]) -> tuple[str, float | None, bool]:
    """判断用户的凭据模式、token 剩余天数、是否具备自动续期能力。

    返回 ``(mode, days_left, can_renew)``，``mode`` ∈ ``{'none','token','password'}``。
    """
    if not user.get("token_enc") and not user.get("credential_enc"):
        return "none", None, False
    token = state.service.secrets.decrypt(user.get("token_enc") or "")
    if token:
        can_renew = bool(state.service.secrets.decrypt(user.get("refresh_token_enc") or ""))
        return "token", token_days_left(token), can_renew
    if state.service.secrets.decrypt(user.get("credential_enc") or ""):
        return "password", None, True
    return "none", None, False


def _user_row(user: dict[str, Any], state: Any = None) -> dict[str, Any]:
    credential_mode = "none"
    token_days: float | None = None
    can_renew = False
    if state is not None:
        credential_mode, token_days, can_renew = _credential_info(state, user)

    # 只有"不能自动续期"的会话才需要提醒重新采集
    needs_recollect = (
        credential_mode == "token"
        and not can_renew
        and token_days is not None
        and token_days < 3
    )
    return {
        "studentId": user.get("student_id", ""),
        "studentIdMasked": mask_student_id(str(user.get("student_id") or "")),
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
        "credentialMode": credential_mode,
        "tokenDaysLeft": round(token_days, 1) if token_days is not None else None,
        "canRenew": can_renew,
        "needsRecollect": needs_recollect,
        "lastError": user.get("last_error") or "",
        "planSignTime": user.get("plan_sign_time") or "",
        "taskId": user.get("task_id") or "",
        "taskName": user.get("task_name") or "",
        "dormName": user.get("dorm_name") or "",
        "roomNo": user.get("room_no") or "",
        "createdAt": user.get("created_at") or "",
        "updatedAt": user.get("updated_at") or "",
    }
