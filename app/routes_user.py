"""用户端路由。

设计约束（来自需求）：
* 首次使用需要先提交申请，管理员批准后才能进行学校打卡账号登录；
* 登录成功后由系统在每天 21:00-22:30 之间随机时刻自动签到；
* 用户端**只能通过输入学号**查看自己的签到情况；
* 提供日历看板。

安全说明：仅凭学号即可查看会泄露信息，因此每位用户在首次绑定时自设一个
"查询口令"，之后凭 学号 + 查询口令 查看。该口令只用于本服务，不是学校密码。
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi import APIRouter, Body, Request, Response

from .constants import USER_COOKIE, USER_SESSION_TTL
from .services import ServiceError

router = APIRouter(tags=["user"])


# --------------------------------------------------------------------------- #
# 辅助
# --------------------------------------------------------------------------- #


def _state(request: Request):
    return request.app.state.app_state


def _current_user(request: Request) -> dict[str, Any] | None:
    state = _state(request)
    raw = request.cookies.get(USER_COOKIE)
    data = state.sessions.loads(raw)
    if not data or data.get("kind") != "user":
        return None
    student_id = str(data.get("sid") or "")
    if not student_id:
        return None
    return state.db.get_user(student_id)


def _require_user(request: Request) -> dict[str, Any]:
    user = _current_user(request)
    if not user:
        raise ServiceError("会话已过期，请重新输入学号与查询口令")
    return user


def _set_user_cookie(response: Response, state, student_id: str) -> None:
    token = state.sessions.dumps({"kind": "user", "sid": student_id}, USER_SESSION_TTL)
    response.set_cookie(
        USER_COOKIE,
        token,
        max_age=USER_SESSION_TTL,
        httponly=True,
        samesite="lax",
        path="/",
    )


# --------------------------------------------------------------------------- #
# 会话状态
# --------------------------------------------------------------------------- #


@router.get("/api/me")
def api_me(request: Request) -> dict[str, Any]:
    state = _state(request)
    user = _current_user(request)
    payload: dict[str, Any] = {
        "success": True,
        "loggedIn": bool(user),
        "config": state.settings.as_public_dict(),
    }
    if user:
        payload["overview"] = state.service.user_overview(user["student_id"])
    return payload


@router.post("/api/logout")
def api_logout(response: Response) -> dict[str, Any]:
    response.delete_cookie(USER_COOKIE, path="/")
    return {"success": True}


# --------------------------------------------------------------------------- #
# 首次使用：申请
# --------------------------------------------------------------------------- #


@router.post("/api/apply")
def api_apply(request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    state = _state(request)
    student_id = str(payload.get("studentId") or "").strip()
    if not student_id:
        raise ServiceError("请输入学号")
    if not student_id.isdigit():
        raise ServiceError("学号应为纯数字")
    result = state.service.submit_application(
        student_id,
        name=str(payload.get("name") or ""),
        phone=str(payload.get("phone") or ""),
        remark=str(payload.get("remark") or ""),
        major=str(payload.get("major") or ""),
    )
    messages = {
        "submitted": "申请已提交，请等待管理员批准",
        "resubmitted": "申请已重新提交，请等待管理员批准",
        "already_pending": "申请已在等待管理员批准",
        "already_approved": "该学号已获批准，可直接绑定",
    }
    return {
        "success": True,
        "state": result["state"],
        "message": messages.get(result["state"], "已提交"),
    }


@router.get("/api/apply/status")
def api_apply_status(request: Request, studentId: str) -> dict[str, Any]:
    state = _state(request)
    result = state.service.application_status(studentId.strip())
    if result["state"] == "not_found":
        return {"success": True, "state": "not_found", "message": "尚未提交申请"}
    user = result.get("user") or {}
    messages = {
        "pending": "等待管理员批准",
        "approved": "已批准，可以绑定学校账号",
        "rejected": f"未获批准：{user.get('rejectReason') or '未说明原因'}",
    }
    return {
        "success": True,
        "state": result["state"],
        "user": user,
        "message": messages.get(result["state"], ""),
    }


# --------------------------------------------------------------------------- #
# 首次使用：绑定学校账号
# --------------------------------------------------------------------------- #


@router.get("/api/captcha")
def api_captcha(request: Request) -> dict[str, Any]:
    state = _state(request)
    try:
        data = state.service.get_captcha()
        return {"success": True, **data}
    except Exception as exc:  # noqa: BLE001
        # 学校未开放验证码接口时不阻塞绑定流程
        return {
            "success": False,
            "message": f"暂无法获取验证码（{exc}），若登录提示需要验证码请联系管理员",
            "captchaKey": "",
            "image": "",
        }


@router.post("/api/bind")
def api_bind(
    request: Request, response: Response, payload: dict[str, Any] = Body(...)
) -> dict[str, Any]:
    state = _state(request)
    student_id = str(payload.get("studentId") or "").strip()
    result = state.service.bind_credentials(
        student_id,
        str(payload.get("password") or ""),
        str(payload.get("queryPassword") or ""),
        captcha_key=str(payload.get("captchaKey") or ""),
        captcha_code=str(payload.get("captchaCode") or ""),
    )
    _set_user_cookie(response, state, student_id)
    return {
        "success": True,
        "message": "绑定成功，系统将在每天 "
        + state.settings.sign_window_start
        + "-"
        + state.settings.sign_window_end
        + " 之间自动为你签到",
        **result,
    }


# --------------------------------------------------------------------------- #
# 查看签到情况
# --------------------------------------------------------------------------- #


@router.post("/api/login")
def api_login(
    request: Request, response: Response, payload: dict[str, Any] = Body(...)
) -> dict[str, Any]:
    """凭 学号 + 查询口令 查看自己的签到情况。"""
    state = _state(request)
    student_id = str(payload.get("studentId") or "").strip()
    query_password = str(payload.get("queryPassword") or "")

    if not student_id:
        raise ServiceError("请输入学号")
    if not query_password:
        raise ServiceError("请输入查询口令")

    user = state.db.get_user(student_id)
    if not user:
        raise ServiceError("未找到该学号，请先提交使用申请")

    if user["approval_state"] == "pending":
        raise ServiceError("申请正在等待管理员批准")
    if user["approval_state"] != "approved":
        raise ServiceError("该学号未获批准使用")

    if not user.get("query_password_hash"):
        raise ServiceError("该学号尚未完成首次绑定，请先绑定学校打卡账号")

    from .security import verify_password

    if not verify_password(query_password, str(user["query_password_hash"])):
        state.db.log("login_fail", f"{student_id} 查询口令错误", student_id=student_id, level="warn")
        raise ServiceError("学号或查询口令错误")

    _set_user_cookie(response, state, student_id)
    state.db.log("login", f"{student_id} 查询登录成功", student_id=student_id)
    return {
        "success": True,
        "message": "登录成功",
        "overview": state.service.user_overview(student_id),
    }


@router.get("/api/overview")
def api_overview(request: Request) -> dict[str, Any]:
    state = _state(request)
    user = _require_user(request)
    return {"success": True, **state.service.user_overview(user["student_id"])}


@router.get("/api/calendar")
def api_calendar(
    request: Request, year: int = 0, month: int = 0, sync: bool = False
) -> dict[str, Any]:
    state = _state(request)
    user = _require_user(request)
    today = dt.date.today()
    year = year or today.year
    month = month or today.month
    if not (1 <= month <= 12) or not (2000 <= year <= 2100):
        raise ServiceError("年月参数不正确")

    student_id = user["student_id"]

    if sync:
        try:
            state.service.sync_month_from_server(student_id, year, month)
        except ServiceError as exc:
            # 同步失败不影响查看本地缓存
            state.db.log("sync_fail", str(exc), student_id=student_id, level="warn")

    data = state.service.calendar_payload(student_id, year, month)
    return {"success": True, **data}


@router.get("/api/records")
def api_records(request: Request, limit: int = 60) -> dict[str, Any]:
    state = _state(request)
    user = _require_user(request)
    rows = state.db.query(
        """
        SELECT sign_date, status, message, sign_status, sign_time, source
        FROM sign_records WHERE student_id = ?
        ORDER BY sign_date DESC LIMIT ?
        """,
        (user["student_id"], max(1, min(limit, 365))),
    )
    return {"success": True, "records": rows}


@router.post("/api/plan-time")
def api_plan_time(
    request: Request, payload: dict[str, Any] = Body(...)
) -> dict[str, Any]:
    """允许用户在自己的窗口内微调签到时刻（仍受随机窗口约束）。"""
    state = _state(request)
    user = _require_user(request)
    value = str(payload.get("planSignTime") or "")
    text = state.service.set_plan_sign_time(user["student_id"], value)
    return {"success": True, "planSignTime": text, "message": f"已设置为每天 {text}"}
