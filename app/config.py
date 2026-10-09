"""应用配置。

配置来源优先级：环境变量 > ``data/config.json`` > 内置默认值。
首次启动时会自动生成随机的会话密钥与加密密钥并写入 ``data/config.json``，
因此不需要用户手工准备密钥material。
"""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
CONFIG_PATH = DATA_DIR / "config.json"

# 签到时间窗口（本地时间），默认 21:00 - 22:30
DEFAULT_SIGN_WINDOW_START = "21:00"
DEFAULT_SIGN_WINDOW_END = "22:30"

# 宿舍定位允许的默认误差（米），任务未配置时使用
DEFAULT_LOCATION_ACCURACY = 30.0


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env(name).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    """运行期配置。"""

    # 站点
    site_name: str = "中南林学工自动签到"
    base_url: str = ""

    # 学校接口
    flysource_base_url: str = "https://simp.csuft.edu.cn"
    tenant_id: str = "000000"
    verify_tls: bool = True
    request_timeout: float = 30.0

    # 签到时间窗口
    sign_window_start: str = DEFAULT_SIGN_WINDOW_START
    sign_window_end: str = DEFAULT_SIGN_WINDOW_END
    location_accuracy: float = DEFAULT_LOCATION_ACCURACY

    # 调度
    scheduler_enabled: bool = True
    timezone: str = "Asia/Shanghai"

    # 安全
    secret_key: str = ""
    encryption_key: str = ""

    # 管理员（唯一账号）
    admin_username: str = ""
    admin_password_hash: str = ""

    # 数据
    data_dir: Path = field(default_factory=lambda: DATA_DIR)

    # ------------------------------------------------------------------ #

    @property
    def db_path(self) -> Path:
        return self.data_dir / "app.db"

    @property
    def config_path(self) -> Path:
        return self.data_dir / "config.json"

    def is_admin_configured(self) -> bool:
        return bool(self.admin_username and self.admin_password_hash)

    def as_public_dict(self) -> dict[str, Any]:
        """可安全返回给前端的配置片段。"""
        return {
            "siteName": self.site_name,
            "signWindowStart": self.sign_window_start,
            "signWindowEnd": self.sign_window_end,
            "schedulerEnabled": self.scheduler_enabled,
            "timezone": self.timezone,
            "adminConfigured": self.is_admin_configured(),
        }


def _load_file_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_file_config(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    tmp.replace(path)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def load_settings() -> Settings:
    """读取配置；缺失的密钥会自动生成并持久化。"""
    # 数据目录可由环境变量覆盖（systemd 单元会设置它）
    data_dir = Path(_env("DATA_DIR") or DATA_DIR).expanduser()
    data_dir.mkdir(parents=True, exist_ok=True)
    config_path = data_dir / "config.json"
    stored = _load_file_config(config_path)

    def pick(key: str, env_name: str, default: Any) -> Any:
        env_value = _env(env_name)
        if env_value:
            return env_value
        if key in stored and stored[key] not in (None, ""):
            return stored[key]
        return default

    settings = Settings(
        site_name=pick("site_name", "SITE_NAME", "中南林学工自动签到"),
        base_url=pick("base_url", "BASE_URL", ""),
        flysource_base_url=pick(
            "flysource_base_url", "FLYSOURCE_BASE_URL", "https://simp.csuft.edu.cn"
        ),
        tenant_id=pick("tenant_id", "FLYSOURCE_TENANT_ID", "000000"),
        verify_tls=not _env_bool("FLYSOURCE_INSECURE", False),
        request_timeout=float(_env_int("REQUEST_TIMEOUT", 30)),
        sign_window_start=pick("sign_window_start", "SIGN_WINDOW_START", DEFAULT_SIGN_WINDOW_START),
        sign_window_end=pick("sign_window_end", "SIGN_WINDOW_END", DEFAULT_SIGN_WINDOW_END),
        location_accuracy=float(pick("location_accuracy", "LOCATION_ACCURACY", DEFAULT_LOCATION_ACCURACY)),
        scheduler_enabled=not _env_bool("SCHEDULER_DISABLED", False),
        timezone=pick("timezone", "TIMEZONE", "Asia/Shanghai"),
        secret_key=str(pick("secret_key", "SECRET_KEY", "")),
        encryption_key=str(pick("encryption_key", "ENCRYPTION_KEY", "")),
        admin_username=str(pick("admin_username", "ADMIN_USERNAME", "")),
        admin_password_hash=str(pick("admin_password_hash", "ADMIN_PASSWORD_HASH", "")),
        data_dir=data_dir,
    )

    # 自动补齐密钥
    changed = False
    if not settings.secret_key:
        settings.secret_key = secrets.token_urlsafe(48)
        changed = True
    if not settings.encryption_key:
        # Fernet 需要 32 字节 urlsafe base64 密钥
        from cryptography.fernet import Fernet

        settings.encryption_key = Fernet.generate_key().decode("ascii")
        changed = True

    if changed:
        stored.update(
            {
                "secret_key": settings.secret_key,
                "encryption_key": settings.encryption_key,
            }
        )
        _save_file_config(config_path, stored)

    return settings


def persist(key: str, value: Any, data_dir: Path | None = None) -> None:
    """把单个配置项写回 ``config.json``。

    注意不要走 ``get_settings()``，否则在单例尚未初始化时会递归触发
    ``load_settings``（后者又可能调用本函数）。
    """
    if data_dir is None:
        if _settings is not None:
            data_dir = _settings.data_dir
        else:
            data_dir = Path(_env("DATA_DIR") or DATA_DIR).expanduser()
    path = data_dir / "config.json"
    stored = _load_file_config(path)
    stored[key] = value
    _save_file_config(path, stored)


_settings: Settings | None = None


def get_settings(refresh: bool = False) -> Settings:
    """进程内单例配置。"""
    global _settings
    if _settings is None or refresh:
        _settings = load_settings()
    return _settings
