"""API key 加密工具 — Fernet (AES128 + HMAC)。

设计：
- 密钥派生：``ENCRYPTION_KEY`` 环境变量（≥32 字符）经 SHA256 → base64 喂给 Fernet
- ``encrypt_key(plain: str) -> bytes`` — 返回可直接存 SQLAlchemy LargeBinary
- ``decrypt_key(blob: bytes) -> str`` — 失败抛 ``ValueError``（绝不静默）
- 启动时缓存 Fernet 实例（``@lru_cache``）— 单进程只初始化一次
- 切换 ``ENCRYPTION_KEY`` 后必须清缓存（``_get_fernet.cache_clear()``）
"""
from __future__ import annotations

import base64
import hashlib
import os
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken


_MIN_KEY_LEN = 32


class CryptoError(Exception):
    """加密/解密错误基类。"""


def _validate_key(passphrase: str) -> str:
    if len(passphrase) < _MIN_KEY_LEN:
        raise ValueError(
            f"ENCRYPTION_KEY must be >= {_MIN_KEY_LEN} chars, got {len(passphrase)}. "
            "Generate one with: python -c 'import secrets; print(secrets.token_urlsafe(32))'"
        )
    return passphrase


def _passphrase_to_fernet_key(passphrase: str) -> bytes:
    """从口令派生 32 字节 base64 编码的 Fernet key（确定性映射）。"""
    digest = hashlib.sha256(passphrase.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


@lru_cache(maxsize=1)
def _get_fernet() -> Fernet:
    """从环境变量获取 Fernet 实例（首次调用时初始化，进程内缓存）。"""
    passphrase = os.environ.get("ENCRYPTION_KEY", "")
    _validate_key(passphrase)
    return Fernet(_passphrase_to_fernet_key(passphrase))


def encrypt_key(plain: str) -> bytes:
    """加密 API key 明文 → bytes（密文）。"""
    if not isinstance(plain, str):
        raise TypeError(f"plain must be str, got {type(plain).__name__}")
    return _get_fernet().encrypt(plain.encode("utf-8"))


def decrypt_key(blob: bytes) -> str:
    """解密 API key 密文 → 明文。错误 key / 损坏密文时抛 ``ValueError``。"""
    if not isinstance(blob, (bytes, bytearray)):
        raise TypeError(f"blob must be bytes, got {type(blob).__name__}")
    try:
        return _get_fernet().decrypt(bytes(blob)).decode("utf-8")
    except InvalidToken as e:
        raise ValueError(
            "Invalid ENCRYPTION_KEY or corrupted ciphertext — cannot decrypt"
        ) from e
