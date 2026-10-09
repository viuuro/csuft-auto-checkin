"""
mitmproxy 抓包插件：捕获中南林学工小程序发给 simp.csuft.edu.cn 的请求。

目的：拿到
  1. 小程序实际使用的 access_token（刷新令牌也行）
  2. 真实的签到请求体（stuSign），用于验证我们复刻的请求是否一致

用法：
  1. 先启动本代理，再重启微信（PC 客户端只在启动时读一次系统代理）
  2. 在小程序里进「打卡」页，正常点一次打卡
  3. 按 Ctrl+C 结束，抓到的内容会落在 tools/capture/ 目录

输出：
  tools/capture/requests.log        可读的请求流水
  tools/capture/raw/<n>-<slug>.txt  含完整请求头与请求体的原文
  tools/capture/tokens.json         抽取到的 token 汇总
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from mitmproxy import ctx, http

OUT_DIR = Path(__file__).resolve().parent / "capture"
RAW_DIR = OUT_DIR / "raw"
OUT_DIR.mkdir(parents=True, exist_ok=True)
RAW_DIR.mkdir(parents=True, exist_ok=True)

LOG_PATH = OUT_DIR / "requests.log"
TOKEN_PATH = OUT_DIR / "tokens.json"

# 只关心学校服务器
TARGET_HOSTS = ("simp.csuft.edu.cn", "csuft.edu.cn", "authserver.csuft.edu.cn")

# 需要重点关注的路径片段
INTERESTING = (
    "oauth/token",
    "dormSign",
    "signRecord",
    "stuSign",
    "getTaskById",
    "getListForApp",
    "getOne",
    "getUserListAppByMonth",
    "captcha",
    "openApi",
)

counter = {"n": 0}
tokens: dict[str, object] = {
    "access_token": "",
    "refresh_token": "",
    "captured_at": "",
    "sign_requests": [],
    "login_requests": [],
}


def _host(flow: http.HTTPFlow) -> str:
    return flow.request.pretty_host or ""


def _matches(flow: http.HTTPFlow) -> bool:
    host = _host(flow)
    return any(host == h or host.endswith("." + h) for h in TARGET_HOSTS)


def _body_text(flow: http.HTTPFlow) -> str:
    req = flow.request
    try:
        if req.content:
            return req.get_text(strict=False)
    except Exception:  # noqa: BLE001
        pass
    return ""


def _pretty_json(text: str) -> str:
    try:
        return json.dumps(json.loads(text), ensure_ascii=False, indent=2)
    except Exception:  # noqa: BLE001
        return text


def request(flow: http.HTTPFlow) -> None:
    if not _matches(flow):
        return

    counter["n"] += 1
    n = counter["n"]
    req = flow.request
    url = req.pretty_url
    body = _body_text(flow)

    is_interesting = any(k.lower() in url.lower() for k in INTERESTING)

    # ---- 写入原文 ----
    slug = re.sub(r"[^A-Za-z0-9]+", "-", url.split("?")[0].split("/")[-1])[:40] or "root"
    raw_path = RAW_DIR / f"{n:03d}-{slug}.txt"
    lines = [
        f"# {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"{req.method} {url}",
        "",
        "--- 请求头 ---",
    ]
    lines += [f"{k}: {v}" for k, v in req.headers.items()]
    if body:
        lines += ["", "--- 请求体 ---", _pretty_json(body)]
    raw_path.write_text("\n".join(lines), encoding="utf-8")

    # ---- 写入流水 ----
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(f"\n{'=' * 78}\n[{n}] {time.strftime('%H:%M:%S')} {req.method} {url}\n")
        for key in ("FlySource-Auth", "FlySource-sign", "Authorization", "Content-Type",
                    "Tenant-Id", "Web-Type", "Captcha-Key"):
            if key in req.headers:
                value = req.headers[key]
                shown = value if key != "FlySource-Auth" else value[:20] + "…"
                fh.write(f"    {key}: {shown}\n")
        if body:
            fh.write(f"    body: {_pretty_json(body)[:1200]}\n")

    # ---- 抽取 token ----
    auth = req.headers.get("FlySource-Auth", "")
    if auth and not tokens["access_token"]:
        tokens["access_token"] = auth
        tokens["captured_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        ctx.log.warn(f"★ 抓到 access_token（{len(auth)} 字符），已保存")

    if "oauth/token" in url.lower():
        entry = {"url": url, "body": body[:1000]}
        tokens["login_requests"].append(entry)  # type: ignore[union-attr]
        ctx.log.warn(f"★ 抓到登录请求：{url.split('?')[0]}")

    if "stusign" in url.lower() or "stuLateSign" in url.lower():
        tokens["sign_requests"].append({"url": url, "body": body[:2000]})  # type: ignore[union-attr]
        ctx.log.warn("★ 抓到签到请求！请求体已保存")

    TOKEN_PATH.write_text(json.dumps(tokens, ensure_ascii=False, indent=2), encoding="utf-8")

    marker = "★" if is_interesting else " "
    ctx.log.info(f"{marker} [{n}] {req.method} {url[:110]}")


def response(flow: http.HTTPFlow) -> None:
    """记录登录响应里的 token（有些客户端把 token 放在响应体）。"""
    if not _matches(flow):
        return
    url = flow.request.pretty_url.lower()
    if "oauth/token" not in url:
        return
    try:
        payload = json.loads(flow.response.get_text(strict=False))
    except Exception:  # noqa: BLE001
        return
    if isinstance(payload, dict):
        if payload.get("access_token"):
            tokens["access_token"] = payload["access_token"]
            ctx.log.warn("★ 从登录响应里取到 access_token")
        if payload.get("refresh_token"):
            tokens["refresh_token"] = payload["refresh_token"]
            ctx.log.warn("★ 从登录响应里取到 refresh_token")
        tokens["login_response"] = {
            k: ("<省略>" if k in ("access_token", "refresh_token") else v)
            for k, v in payload.items()
        }
        TOKEN_PATH.write_text(json.dumps(tokens, ensure_ascii=False, indent=2), encoding="utf-8")


def done() -> None:
    ctx.log.warn(f"抓包结束：共 {counter['n']} 条请求")
    ctx.log.warn(f"结果目录：{OUT_DIR}")
    if tokens["access_token"]:
        ctx.log.warn("已拿到 access_token ✔")
    else:
        ctx.log.warn("未拿到 access_token —— 请确认微信是在代理启动之后重启的")
