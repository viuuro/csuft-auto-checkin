"""中南林学工（simp.csuft.edu.cn）签到接口客户端。

本模块是对微信小程序 ``wx0e47c34c9982aa09`` 的逆向结果的服务端重实现。
小程序基于 uni-app + flysource 框架，所有请求都经过一个签名拦截器，
这里逐字段还原了该拦截器与业务接口的行为。

签名规则（来自 ``http/request.js``）：:

    ts       = 当前毫秒时间戳
    token    = access_token
    path     = url 去掉 query 后再拼上 "?sign="            # 注意：保留字面 "?sign="
    FlySource-sign = md5(path + md5(str(ts) + token)) + "1." + base64(str(ts))

请求头：:

    Authorization:   Basic base64("<ClientId>:<ClientSecret>")
    FlySource-Auth:  <token>
    FlySource-sign:  <签名>

``stuTaskId`` 是请求体自身的校验串，同样按小程序的方式计算：
``md5(json.dumps({latitude, longitude, locationAccuracy, signDate, taskId, fileId}))``。
"""

from __future__ import annotations

import base64
import hashlib
import json
import time as _time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, time
from typing import Any

import httpx

# --------------------------------------------------------------------------- #
# 常量（逆向自 http/authUtil.js）
# --------------------------------------------------------------------------- #

DEFAULT_BASE_URL = "https://simp.csuft.edu.cn"

# 小程序端客户端：只允许 grant_type=wxapp（服务端会拒绝 password）
WX_CLIENT_ID = "flysource_wise_wxapp"
WX_CLIENT_SECRET = "DA788asdUDjnasd_flysource_wxappdsdadDAIUiuwqe"

# 网页端客户端：允许 grant_type=password，这正是我们需要的登录方式
CLIENT_ID = "flySource"
CLIENT_SECRET = "FlySource_SDEKOFSIDF82329F8sd8723dS87DAS"

TOKEN_HEADER = "FlySource-Auth"
SIGN_HEADER = "FlySource-sign"
DEFAULT_TENANT_ID = "000000"

# 业务接口路径
PATH_CAPTCHA = "/api/flySource-auth/captcha"
PATH_TOKEN = "/api/flySource-auth/oauth/token"
PATH_LOGOUT = "/api/flySource-auth/logOut"

PATH_TASK_LIST = "/api/flySource-yxgl/dormSignTask/getListForApp"
PATH_TASK_DETAIL = "/api/flySource-yxgl/dormSignTask/getTaskByIdForApp"
PATH_RECORD_MONTH = "/api/flySource-yxgl/dormSignRecord/getUserListAppByMonth"
PATH_RECORD_ONE = "/api/flySource-yxgl/dormSignRecord/getOne"
PATH_SIGN = "/api/flySource-yxgl/dormSignRecord/stuSign"
PATH_SIGN_LATE = "/api/flySource-yxgl/dormSignRecord/stuLateSign"
PATH_UPLOAD = "/api/flySource-resource/oss/endpoint/put-file-save"

# 签到状态码 —— 由服务端 signStatusName 字段实测证实（2026-10-09 核验）：
#
#   signStatus  signStatusName  signTime   含义
#   ----------  --------------  ---------  --------------------------
#       0        正常            有值       已签到成功   ← 关键！
#       3        未签            空         今天还没签
#       5        离校            空         离校中（免打卡）
#
# 注意 signStatus=0 **不等于**「未打卡」。判定是否已签，必须看 signTime
# 是否有值 —— 只看状态码会把成功误判成失败。
#
# 早期版本这里是错的（把 0 当"未打卡"），导致签到成功后又被记成失败。
SIGN_STATUS_NAMES = {
    0: "正常",
    1: "已签到",
    2: "请假中",
    3: "未签",
    4: "走读中",
    5: "离校",
    6: "外宿中",
}


class FlySourceError(Exception):
    """接口返回的业务错误。"""

    def __init__(self, message: str, *, code: Any = None, payload: Any = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.payload = payload


class AuthExpired(FlySourceError):
    """access_token 失效，需要重新登录。"""


class CaptchaRequired(FlySourceError):
    """登录需要图形验证码。

    ``captcha_key`` 必须与用户填写的验证码一起回传。
    """

    def __init__(self, message: str = "需要图形验证码", *, captcha_key: str = ""):
        super().__init__(message)
        self.captcha_key = captcha_key


# --------------------------------------------------------------------------- #
# 签名工具
# --------------------------------------------------------------------------- #


def md5_hex(text: str) -> str:
    """小程序的 md5 默认输出小写十六进制。"""
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def build_sign_header(url: str, token: str, ts_ms: int | None = None) -> str:
    """复刻 ``http/request.js`` 里的 FlySource-sign 计算。

    **签名签的是相对路径去掉 query 后的部分，且 token 用原始值（不加 bearer）。**

    等价于小程序里的::

        var r = e.url.split("?")[0] + "?sign=";     // e.url 是相对路径
        header[SignHeader] = md5(r + md5(ts + token)) + "1." + base64(ts)

    实测证据（抓包比对，936 种组合中唯一命中）::

        # 完整 URL   https://simp.csuft.edu.cn/api/xxx?a=1
        # 签名输入   /api/xxx?sign=
        # 结果       与小程序逐字节一致

    两个易错点：

    1. 误用**完整 URL**（含 host）→ 签名不匹配。
    2. token 加了 ``bearer `` 前缀 → 签名不匹配（小程序发的是原始 token；
       网页端才用 ``bearer `` 前缀）。
    """
    if ts_ms is None:
        ts_ms = int(_time.time() * 1000)
    ts = str(ts_ms)
    sign_path = _sign_path(url) + "?sign="
    digest = md5_hex(sign_path + md5_hex(ts + token))
    encoded_ts = base64.b64encode(ts.encode("utf-8")).decode("ascii")
    return f"{digest}1.{encoded_ts}"


def _sign_path(url: str) -> str:
    """从任意形式的 URL 取出用于签名的相对路径（去 host、去 query）。"""
    text = url
    if "://" in text:
        # 去掉 scheme://host
        without_scheme = text.split("://", 1)[1]
        slash = without_scheme.find("/")
        text = without_scheme[slash:] if slash >= 0 else "/"
    # 去掉 query 与 fragment
    for sep in ("?", "#"):
        text = text.split(sep, 1)[0]
    return text


def relative_url(url: str) -> str:
    """把完整 URL 归一化成相对路径（保留 query）。"""
    if "://" in url:
        without_scheme = url.split("://", 1)[1]
        slash = without_scheme.find("/")
        return without_scheme[slash:] if slash >= 0 else "/"
    return url


def build_basic_auth(client_id: str = CLIENT_ID, client_secret: str = CLIENT_SECRET) -> str:
    raw = f"{client_id}:{client_secret}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def build_stu_task_id(
    *,
    latitude: str,
    longitude: str,
    location_accuracy: str,
    sign_date: str,
    task_id: str,
    file_id: str = "",
) -> str:
    """计算请求体里的 ``stuTaskId``。

    字段顺序与小程序一致（JSON 序列化顺序会影响结果）。
    """
    payload = {
        "latitude": latitude,
        "longitude": longitude,
        "locationAccuracy": location_accuracy,
        "signDate": sign_date,
        "taskId": task_id,
        "fileId": file_id,
    }
    return md5_hex(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))


def haversine_meters(lat1: float, lng1: float, lat2: float, lng2: float) -> int:
    """复刻小程序的 getDistance：返回米（四舍五入到 10 米）。"""
    import math

    r = 6378.137
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p1 - p2
    dl = math.radians(lng1) - math.radians(lng2)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    dist_km = 2 * math.asin(math.sqrt(h)) * r
    # 小程序是 Math.round(1e4 * l) / 10，即公里保留 4 位后换算成米并取整
    return int(round(round(dist_km * 1e4) / 10))


# --------------------------------------------------------------------------- #
# 结果对象
# --------------------------------------------------------------------------- #


@dataclass
class LoginResult:
    access_token: str
    refresh_token: str = ""
    expires_in: int = 0
    account_no: str = ""
    user_name: str = ""
    tenant_id: str = ""
    school_name: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class SignTask:
    """一个打卡任务（宿舍签到任务）。"""

    task_id: str
    task_name: str = ""
    sign_start_time: str = ""
    sign_end_time: str = ""
    allow_late_sign_time: str = ""
    open_locate: int = 0
    open_take_photo: int = 0
    is_late_stu_take_photo: int = 0
    is_allow_special_sign: int = 0
    is_allow_late: int = 0
    scan_type: Any = None
    dorm: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def room_id(self) -> str:
        return str(self.dorm.get("roomId") or "")

    @property
    def dorm_name(self) -> str:
        return str(self.dorm.get("dormName") or "")

    @property
    def room_no(self) -> str:
        return str(self.dorm.get("roomNo") or "")

    @property
    def location_lat(self) -> float | None:
        return _to_float(self.dorm.get("locationLat"))

    @property
    def location_lng(self) -> float | None:
        return _to_float(self.dorm.get("locationLng"))

    @property
    def location_accuracy(self) -> float | None:
        """宿舍允许的定位误差（米）。"""
        return _to_float(self.dorm.get("locationAccuracy"))

    @property
    def location_state(self) -> Any:
        return self.dorm.get("locationState")

    @property
    def needs_photo(self) -> bool:
        return int(self.open_take_photo or 0) == 1

    def in_window(self, now: time | None = None) -> tuple[bool, str]:
        """当前是否处于任务的签到时段内。

        实测：窗口外提交会被服务端拒绝，返回 ``未到签到时间！``。
        返回 ``(是否可签到, 原因)``。
        """
        start = _parse_hhmm(self.sign_start_time)
        end = _parse_hhmm(self.sign_end_time)
        if start is None or end is None:
            return True, "任务未声明签到时段，交由服务端判断"

        moment = now or datetime.now().time()
        if moment < start:
            return False, f"未到签到时间（{self.sign_start_time} 开始）"
        if moment > end:
            return False, f"已过签到时间（{self.sign_end_time} 结束）"
        return True, "在签到时段内"

    def summary(self) -> dict[str, Any]:
        return {
            "taskId": self.task_id,
            "taskName": self.task_name,
            "signStartTime": self.sign_start_time,
            "signEndTime": self.sign_end_time,
            "openLocate": self.open_locate,
            "openTakePhoto": self.open_take_photo,
            "dormName": self.dorm_name,
            "roomNo": self.room_no,
            "locationLat": self.location_lat,
            "locationLng": self.location_lng,
            "locationAccuracy": self.location_accuracy,
        }


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# 客户端
# --------------------------------------------------------------------------- #


class FlySourceClient:
    """同步 HTTP 客户端，线程安全使用方式：每次请求复用同一个实例即可。

    服务端没有 ``wx.login``，因此登录走账号密码模式
    （``grant_type=password``），这也是小程序 ``pages/login/binduser`` 绑定
    账号时使用的同一套接口。
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        tenant_id: str = DEFAULT_TENANT_ID,
        client_id: str = CLIENT_ID,
        client_secret: str = CLIENT_SECRET,
        timeout: float = 30.0,
        verify_tls: bool = True,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self._basic = "Basic " + base64.b64encode(
            f"{client_id}:{client_secret}".encode("utf-8")
        ).decode("ascii")
        self._client = httpx.Client(
            timeout=timeout,
            verify=verify_tls,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
        )
        self.access_token: str = ""

    # -- 生命周期 ---------------------------------------------------------- #

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "FlySourceClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- 内部请求 ---------------------------------------------------------- #

    def _url(self, path: str) -> str:
        return self.base_url + path

    def _headers(
        self, *, auth: bool, token: str = "", sign_path: str = ""
    ) -> dict[str, str]:
        """构造请求头。

        ``sign_path`` 必须是**相对路径**（如 ``/api/xxx``）——签名签的就是它，
        与小程序拦截器看到的一致。传完整 URL 会导致签名不匹配。
        """
        headers: dict[str, str] = {"Authorization": self._basic}
        tok = token or self.access_token
        if auth and tok:
            headers[TOKEN_HEADER] = tok
            headers[SIGN_HEADER] = build_sign_header(sign_path or "/", tok)
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        json_body: Any = None,
        headers: dict[str, str] | None = None,
        auth: bool = True,
        token: str = "",
    ) -> Any:
        url = self._url(path)
        # 签名基于相对路径（不含 host）
        merged = self._headers(auth=auth, token=token, sign_path=path)
        if headers:
            merged.update(headers)

        try:
            resp = self._client.request(
                method,
                url,
                params=params,
                data=data,
                json=json_body,
                headers=merged,
            )
        except httpx.HTTPError as exc:  # 网络层错误
            raise FlySourceError(f"网络错误：{exc}") from exc

        if resp.status_code == 401:
            raise AuthExpired("未登录或登录已过期", code=401)

        try:
            payload = resp.json()
        except ValueError:
            if resp.status_code >= 400:
                raise FlySourceError(
                    f"服务端返回 HTTP {resp.status_code}",
                    code=resp.status_code,
                    payload=resp.text[:500],
                ) from None
            raise FlySourceError("服务端返回的不是 JSON") from None

        # OAuth 风格错误：{"error":..,"error_description":..}
        if isinstance(payload, dict) and payload.get("error"):
            err = str(payload["error"])
            desc = str(payload.get("error_description") or err)
            if err in {"invalid_captcha", "captcha_error", "invalid_captcha_code"} or "验证码" in desc:
                raise CaptchaRequired(desc, captcha_key=resp.headers.get("Captcha-Key", ""))
            if err == "invalid_token":
                raise AuthExpired(desc, code=err, payload=payload)
            raise FlySourceError(desc, code=err, payload=payload)

        if resp.status_code >= 400:
            msg = ""
            if isinstance(payload, dict):
                msg = str(payload.get("msg") or payload.get("message") or "")
            raise FlySourceError(
                msg or f"服务端返回 HTTP {resp.status_code}",
                code=resp.status_code,
                payload=payload,
            )

        # 业务层 401
        if isinstance(payload, dict) and payload.get("code") == 401:
            raise AuthExpired("未登录或登录已过期（业务码 401）", code=401, payload=payload)
        return payload

    # -- 登录 -------------------------------------------------------------- #

    def get_captcha(self, captcha_type: str = "captcha") -> tuple[str, bytes]:
        """获取图形验证码。

        返回 ``(captcha_key, image_bytes)``。小程序把验证码图片和 key 一起返回，
        这里同样返回原始字节，由前端直接展示。
        """
        url = self._url(PATH_CAPTCHA)
        headers = self._headers(auth=False)
        try:
            resp = self._client.get(url, params={"type": captcha_type}, headers=headers)
        except httpx.HTTPError as exc:
            raise FlySourceError(f"获取验证码失败：{exc}") from exc
        if resp.status_code >= 400:
            raise FlySourceError(f"获取验证码失败：HTTP {resp.status_code}")

        captcha_key = (
            resp.headers.get("Captcha-Key")
            or resp.headers.get("captcha-key")
            or ""
        )
        content_type = resp.headers.get("content-type", "")

        # 可能是图片，也可能是 {"key":..,"image":base64} 形式
        if "image" in content_type or resp.content[:2] in (b"\xff\xd8", b"\x89P", b"GI"):
            return captcha_key, resp.content

        try:
            payload = resp.json()
        except ValueError:
            return captcha_key, resp.content

        if isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, dict):
                key = data.get("key") or data.get("captchaKey") or captcha_key
                image_b64 = data.get("image") or data.get("img") or ""
                if image_b64:
                    if "," in image_b64:
                        image_b64 = image_b64.split(",", 1)[1]
                    return str(key), base64.b64decode(image_b64)
            if isinstance(data, str) and data:
                return captcha_key, base64.b64decode(data)
        return captcha_key, resp.content

    def login(
        self,
        username: str,
        password: str,
        *,
        captcha_key: str = "",
        captcha_code: str = "",
        grant_type: str = "password",
        login_type: str = "captcha",
    ) -> LoginResult:
        """账号密码登录，成功后保存 access_token。

        密码按小程序/网页端的做法先做一次 md5 再提交。
        ``login_type`` 对应网页端的 ``type`` 参数：正常登录传 ``captcha``。
        若服务端要求图形验证码，会抛出 :class:`CaptchaRequired`。
        """
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Tenant-Id": self.tenant_id,
        }
        if captcha_key:
            headers["Captcha-Key"] = captcha_key
        if captcha_code:
            headers["Captcha-Code"] = captcha_code

        # 网页端 signIn 是这么发的（见 simp.csuft.edu.cn/js/index-*.js）：
        #   service({ headers:{"Tenant-Id":e},
        #             url:"/api/flySource-auth/oauth/token", method:"post",
        #             params:{ tenantId:e, username:_, password:md5(t),
        #                      type:i, grant_type:"password", scope:"all" } })
        # 即**参数走 query string**、Tenant-Id 走请求头。这里逐字段对齐。
        params = {
            "tenantId": self.tenant_id,
            "username": username,
            "password": md5_hex(password),
            "grant_type": grant_type,
            "scope": "all",
        }
        if login_type:
            params["type"] = login_type

        payload = self._request(
            "POST",
            PATH_TOKEN,
            params=params,
            headers=headers,
            auth=False,
        )
        return self._consume_login(payload, username)

    def login_auto(
        self,
        username: str,
        password: str,
        *,
        captcha_key: str = "",
        captcha_code: str = "",
    ) -> LoginResult:
        """自动选择登录方式：先直连，必要时再带图形验证码。

        服务端在部分场景下会要求验证码，此时抛出 :class:`CaptchaRequired`，
        调用方（Web 层）应展示验证码并带着 ``captcha_key``/``captcha_code``
        重新调用。
        """
        last_error: Exception | None = None
        for login_type in (None, "captcha"):
            if captcha_code and login_type != "captcha":
                # 已经拿到验证码就直接用 captcha 模式，避免白白失败一次
                continue
            try:
                return self.login(
                    username,
                    password,
                    captcha_key=captcha_key,
                    captcha_code=captcha_code,
                    login_type=login_type or "",
                )
            except CaptchaRequired as exc:
                last_error = exc
                continue
            except FlySourceError:
                raise
        if last_error:
            raise last_error
        raise FlySourceError("登录失败")

    def refresh_access_token(self, refresh_token: str) -> LoginResult:
        """用 refresh_token 换取新的 access_token。

        实测（2026-09-30）该授权可用，且 **refresh_token 会随每次刷新轮换**，
        因此可以实现滚动续期 —— 同学只需授权一次，之后长期自动维持会话。

        调用方必须保存返回的 ``refresh_token``（通常是新的那个），否则下一次
        刷新会失败。
        """
        if not refresh_token:
            raise FlySourceError("缺少 refresh_token")

        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Tenant-Id": self.tenant_id,
            "Web-Type": "wxapp",  # 与小程序一致
        }
        params = {
            "tenantId": self.tenant_id,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
            "scope": "all",
        }
        payload = self._request(
            "POST",
            PATH_TOKEN,
            params=params,
            headers=headers,
            auth=False,
        )
        result = self._consume_login(payload, "")
        if not result.refresh_token:
            # 服务端没给新的，就沿用旧的，避免续期链断掉
            result.refresh_token = refresh_token
        return result

    def _consume_login(self, payload: Any, username: str) -> LoginResult:
        if not isinstance(payload, dict):
            raise FlySourceError("登录返回格式异常", payload=payload)

        # OAuth 错误形如 {"error":"invalid_grant","error_description":"..."}
        if payload.get("error"):
            err = str(payload["error"])
            desc = str(payload.get("error_description") or err)
            if err in {"invalid_captcha", "captcha_error", "invalid_grant_captcha"}:
                raise CaptchaRequired(desc)
            raise FlySourceError(desc, code=err, payload=payload)

        token = payload.get("access_token") or ""
        if not token:
            msg = str(payload.get("msg") or payload.get("message") or "登录失败，未返回 access_token")
            raise FlySourceError(msg, code=payload.get("code"), payload=payload)

        self.access_token = token
        return LoginResult(
            access_token=token,
            refresh_token=str(payload.get("refresh_token") or ""),
            expires_in=int(payload.get("expires_in") or 0),
            account_no=str(payload.get("accountNo") or payload.get("account_no") or username),
            user_name=str(payload.get("userName") or payload.get("user_name") or ""),
            tenant_id=str(payload.get("tenantId") or self.tenant_id),
            school_name=str(payload.get("schoolName") or ""),
            raw=payload,
        )

    # -- 业务接口 ---------------------------------------------------------- #

    def list_tasks(self, *, current: int = 1, size: int = 20, **extra: Any) -> list[SignTask]:
        """打卡任务列表（``getListForApp``）。"""
        params: dict[str, Any] = {"current": current, "size": size}
        params.update({k: v for k, v in extra.items() if v not in (None, "")})
        payload = self._request("GET", PATH_TASK_LIST, params=params)
        records = _records_of(payload)
        return [self._parse_task(item) for item in records]

    def get_task(self, task_id: str) -> SignTask:
        """任务详情（``getTaskByIdForApp``），包含宿舍定位基准。"""
        payload = self._request(
            "GET", PATH_TASK_DETAIL, params={"taskId": task_id}
        )
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise FlySourceError(_msg_of(payload) or "获取任务详情失败", payload=payload)
        return self._parse_task(data)

    @staticmethod
    def _parse_task(data: dict[str, Any]) -> SignTask:
        dorm = data.get("dormitoryRegisterVO") or {}
        if not isinstance(dorm, dict):
            dorm = {}
        return SignTask(
            task_id=str(data.get("id") or data.get("taskId") or ""),
            task_name=str(data.get("taskName") or data.get("name") or ""),
            sign_start_time=str(data.get("signStartTime") or ""),
            sign_end_time=str(data.get("signEndTime") or ""),
            allow_late_sign_time=str(data.get("allowLateSignTime") or ""),
            open_locate=int(data.get("openLocate") or 0),
            open_take_photo=int(data.get("openTakePhoto") or 0),
            is_late_stu_take_photo=int(data.get("isLateStuTakePhoto") or 0),
            is_allow_special_sign=int(data.get("isAllowSpecialSign") or 0),
            is_allow_late=int(data.get("isAllowLate") or 0),
            scan_type=data.get("scanType"),
            dorm=dorm,
            raw=data,
        )

    def month_records(self, task_id: str, month: str) -> dict[str, Any]:
        """某月的打卡记录，``month`` 形如 ``2026-06``。

        返回 ``{日期: 记录}``，记录里 ``signStatus`` 是签到状态码。
        """
        payload = self._request(
            "GET",
            PATH_RECORD_MONTH,
            params={"taskId": task_id, "month": month},
        )
        data = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(data, dict):
            return data
        return {}

    def today_record(self, task_id: str, sign_date: str = "") -> dict[str, Any]:
        """当日打卡记录（``getOne``）。``sign_date`` 用于补签查询。"""
        params: dict[str, Any] = {"taskId": task_id}
        if sign_date:
            params["signDate"] = sign_date
        payload = self._request("GET", PATH_RECORD_ONE, params=params)
        data = payload.get("data") if isinstance(payload, dict) else None
        return data if isinstance(data, dict) else {}

    def sign(
        self,
        task: SignTask,
        *,
        latitude: float,
        longitude: float,
        sign_date: str,
        file_id: str = "",
        sign_type: int = 0,
        late: bool = False,
        check_window: bool = True,
    ) -> dict[str, Any]:
        """执行签到。

        ``latitude``/``longitude`` 应与宿舍基准点一致（或误差在允许范围内）。
        ``locationAccuracy`` 是到宿舍基准点的距离（米），与小程序一致。

        ``check_window``：为 True 时若当前时刻不在任务签到时段内，直接抛错
        而不发请求。实测服务端在窗口外会返回 ``未到签到时间！``（HTTP 200 +
        ``success:false``），本地先拦一道可避免无谓请求。
        """
        if check_window and not late and sign_date == _today_str():
            ok, reason = task.in_window()
            if not ok:
                raise FlySourceError(reason)

        lat_s = _fmt_coord(latitude)
        lng_s = _fmt_coord(longitude)

        # locationAccuracy 语义是「签到点与宿舍基准点的距离」。
        # 我们使用宿舍基准点自身，故为 0 —— 必然满足服务端的定位校验。
        distance = 0
        if task.location_lat is not None and task.location_lng is not None:
            distance = haversine_meters(
                latitude, longitude, task.location_lat, task.location_lng
            )

        stu_task_id = build_stu_task_id(
            latitude=lat_s,
            longitude=lng_s,
            location_accuracy=str(distance),
            sign_date=sign_date,
            task_id=task.task_id,
            file_id=file_id,
        )

        body: dict[str, Any] = {
            "taskId": task.task_id,
            "roomId": task.room_id,
            "isLateStuTakePhoto": task.is_late_stu_take_photo,
            "scanType": task.scan_type,
            "signLat": lat_s,
            "signLng": lng_s,
            "locationAccuracy": str(distance),
            "signDate": sign_date,
            "fileId": file_id,
            "signType": sign_type,
            "scanCode": "",
            "stuTaskId": stu_task_id,
        }

        path = PATH_SIGN_LATE if late else PATH_SIGN
        payload = self._request(
            "POST",
            path,
            json_body=body,
            headers={"Content-Type": "application/json"},
        )
        if isinstance(payload, dict) and payload.get("success") is False:
            raise FlySourceError(_msg_of(payload) or "打卡失败", payload=payload)
        return payload if isinstance(payload, dict) else {}

    def logout(self) -> None:
        try:
            self._request("GET", PATH_LOGOUT, auth=False)
        except FlySourceError:
            pass
        self.access_token = ""


# --------------------------------------------------------------------------- #
# JWT 工具（token 是无签名校验需求的普通 JWT，这里只读 claims）
# --------------------------------------------------------------------------- #


def jwt_claims(token: str) -> dict[str, Any]:
    """解析 JWT 的 payload 部分；失败返回空字典。

    仅用于读取 ``exp`` / ``accountNo`` 等信息，**不做签名校验**
    （签名校验是服务端的事，我们只关心何时过期）。
    """
    parts = str(token or "").split(".")
    if len(parts) != 3:
        return {}
    try:
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, TypeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def token_expires_at(token: str) -> datetime | None:
    """返回 token 的过期时间；无 ``exp`` 时返回 None。"""
    exp = jwt_claims(token).get("exp")
    if not exp:
        return None
    try:
        return datetime.fromtimestamp(int(exp))
    except (TypeError, ValueError, OSError):
        return None


def token_days_left(token: str) -> float | None:
    """剩余有效天数；无法判断时返回 None。"""
    expires = token_expires_at(token)
    if expires is None:
        return None
    return (expires - datetime.now()).total_seconds() / 86400


def token_is_expired(token: str, *, skew_minutes: int = 5) -> bool:
    """token 是否已过期（留一点余量）。"""
    expires = token_expires_at(token)
    if expires is None:
        return False  # 判断不了就当没过期，让服务端裁决
    return datetime.now() >= expires - timedelta(minutes=skew_minutes)


class TokenExpired(FlySourceError):
    """本地已判定 token 过期，无需发起请求。"""

# --------------------------------------------------------------------------- #
# 解析辅助
# --------------------------------------------------------------------------- #


def _records_of(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        records = data.get("records")
        if isinstance(records, list):
            return [x for x in records if isinstance(x, dict)]
    return []


def _msg_of(payload: Any) -> str:
    if isinstance(payload, dict):
        return str(payload.get("msg") or payload.get("message") or "")
    return ""


def _fmt_coord(value: float) -> str:
    """坐标格式化：小程序直接 toString()，这里避免出现多余的 .0。"""
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text or "0"


def _parse_hhmm(value: str) -> time | None:
    """解析 ``HH:MM`` 或 ``HH:MM:SS``。"""
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(text, fmt).time()
        except ValueError:
            continue
    return None


def _today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")
