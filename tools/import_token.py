#!/usr/bin/env python3
"""把本机抓到的同学授权导入服务器。

场景：`token_catcher.py` 必须在有微信的电脑上跑（服务器装不了微信），
因此本机负责采集，本工具把采集结果安全地送到服务器的数据库里。

用法::

    python tools/import_token.py --host <你的服务器地址> --user root --student 20240001
    python tools/import_token.py --host <你的服务器地址> --user root --all

实现要点
--------
不使用 heredoc —— 脚本本身是通过 stdin 传给 ``bash -s`` 的，
``cat > file`` 之类的命令会把脚本剩下的部分一起吞掉。
因此这里改为两步：先 scp 一个独立的导入脚本，再 scp 一个 JSON 数据文件，
最后执行脚本。
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from app.config import get_settings  # noqa: E402
from app.db import Database  # noqa: E402
from app.flysource import token_days_left  # noqa: E402
from app.security import SecretBox  # noqa: E402

REMOTE_DIR = "/opt/autosinin"
REMOTE_SCRIPT = "/tmp/asi_import_runner.py"
REMOTE_DATA = "/tmp/asi_import.json"

# 远端导入脚本：独立文件，通过 scp 上传，避免 heredoc 吞掉 stdin
RUNNER = '''#!/usr/bin/env python3
"""远端导入器（由 import_token.py 上传后执行）。"""
import json
import sys

sys.path.insert(0, "/opt/autosinin")
sys.stdout.reconfigure(encoding="utf-8")

from app.config import get_settings
from app.db import Database
from app.flysource import (
    WX_CLIENT_ID, WX_CLIENT_SECRET, FlySourceClient, token_days_left,
)
from app.security import SecretBox, generate_password, hash_password
from app.services import SignInService

DATA_PATH = sys.argv[1]

with open(DATA_PATH, encoding="utf-8") as fh:
    records = json.load(fh)

settings = get_settings(refresh=True)
db = Database(settings.db_path)
service = SignInService(db, settings)
vault = SecretBox(settings.encryption_key)

ok = fail = 0
credentials = []
details = []

for rec in records:
    sid = rec["studentId"]
    token = rec["token"]
    refresh = rec.get("refreshToken") or ""

    # 1) 校验 token 真的能用（拉一次任务）
    client = FlySourceClient(
        client_id=WX_CLIENT_ID,
        client_secret=WX_CLIENT_SECRET,
        timeout=settings.request_timeout,
        verify_tls=settings.verify_tls,
    )
    client.access_token = token
    try:
        tasks = client.list_tasks()
        if not tasks:
            raise RuntimeError("拉不到打卡任务")
        task = client.get_task(tasks[0].task_id)
    except Exception as exc:
        details.append(f"  x {sid} 校验失败：{exc}")
        fail += 1
        try:
            client.close()
        except Exception:
            pass
        continue
    else:
        try:
            client.close()
        except Exception:
            pass

    # 2) 建档 / 更新
    existing = db.get_user(sid)
    if existing is None:
        db.create_user(
            sid,
            name=rec.get("name", ""),
            phone=rec.get("phone", ""),
            major=rec.get("major", ""),
            remark=rec.get("remark", ""),
            approval_state="approved",
            trust_state="trusted",
        )

    fields = dict(
        name=rec.get("name") or (existing or {}).get("name") or "",
        token_enc=vault.encrypt(token),
        refresh_token_enc=vault.encrypt(refresh) if refresh else "",
        credential_enc="",
        credential_ok=1,
        trust_state="trusted",
        approval_state="approved",
        enabled=1,
        last_error="",
        task_id=task.task_id,
        task_name=task.task_name,
        dorm_name=task.dorm_name,
        room_no=task.room_no,
        location_lat=task.location_lat,
        location_lng=task.location_lng,
        location_accuracy=task.location_accuracy or settings.location_accuracy,
    )
    if rec.get("planSignTime"):
        fields["plan_sign_time"] = rec["planSignTime"]
    db.update_user(sid, **fields)

    # 3) 没有查询口令就生成一个
    user = db.get_user(sid) or {}
    if not user.get("query_password_hash"):
        pw = generate_password()
        db.update_user(sid, query_password_hash=hash_password(pw))
        credentials.append((sid, pw))
    else:
        credentials.append((sid, None))

    renew = "可自动续期" if refresh else "无续期凭据"
    db.log(
        "token_imported",
        f"从本机导入 {sid}（{rec.get('name') or '未填姓名'}）的授权，{renew}",
        student_id=sid,
    )
    details.append(
        f"  v {sid}  {task.task_name}  {task.sign_start_time}~{task.sign_end_time}  {renew}"
    )
    ok += 1

# 未分配签到时刻的补一个随机值
need = [
    r["studentId"]
    for r in records
    if not (db.get_user(r["studentId"]) or {}).get("plan_sign_time")
]
if need:
    n = service.randomize_plan_times(need)
    details.append(f"  已为 {n} 位分配随机签到时刻")

for line in details:
    print(line)
print()
print("IMPORT_RESULT")
print(json.dumps(
    {
        "ok": ok,
        "fail": fail,
        "credentials": [{"studentId": s, "password": p} for s, p in credentials],
    },
    ensure_ascii=False,
))
'''


def hr(t: str) -> None:
    print("\n" + "=" * 74)
    print(t)
    print("=" * 74)


def build_records(student_ids: list[str], json_file: str = "") -> list[dict]:
    """取出待导入的凭据。

    两种来源：

    * ``--from-json``：直接读 ``read_token_file.py --save-to`` 生成的 JSON
      （推荐 —— 不需要先把 token 写进本机数据库）
    * 否则从本机数据库读取已采集的账号
    """
    if json_file:
        path = Path(json_file)
        if not path.exists():
            print(f"  ✗ 文件不存在：{path}")
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"  ✗ 读取失败：{exc}")
            return []
        if not isinstance(data, list):
            data = [data]
        print(f"  待导入 {len(data)} 位（来自 {path.name}）：")
        for rec in data:
            left = rec.get("tokenDaysLeft")
            renew = "可自动续期" if rec.get("refreshToken") else "无续期凭据"
            print(f"    {rec.get('studentId')}  {rec.get('name') or '(未填姓名)'}"
                  f"  剩余 {left if left is not None else '未知'} 天  {renew}")
        return data

    settings = get_settings(refresh=True)
    db = Database(settings.db_path)
    vault = SecretBox(settings.encryption_key)

    users = db.list_users()
    if not student_ids:
        student_ids = [
            u["student_id"] for u in users if vault.decrypt(u.get("token_enc") or "")
        ]
    if not student_ids:
        print("本机没有可导入的账号。请先运行：")
        print("  python tools/token_catcher.py 学号")
        return []

    wanted = set(student_ids)
    out: list[dict] = []
    for user in users:
        sid = str(user["student_id"])
        if sid not in wanted:
            continue
        token = vault.decrypt(user.get("token_enc") or "")
        refresh = vault.decrypt(user.get("refresh_token_enc") or "")
        if not token:
            print(f"  跳过 {sid}：本机没有会话凭据")
            continue
        days = token_days_left(token)
        out.append(
            {
                "studentId": sid,
                "name": user.get("name") or "",
                "phone": user.get("phone") or "",
                "major": user.get("major") or "",
                "remark": user.get("remark") or "",
                "token": token,
                "refreshToken": refresh,
                "planSignTime": user.get("plan_sign_time") or "",
                "tokenDaysLeft": round(days, 1) if days is not None else None,
            }
        )

    print(f"  待导入 {len(out)} 位：")
    for rec in out:
        left = f"{rec['tokenDaysLeft']} 天" if rec["tokenDaysLeft"] is not None else "未知"
        renew = "可自动续期" if rec["refreshToken"] else "无续期凭据"
        print(f"    {rec['studentId']}  {rec['name'] or '(未填姓名)'}  剩余 {left}  {renew}")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="把本机采集的授权导入服务器")
    parser.add_argument("--host", required=True)
    parser.add_argument("--user", default="root")
    parser.add_argument("--key", default="")
    parser.add_argument("--student", action="append", default=[], help="学号，可重复")
    parser.add_argument("--all", action="store_true", help="导入本机全部已采集账号")
    parser.add_argument(
        "--from-json", default="",
        help="直接读 read_token_file.py 生成的 JSON（同学发来的授权文件）",
    )
    parser.add_argument("--password-env", default="ASI_SSH_PASSWORD")
    args = parser.parse_args()

    hr("1. 读取待导入凭据")
    if args.from_json:
        records = build_records([], args.from_json)
    else:
        student_ids = [] if args.all else args.student
        if not student_ids and not args.all:
            print("请指定 --student 学号 / --all / --from-json 文件")
            return 2
        records = build_records(student_ids)
    if not records:
        return 1

    password = ""
    if not args.key:
        password = os.environ.get(args.password_env, "")
        if not password:
            password = getpass.getpass(f"{args.user}@{args.host} 的密码: ")
        if not password:
            print("未输入密码，退出。")
            return 1

    # 准备 SSH 环境
    env = dict(os.environ)
    opts = ["-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=15"]
    if args.key:
        opts += ["-i", args.key, "-o", "IdentitiesOnly=yes"]

    askpass: Path | None = None
    if password:
        fd, name = tempfile.mkstemp(suffix=".cmd", prefix="asi-askpass-")
        os.close(fd)
        askpass = Path(name)
        askpass.write_text("@echo off\r\necho %ASI_SSH_PASSWORD%\r\n", encoding="ascii")
        env["ASI_SSH_PASSWORD"] = password
        env["SSH_ASKPASS"] = str(askpass)
        env["SSH_ASKPASS_REQUIRE"] = "force"
        env["DISPLAY"] = "localhost:0"

    target = f"{args.user}@{args.host}"
    tmp_files: list[Path] = []
    try:
        hr("2. 上传导入脚本与数据")
        # 上传 runner 脚本
        fd, runner_path = tempfile.mkstemp(suffix=".py", prefix="asi-runner-")
        os.close(fd)
        runner = Path(runner_path)
        runner.write_bytes(RUNNER.replace("\r\n", "\n").encode("utf-8"))
        tmp_files.append(runner)

        # 上传数据（含明文 token，仅走 SSH 加密通道）
        fd, data_path = tempfile.mkstemp(suffix=".json", prefix="asi-data-")
        os.close(fd)
        dataf = Path(data_path)
        dataf.write_bytes(json.dumps(records, ensure_ascii=False).encode("utf-8"))
        tmp_files.append(dataf)

        for local, remote in ((runner, REMOTE_SCRIPT), (dataf, REMOTE_DATA)):
            proc = subprocess.run(
                ["scp", *opts, str(local), f"{target}:{remote}"],
                capture_output=True, timeout=300, env=env,
            )
            if proc.returncode != 0:
                err = (proc.stderr or b"").decode("utf-8", errors="replace")
                print(f"  ✗ 上传失败：{err.strip()[:300]}")
                return 1
        print("  ✓ 已上传")

        hr("3. 在服务器上执行导入")
        cmd = (
            f"cd {REMOTE_DIR} && sudo -u autosinin .venv/bin/python "
            f"{REMOTE_SCRIPT} {REMOTE_DATA}; rc=$?; rm -f {REMOTE_SCRIPT} {REMOTE_DATA}; exit $rc"
        )
        proc = subprocess.run(
            ["ssh", *opts, target, cmd],
            capture_output=True, timeout=900, env=env,
        )
        out = (proc.stdout or b"").decode("utf-8", errors="replace")
        err = (proc.stderr or b"").decode("utf-8", errors="replace")

        for line in out.splitlines():
            if line.strip() and line.strip() != "IMPORT_RESULT":
                print("  " + line)

        if proc.returncode != 0 or "IMPORT_RESULT" not in out:
            print("\n✗ 导入失败")
            for line in err.strip().splitlines()[-15:]:
                print("  ! " + line)
            return 1

        result: dict = {}
        lines = out.splitlines()
        for i, line in enumerate(lines):
            if line.strip() == "IMPORT_RESULT" and i + 1 < len(lines):
                try:
                    result = json.loads(lines[i + 1])
                except ValueError:
                    pass
                break

        hr("导入完成")
        print(f"  成功 {result.get('ok', 0)} 位，失败 {result.get('fail', 0)} 位")
        creds = result.get("credentials") or []
        if creds:
            print("\n  查询口令（请转告对应同学，仅此一次可见）：")
            for item in creds:
                if item.get("password"):
                    print(f"    {item['studentId']}  →  {item['password']}")
        print(f"\n  管理端: http://{args.host}:8000/admin")
        return 0

    finally:
        if askpass:
            askpass.unlink(missing_ok=True)
        for f in tmp_files:
            f.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
