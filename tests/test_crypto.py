"""Fernet API key 加密工具测试。"""
import os
import pytest


@pytest.fixture(autouse=True)
def _setup_env(monkeypatch):
    """每个测试都设置一个固定的 ENCRYPTION_KEY。"""
    monkeypatch.setenv("ENCRYPTION_KEY", "test-passphrase-32-chars-minimum-xyz")


def test_encrypt_decrypt_roundtrip():
    """明文 → 密文 → 明文 应一致。"""
    from backend.core.crypto import encrypt_key, decrypt_key
    plain = "sk-test-1234567890abcdef"
    enc = encrypt_key(plain)
    assert enc != plain.encode()
    assert decrypt_key(enc) == plain


def test_different_plaintext_different_ciphertext():
    """不同明文应产生不同密文（概率性，Fernet 随机 IV）。"""
    from backend.core.crypto import encrypt_key
    a = encrypt_key("aaa")
    b = encrypt_key("bbb")
    assert a != b


def test_ciphertext_is_bytes():
    """密文应是 bytes（直接对应 SQLAlchemy LargeBinary）。"""
    from backend.core.crypto import encrypt_key
    enc = encrypt_key("hello")
    assert isinstance(enc, bytes)


def test_short_key_raises():
    """ENCRYPTION_KEY 太短应抛错。"""
    from backend.core import crypto
    crypto._get_fernet.cache_clear()
    old = os.environ.get("ENCRYPTION_KEY", "")
    os.environ["ENCRYPTION_KEY"] = "short"
    try:
        with pytest.raises(ValueError, match="ENCRYPTION_KEY"):
            crypto._get_fernet()
    finally:
        os.environ["ENCRYPTION_KEY"] = old
        crypto._get_fernet.cache_clear()


def test_wrong_key_cannot_decrypt():
    """用错 key 解密应抛 ValueError。"""
    from backend.core import crypto
    # 用 key A 加密
    os.environ["ENCRYPTION_KEY"] = "key-a-32-chars-padding-padding-xx"
    crypto._get_fernet.cache_clear()
    enc = crypto.encrypt_key("secret")
    # 切到 key B
    os.environ["ENCRYPTION_KEY"] = "key-b-32-chars-padding-padding-yy"
    crypto._get_fernet.cache_clear()
    with pytest.raises(ValueError, match="Invalid"):
        crypto.decrypt_key(enc)
    # 恢复
    os.environ["ENCRYPTION_KEY"] = "test-passphrase-32-chars-minimum-xyz"
    crypto._get_fernet.cache_clear()
