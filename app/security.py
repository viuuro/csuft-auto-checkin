"""安全原语：口令哈希、敏感字段加密、签名会话 Cookie。

- 管理员口令、用户查询口令：bcrypt 哈希后存储。
- 学校账号密码与 access_token：Fernet 对称加密后存储（密钥在 data/config.json）。
- 浏览器会话：HMAC-SHA256 签名的 Cookie，无需额外依赖。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

import bcrypt
from cryptography.fernet import Fernet, InvalidToken

# --------------------------------------------------------------------------- #
# 口令哈希
# --------------------------------------------------------------------------- #


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("ascii")


def verify_password(password: str, hashed: str) -> bool:
    if not password or not hashed:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("ascii"))
    except (ValueError, TypeError):
        return False


# --------------------------------------------------------------------------- #
# 对称加密
# --------------------------------------------------------------------------- #


class SecretBox:
    """基于 Fernet 的字段级加密。"""

    def __init__(self, key: str) -> None:
        if not key:
            raise ValueError("encryption_key 未配置")
        self._fernet = Fernet(key.encode("ascii") if isinstance(key, str) else key)

    def encrypt(self, plaintext: str | None) -> str:
        if not plaintext:
            return ""
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, token: str | None) -> str:
        if not token:
            return ""
        try:
            return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError):
            return ""


# --------------------------------------------------------------------------- #
# 会话 Cookie（HMAC 签名）
# --------------------------------------------------------------------------- #


class SessionSigner:
    """生成/校验 ``payload.signature`` 形式的会话值。"""

    def __init__(self, secret: str) -> None:
        if not secret:
            raise ValueError("secret_key 未配置")
        self._secret = secret.encode("utf-8")

    def _sign(self, payload: bytes) -> str:
        digest = hmac.new(self._secret, payload, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")

    def dumps(self, data: dict[str, Any], ttl_seconds: int) -> str:
        body = dict(data)
        body["exp"] = int(time.time()) + int(ttl_seconds)
        raw = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        encoded = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
        return f"{encoded}.{self._sign(raw)}"

    def loads(self, token: str | None) -> dict[str, Any] | None:
        if not token or "." not in token:
            return None
        encoded, signature = token.rsplit(".", 1)
        try:
            padding = "=" * (-len(encoded) % 4)
            raw = base64.urlsafe_b64decode(encoded + padding)
        except (ValueError, TypeError):
            return None
        if not hmac.compare_digest(self._sign(raw), signature):
            return None
        try:
            data = json.loads(raw.decode("utf-8"))
        except ValueError:
            return None
        if not isinstance(data, dict):
            return None
        if int(data.get("exp", 0)) < int(time.time()):
            return None
        return data


# --------------------------------------------------------------------------- #
# 随机标识
# --------------------------------------------------------------------------- #


def generate_password(length: int = 6) -> str:
    """生成用户查询口令。

    去掉了容易混淆的字符（0/O、1/l/I），方便同学手抄。
    """
    alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def mask_student_id(student_id: str) -> str:
    """学号脱敏展示。"""
    text = str(student_id or "")
    if len(text) <= 4:
        return text
    return text[:2] + "*" * (len(text) - 4) + text[-2:]
