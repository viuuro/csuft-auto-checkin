#!/usr/bin/env python3
"""单元测试：签名算法、stuTaskId、距离计算、校历判定、配置与加密。

用法: python tools/test_units.py
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from app.flysource import (  # noqa: E402
    CLIENT_SECRET,
    WX_CLIENT_SECRET,
    FlySourceClient,
    build_basic_auth,
    build_sign_header,
    build_stu_task_id,
    haversine_meters,
    md5_hex,
)
from app.security import (  # noqa: E402
    SecretBox,
    SessionSigner,
    generate_password,
    hash_password,
    mask_student_id,
    verify_password,
)

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  [PASS] {name}")
    else:
        FAILED.append(f"{name} :: {detail}")
        print(f"  [FAIL] {name} :: {detail}")


# --------------------------------------------------------------------------- #
print("\n=== 签名算法（含真实抓包向量）===")

# 以下向量来自 2026-09-30 对微信小程序实际请求的抓包。
# 关键结论：签名签的是【相对路径去 query】+ "?sign="，token 用原始值。
REAL_TOKEN = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJwYXNzV29yZExldmVsIjowLCJhdmF0YXJVcmwiOiJjYWU2NWY0YmIxOTRmZDgwNjY5YzkzZWRjYzU5NTk2NSIs"
    "InVzZXJfbmFtZSI6IjIwMjQyNDE4IiwiYWNjb3VudFR5cGUiOjUsInVzZXJOYW1lIjoi5p2O6L2pIiwicm9sZVR5cGUiOiIzIiwidXNlcklkIjoiZTAyMTk2M2YwZDRhNWI1Zjg5MjY0MWE4YzZmYTIwNTYiLCJhdXRob3JpdGllcyI6WyJzdHVkZW50Il0sImNsaWVudF9pZCI6ImZseXNvdXJjZV93aXNlX3d4YXBwIiwibGFzdExvZ2luVGltZSI6IjIwMjYtMDktMjkgMjM6NTE6NDAiLCJvYXV0aElkIjpudWxsLCJzY29wZSI6WyJhbGwiXSwiYWNjb3VudE5vIjoiMjAyNDI0MTgiLCJ0ZW5hbnRJZCI6IjAwMDAwMCIsInJvbGVOYW1lIjoic3R1ZGVudCIsInVzZXJUeXBlIjoyLCJkZXRhaWwiOnsic3lzQXV0aFR5cGUiOiJzbXMiLCJpc1N5c1VzZXJTZWNvbmRBdXRoIjp0cnVlfSwiZXhwIjoxNzkyMzAyODY0LCJzY2hvb2xOYW1lIjoi5Lit5Y2X5p6X5Lia56eR5oqA5aSn5a2mIiwianRpIjoiZWY0YWNjNDMtYTM5NC00NmM3LTgyNjYtMDQ0YTM3YjZkZDE3In0."
    "yfXeIY0fiSLnyADLuBHRo2N6edDPA0XgEm7t6sao_JI"
)

REAL_VECTORS = [
    (
        "https://simp.csuft.edu.cn/api/flySource-yxgl/dormSignTask/getListForApp?current=1&size=10",
        "1790699446853",
        "5f5375a7adc4ec6b0f96b240b881180a1.MTc5MDY5OTQ0Njg1Mw==",
    ),
    (
        "https://simp.csuft.edu.cn/api/flySource-yxgl/dormSignTask/getTaskByIdForApp"
        "?taskId=45f90f7a3a61ccd4e35611bb59ed7f25",
        "1790699448573",
        "30dd21907c8bdab268c2ec07ced699a81.MTc5MDY5OTQ0ODU3Mw==",
    ),
]

for url, ts, expected in REAL_VECTORS:
    actual = build_sign_header(url, REAL_TOKEN, int(ts))
    check(
        f"抓包向量一致（{url.split('/')[-1].split('?')[0]}）",
        actual == expected,
        f"\n    got={actual}\n    exp={expected}",
    )

# 明确守住两个易错点
check(
    "签名基于相对路径而非完整 URL",
    build_sign_header("https://simp.csuft.edu.cn/api/x", "T", 1000)
    == build_sign_header("/api/x", "T", 1000),
    "完整 URL 与相对路径必须得到同一结果",
)
check(
    "签名会剔除 query 参数",
    build_sign_header("/api/x?a=1", "T", 1000) == build_sign_header("/api/x?b=2", "T", 1000),
    "query 不应参与签名",
)
check(
    "token 不加 bearer 前缀",
    build_sign_header("/api/x", "T", 1000) != build_sign_header("/api/x", "bearer T", 1000),
    "加了前缀会算出不同签名",
)

# 与网页端公式对照（同一个公式，网页端只是 url 本来就是相对路径）
URL = "https://simp.csuft.edu.cn/api/flySource-yxgl/dormSignRecord/stuSign"
TOKEN = "abc123token"
TS = 1700000000000

expected = (
    md5_hex("/api/flySource-yxgl/dormSignRecord/stuSign?sign=" + md5_hex(str(TS) + TOKEN))
    + "1."
    + base64.b64encode(str(TS).encode()).decode()
)
actual = build_sign_header(URL, TOKEN, TS)
check("与手算公式一致", actual == expected, f"\n    got={actual}\n    exp={expected}")

check(
    "sign 以 '1.' + base64 时间戳结尾",
    actual.endswith("1." + base64.b64encode(str(TS).encode()).decode()),
    actual,
)

check(
    "Basic 头使用网页端客户端",
    build_basic_auth() == "Basic " + base64.b64encode(f"flySource:{CLIENT_SECRET}".encode()).decode(),
    build_basic_auth(),
)
check(
    "Basic 头可指定小程序客户端",
    build_basic_auth("flysource_wise_wxapp", WX_CLIENT_SECRET)
    == "Basic " + base64.b64encode(f"flysource_wise_wxapp:{WX_CLIENT_SECRET}".encode()).decode(),
)

# --------------------------------------------------------------------------- #
print("\n=== stuTaskId ===")

stu = build_stu_task_id(
    latitude="28.139500",
    longitude="112.994500",
    location_accuracy="12",
    sign_date="2026-06-18",
    task_id="T1",
    file_id="",
)
js_payload = '{"latitude":"28.139500","longitude":"112.994500","locationAccuracy":"12","signDate":"2026-06-18","taskId":"T1","fileId":""}'
check("stuTaskId = md5(紧凑 JSON)", stu == hashlib.md5(js_payload.encode()).hexdigest(), stu)

# 字段顺序必须与小程序一致
check(
    "JSON 字段顺序固定",
    list(json.loads(js_payload).keys())
    == ["latitude", "longitude", "locationAccuracy", "signDate", "taskId", "fileId"],
)

# --------------------------------------------------------------------------- #
print("\n=== 距离计算（对照小程序 getDistance）===")

d_same = haversine_meters(28.1395, 112.9945, 28.1395, 112.9945)
check("同点为 0 米", d_same == 0, str(d_same))

d_near = haversine_meters(28.1395, 112.9945, 28.13955, 112.9945)
check("约 5.5 米的偏移落在 0-20 米", 0 < d_near <= 20, str(d_near))

d_1km = haversine_meters(28.1395, 112.9945, 28.1485, 112.9945)
check("约 1 公里", 990 <= d_1km <= 1010, str(d_1km))

# --------------------------------------------------------------------------- #
print("\n=== 口令与加密 ===")

h = hash_password("s3cret-pw")
check("bcrypt 哈希可校验", verify_password("s3cret-pw", h))
check("错误口令不通过", not verify_password("wrong", h))
check("空口令不通过", not verify_password("", h))

from cryptography.fernet import Fernet  # noqa: E402

box = SecretBox(Fernet.generate_key().decode())
secret = "学校密码 with 中文 & symbols!@#"
check("加密后可还原", box.decrypt(box.encrypt(secret)) == secret)
check("密文与明文不同", box.encrypt(secret) != secret)
check("空值安全", box.encrypt("") == "" and box.decrypt("") == "")
check("篡改密文返回空", box.decrypt("not-a-valid-token") == "")

signer = SessionSigner("unit-test-secret")
tok = signer.dumps({"kind": "user", "sid": "20230101"}, 3600)
loaded = signer.loads(tok)
check("会话签名可校验", bool(loaded) and loaded.get("sid") == "20230101", str(loaded))
check("篡改会话被拒绝", signer.loads(tok[:-2] + "xx") is None)
check("过期会话被拒绝", signer.loads(signer.dumps({"kind": "user"}, -1)) is None)
check("垃圾输入被拒绝", signer.loads("garbage") is None and signer.loads("") is None)
check("不同密钥不通用", SessionSigner("other").loads(tok) is None)

pw = generate_password()
check("随机口令为 6 位", len(pw) == 6, pw)
check("随机口令不含易混字符", not (set(pw) & set("0O1lI")), pw)
check("学号脱敏", mask_student_id("20230101") == "20****01", mask_student_id("20230101"))

# --------------------------------------------------------------------------- #
print("\n=== 校历判定 ===")

import os  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="autosinin-unit-"))
os.environ["SCHEDULER_DISABLED"] = "1"

from app.calendar_rules import CalendarRules  # noqa: E402
from app.db import Database  # noqa: E402

db = Database(TMP / "unit.db")
rules = CalendarRules(db)

today = dt.date(2026, 3, 10)
should, reason = rules.should_sign(today)
check(
    "未配置学期时本地校历判否（但不影响签到）",
    should is False and ("未配置学期" in reason or "不影响自动签到" in reason),
    reason,
)

db.add_term("2025-2026-2 学期", "2026-02-23", "2026-07-10")
should, reason = rules.should_sign(today)
check("学期内应签到", should is True, reason)

before = dt.date(2026, 1, 15)
should, reason = rules.should_sign(before)
check("学期外不签到", should is False and "学期" in reason, reason)

db.add_holiday("2026 寒假", "2026-01-01", "2026-02-22", "vacation")
should, reason = rules.should_sign(before)
check("寒假区间判定为假期/学期外", should is False, reason)

db.add_holiday("清明", "2026-04-04", "2026-04-06", "holiday")
should, reason = rules.should_sign(dt.date(2026, 4, 5))
check("节假日不签到", should is False and "假期中" in reason, reason)

db.add_holiday("调休上课", "2026-04-04", "2026-04-04", "makeup")
should, reason = rules.should_sign(dt.date(2026, 4, 4))
check("调休上课照常签到", should is True, reason)

month_map = rules.describe_month(2026, 1)
check("1 月整月学期外", all(v == "skip" for v in month_map.values()), str(set(month_map.values())))
month_map = rules.describe_month(2026, 5)
check("5 月有可签到日", "sign" in month_map.values(), str(set(month_map.values())))

nxt = rules.next_signable_day(dt.date(2026, 1, 1))
check("能算出下一个可签到日", nxt == dt.date(2026, 2, 23), str(nxt))

# --------------------------------------------------------------------------- #
print("\n=== 配置加载 ===")

import app.config as config  # noqa: E402

cfg_dir = TMP / "cfg"
os.environ["DATA_DIR"] = str(cfg_dir)
settings = config.load_settings()
check("自动生成 session 密钥", len(settings.secret_key) > 20)
check("自动生成 Fernet 密钥", len(settings.encryption_key) == 44 and settings.encryption_key.endswith("="))
check("配置文件已落盘", (cfg_dir / "config.json").exists())
check("DATA_DIR 环境变量生效", settings.data_dir == cfg_dir, str(settings.data_dir))

settings2 = config.load_settings()
check("重启后密钥保持稳定", settings2.secret_key == settings.secret_key and settings2.encryption_key == settings.encryption_key)
check("数据库路径落在 data_dir 下", settings.db_path.parent == cfg_dir, str(settings.db_path))

check("默认窗口为 21:00-22:30", settings.sign_window_start == "21:00" and settings.sign_window_end == "22:30")

# persist 不应递归（单例未初始化时也要能用）
config._settings = None  # noqa: SLF001 - 专测这个边界
config.persist("admin_username", "admin", data_dir=cfg_dir)
reloaded = json.loads((cfg_dir / "config.json").read_text(encoding="utf-8"))
check("persist 可独立工作且不递归", reloaded.get("admin_username") == "admin", str(reloaded.get("admin_username")))

# --------------------------------------------------------------------------- #
print("\n=== 客户端构造 ===")

c = FlySourceClient(timeout=5)
check("默认 base url", c.base_url == "https://simp.csuft.edu.cn", c.base_url)
check("默认 tenant", c.tenant_id == "000000", c.tenant_id)
check(
    "默认使用网页端 client（支持 password 授权）",
    c.client_id == "flySource",
    c.client_id,
)
hdr = c._headers(auth=True, token="TOK")  # noqa: SLF001 - 单元测试直接验证内部签名头
check("auth 头包含 token", hdr.get("FlySource-Auth") == "TOK", str(hdr))
check("auth 头包含 sign", "FlySource-sign" in hdr and len(hdr["FlySource-sign"]) > 20)
check("始终带 Basic", hdr.get("Authorization", "").startswith("Basic "))
c.close()

c2 = FlySourceClient("https://example.com/", timeout=5)
check("base url 去尾部斜杠", c2.base_url == "https://example.com", c2.base_url)
c2.close()

# --------------------------------------------------------------------------- #
print("\n" + "=" * 70)
print(f"通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
if FAILED:
    print("\n失败项：")
    for item in FAILED:
        print("  -", item)
print("=" * 70)
raise SystemExit(1 if FAILED else 0)
