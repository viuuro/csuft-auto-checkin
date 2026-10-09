"""SQLite 数据访问层。

表结构：
  users          —— 学生用户（申请、审批、凭据、计划签到时刻）
  terms          —— 学期
  holidays       —— 假期区间（寒暑假等，跳过自动签到）
  sign_records   —— 每日签到结果（日历看板数据源）
  sign_logs      —— 操作与调度日志
  settings       —— 键值配置
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id          TEXT    NOT NULL UNIQUE,
    name                TEXT    NOT NULL DEFAULT '',
    phone               TEXT    NOT NULL DEFAULT '',
    remark              TEXT    NOT NULL DEFAULT '',
    major               TEXT    NOT NULL DEFAULT '',

    approval_state      TEXT    NOT NULL DEFAULT 'pending',
    reject_reason       TEXT    NOT NULL DEFAULT '',
    approved_at         TEXT,
    approved_by         TEXT    NOT NULL DEFAULT '',
    enabled             INTEGER NOT NULL DEFAULT 1,
    trust_state         TEXT    NOT NULL DEFAULT 'unbound',

    query_password_hash TEXT    NOT NULL DEFAULT '',
    credential_enc      TEXT    NOT NULL DEFAULT '',
    token_enc           TEXT    NOT NULL DEFAULT '',
    refresh_token_enc   TEXT    NOT NULL DEFAULT '',
    token_saved_at      TEXT,
    token_refreshed_at  TEXT,
    credential_ok       INTEGER NOT NULL DEFAULT 0,
    last_error          TEXT    NOT NULL DEFAULT '',

    plan_sign_time      TEXT    NOT NULL DEFAULT '',
    task_id             TEXT    NOT NULL DEFAULT '',
    task_name           TEXT    NOT NULL DEFAULT '',
    dorm_name           TEXT    NOT NULL DEFAULT '',
    room_no             TEXT    NOT NULL DEFAULT '',
    location_lat        REAL,
    location_lng        REAL,
    location_accuracy   REAL,

    created_at          TEXT    NOT NULL DEFAULT '',
    updated_at          TEXT    NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_users_approval ON users(approval_state);

CREATE TABLE IF NOT EXISTS terms (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    start_date  TEXT    NOT NULL,
    end_date    TEXT    NOT NULL,
    note        TEXT    NOT NULL DEFAULT '',
    created_at  TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS holidays (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    start_date  TEXT    NOT NULL,
    end_date    TEXT    NOT NULL,
    kind        TEXT    NOT NULL DEFAULT 'vacation',
    note        TEXT    NOT NULL DEFAULT '',
    created_at  TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS sign_records (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id  TEXT    NOT NULL,
    sign_date   TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'pending',
    message     TEXT    NOT NULL DEFAULT '',
    sign_status INTEGER,
    sign_time   TEXT    NOT NULL DEFAULT '',
    source      TEXT    NOT NULL DEFAULT 'auto',
    attempts    INTEGER NOT NULL DEFAULT 1,
    updated_at  TEXT    NOT NULL DEFAULT '',
    UNIQUE(student_id, sign_date)
);

CREATE INDEX IF NOT EXISTS idx_records_sid_date ON sign_records(student_id, sign_date);

CREATE TABLE IF NOT EXISTS sign_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id  TEXT    NOT NULL DEFAULT '',
    action      TEXT    NOT NULL DEFAULT '',
    level       TEXT    NOT NULL DEFAULT 'info',
    message     TEXT    NOT NULL DEFAULT '',
    detail      TEXT    NOT NULL DEFAULT '',
    created_at  TEXT    NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_logs_created ON sign_logs(created_at DESC);

CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL DEFAULT '',
    updated_at  TEXT NOT NULL DEFAULT ''
);
"""


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


class Database:
    """极简 SQLite 封装：短连接 + 线程锁，够用且无需连接池。"""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_schema()

    # -- 基础设施 ---------------------------------------------------------- #

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    @contextmanager
    def cursor(self) -> Iterator[sqlite3.Cursor]:
        with self._lock:
            conn = self.connect()
            try:
                yield conn.cursor()
            finally:
                conn.close()

    def _init_schema(self) -> None:
        with self._lock:
            conn = self.connect()
            try:
                conn.executescript(SCHEMA)
                self._migrate(conn)
            finally:
                conn.close()

    # 后续新增的列在这里做增量迁移，保证老数据库不用重建
    _MIGRATIONS: tuple[tuple[str, str, str], ...] = (
        ("users", "refresh_token_enc", "TEXT NOT NULL DEFAULT ''"),
        ("users", "token_refreshed_at", "TEXT"),
    )

    def _migrate(self, conn: sqlite3.Connection) -> None:
        """给已存在的数据库补上新增列（SQLite 不支持 IF NOT EXISTS 加列）。"""
        for table, column, decl in self._MIGRATIONS:
            try:
                cols = {
                    row[1]
                    for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
                }
            except sqlite3.Error:
                continue
            if column in cols:
                continue
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
            except sqlite3.Error:
                # 迁移失败不应阻止启动，后续读写会暴露问题
                pass

    # -- 通用查询 ---------------------------------------------------------- #

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self.cursor() as cur:
            cur.execute(sql, params)
            return [dict(row) for row in cur.fetchall()]

    def query_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        with self.cursor() as cur:
            cur.execute(sql, params)
            return cur.lastrowid or cur.rowcount

    def executemany(self, sql: str, seq: list[tuple[Any, ...]]) -> None:
        if not seq:
            return
        with self.cursor() as cur:
            cur.executemany(sql, seq)

    # -- 日志 -------------------------------------------------------------- #

    def log(
        self,
        action: str,
        message: str,
        *,
        student_id: str = "",
        level: str = "info",
        detail: Any = None,
    ) -> None:
        detail_text = ""
        if detail is not None:
            detail_text = (
                detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False)
            )[:4000]
        self.execute(
            """
            INSERT INTO sign_logs(student_id, action, level, message, detail, created_at)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            (student_id, action, level, message[:1000], detail_text, now_iso()),
        )

    def recent_logs(self, limit: int = 200, student_id: str = "") -> list[dict[str, Any]]:
        if student_id:
            return self.query(
                "SELECT * FROM sign_logs WHERE student_id = ? ORDER BY id DESC LIMIT ?",
                (student_id, limit),
            )
        return self.query("SELECT * FROM sign_logs ORDER BY id DESC LIMIT ?", (limit,))

    # -- 用户 -------------------------------------------------------------- #

    def get_user(self, student_id: str) -> dict[str, Any] | None:
        return self.query_one("SELECT * FROM users WHERE student_id = ?", (str(student_id),))

    def list_users(self, approval_state: str = "") -> list[dict[str, Any]]:
        if approval_state:
            return self.query(
                "SELECT * FROM users WHERE approval_state = ? ORDER BY id DESC",
                (approval_state,),
            )
        return self.query("SELECT * FROM users ORDER BY id DESC")

    def list_signable_users(self) -> list[dict[str, Any]]:
        """已批准、已启用、且**持有可用凭据**的用户。

        凭据有两种模式，满足其一即可：

        * ``token_enc``   —— 通过抓包采集的会话（同学没有密码）
        * ``credential_enc`` —— 网页绑定的账号密码

        注意不要把查询条件写成"必须有密码"，否则抓包录入的用户
        永远不会被调度器选中。
        """
        return self.query(
            """
            SELECT * FROM users
            WHERE approval_state = 'approved'
              AND enabled = 1
              AND (token_enc <> '' OR credential_enc <> '')
            ORDER BY id
            """
        )

    def create_user(
        self,
        student_id: str,
        *,
        name: str = "",
        phone: str = "",
        remark: str = "",
        major: str = "",
        approval_state: str = "pending",
        credential_enc: str = "",
        token_enc: str = "",
        refresh_token_enc: str = "",
        credential_ok: int = 0,
        trust_state: str = "unbound",
    ) -> int:
        stamp = now_iso()
        return self.execute(
            """
            INSERT INTO users(student_id, name, phone, remark, major, approval_state,
                              credential_enc, token_enc, refresh_token_enc,
                              credential_ok, trust_state,
                              token_saved_at, created_at, updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                str(student_id), name, phone, remark, major, approval_state,
                credential_enc, token_enc, refresh_token_enc, credential_ok, trust_state,
                stamp if token_enc else None, stamp, stamp,
            ),
        )

    def update_user(self, student_id: str, **fields: Any) -> None:
        if not fields:
            return
        allowed = {
            "name", "phone", "remark", "major", "approval_state", "reject_reason",
            "approved_at", "approved_by", "enabled", "trust_state", "query_password_hash",
            "credential_enc", "token_enc", "refresh_token_enc", "token_saved_at",
            "token_refreshed_at", "credential_ok", "last_error",
            "plan_sign_time", "task_id", "task_name", "dorm_name", "room_no",
            "location_lat", "location_lng", "location_accuracy",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"不允许更新的字段: {sorted(unknown)}")
        fields["updated_at"] = now_iso()
        assignments = ", ".join(f"{key} = ?" for key in fields)
        params = tuple(fields.values()) + (str(student_id),)
        self.execute(f"UPDATE users SET {assignments} WHERE student_id = ?", params)

    def delete_user(self, student_id: str) -> None:
        self.execute("DELETE FROM users WHERE student_id = ?", (str(student_id),))
        self.execute("DELETE FROM sign_records WHERE student_id = ?", (str(student_id),))

    # -- 签到记录 ---------------------------------------------------------- #

    def upsert_record(
        self,
        student_id: str,
        sign_date: str,
        *,
        status: str,
        message: str = "",
        sign_status: int | None = None,
        sign_time: str = "",
        source: str = "auto",
        attempts: int | None = None,
    ) -> None:
        self.execute(
            """
            INSERT INTO sign_records(student_id, sign_date, status, message,
                                     sign_status, sign_time, source, attempts, updated_at)
            VALUES(?,?,?,?,?,?,?,?,?)
            ON CONFLICT(student_id, sign_date) DO UPDATE SET
                status      = excluded.status,
                message     = excluded.message,
                sign_status = COALESCE(excluded.sign_status, sign_records.sign_status),
                sign_time   = CASE WHEN excluded.sign_time <> '' THEN excluded.sign_time
                                   ELSE sign_records.sign_time END,
                source      = excluded.source,
                attempts    = COALESCE(excluded.attempts, sign_records.attempts + 1),
                updated_at  = excluded.updated_at
            """,
            (
                str(student_id), sign_date, status, message[:500], sign_status,
                sign_time, source, attempts if attempts is not None else 1, now_iso(),
            ),
        )

    def get_record(self, student_id: str, sign_date: str) -> dict[str, Any] | None:
        return self.query_one(
            "SELECT * FROM sign_records WHERE student_id = ? AND sign_date = ?",
            (str(student_id), sign_date),
        )

    def records_between(
        self, student_id: str, start: date, end: date
    ) -> list[dict[str, Any]]:
        return self.query(
            """
            SELECT * FROM sign_records
            WHERE student_id = ? AND sign_date BETWEEN ? AND ?
            ORDER BY sign_date
            """,
            (str(student_id), start.isoformat(), end.isoformat()),
        )

    def record_map(self, student_id: str, start: date, end: date) -> dict[str, dict[str, Any]]:
        return {
            row["sign_date"]: row
            for row in self.records_between(student_id, start, end)
        }

    def stats(self, today: date) -> dict[str, int]:
        today_s = today.isoformat()
        total = self.query_one("SELECT COUNT(*) AS n FROM users") or {"n": 0}
        approved = self.query_one(
            "SELECT COUNT(*) AS n FROM users WHERE approval_state = 'approved'"
        ) or {"n": 0}
        pending = self.query_one(
            "SELECT COUNT(*) AS n FROM users WHERE approval_state = 'pending'"
        ) or {"n": 0}
        success_today = self.query_one(
            "SELECT COUNT(*) AS n FROM sign_records WHERE sign_date = ? AND status = 'success'",
            (today_s,),
        ) or {"n": 0}
        failed_today = self.query_one(
            "SELECT COUNT(*) AS n FROM sign_records WHERE sign_date = ? AND status = 'failed'",
            (today_s,),
        ) or {"n": 0}
        return {
            "totalUsers": int(total["n"]),
            "approvedUsers": int(approved["n"]),
            "pendingUsers": int(pending["n"]),
            "successToday": int(success_today["n"]),
            "failedToday": int(failed_today["n"]),
        }

    # -- 学期 / 假期 -------------------------------------------------------- #

    def list_terms(self) -> list[dict[str, Any]]:
        return self.query("SELECT * FROM terms ORDER BY start_date DESC")

    def add_term(self, name: str, start_date: str, end_date: str, note: str = "") -> int:
        return self.execute(
            """
            INSERT INTO terms(name, start_date, end_date, note, created_at)
            VALUES(?,?,?,?,?)
            """,
            (name, start_date, end_date, note, now_iso()),
        )

    def delete_term(self, term_id: int) -> None:
        self.execute("DELETE FROM terms WHERE id = ?", (int(term_id),))

    def list_holidays(self) -> list[dict[str, Any]]:
        return self.query("SELECT * FROM holidays ORDER BY start_date DESC")

    def add_holiday(
        self, name: str, start_date: str, end_date: str, kind: str = "vacation", note: str = ""
    ) -> int:
        return self.execute(
            """
            INSERT INTO holidays(name, start_date, end_date, kind, note, created_at)
            VALUES(?,?,?,?,?,?)
            """,
            (name, start_date, end_date, kind, note, now_iso()),
        )

    def delete_holiday(self, holiday_id: int) -> None:
        self.execute("DELETE FROM holidays WHERE id = ?", (int(holiday_id),))

    def holidays_on(self, day: date) -> list[dict[str, Any]]:
        day_s = day.isoformat()
        return self.query(
            "SELECT * FROM holidays WHERE start_date <= ? AND end_date >= ?",
            (day_s, day_s),
        )

    def terms_on(self, day: date) -> list[dict[str, Any]]:
        day_s = day.isoformat()
        return self.query(
            "SELECT * FROM terms WHERE start_date <= ? AND end_date >= ?",
            (day_s, day_s),
        )


# --------------------------------------------------------------------------- #
# 日期工具
# --------------------------------------------------------------------------- #


def parse_date(value: str) -> date | None:
    if not value:
        return None
    text = str(value).strip()[:10]
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def parse_time(value: str) -> time | None:
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(text, fmt).time()
        except ValueError:
            continue
    return None


def combine(day: date, moment: time) -> datetime:
    return datetime.combine(day, moment)


def month_bounds(year: int, month: int) -> tuple[date, date]:
    start = date(year, month, 1)
    if month == 12:
        end = date(year, 12, 31)
    else:
        end = date(year, month + 1, 1) - timedelta(days=1)
    return start, end
