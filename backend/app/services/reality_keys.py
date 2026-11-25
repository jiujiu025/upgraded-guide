"""reality 密钥材料：给每条入站生成独立的 x25519 密钥对与 shortId。

为什么每条入站独立生成，而不是每台机器共用一对：
面板上的现状（2026-11-26 实测 34 条入站）就是每条 privateKey 都不同。共用一对的
问题是——一条被墙，同机所有客户一起挂；而且客户端链接里的 pbk 全都一样，
出问题时没法按客户定位。

生成结果与 `xray x25519` 完全一致：32 字节原始私钥/公钥，base64url 去 padding，
43 个字符。shortId 取 8 位 hex（xray 要求 0-16 位、偶数长度）。
"""
from __future__ import annotations

import base64
import secrets

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

SHORT_ID_BYTES = 4


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def generate_reality_keypair() -> tuple[str, str]:
    """返回 (private_key, public_key)，两者都是面板/客户端直接可用的字符串。"""
    private = X25519PrivateKey.generate()
    raw_private = private.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    raw_public = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return _b64url(raw_private), _b64url(raw_public)


def generate_short_id() -> str:
    return secrets.token_hex(SHORT_ID_BYTES)


def public_key_from_private(private_key: str) -> str | None:
    """面板只回了私钥时，用它算出客户端要的 pbk。失败返回 None，不猜。"""
    try:
        padded = private_key + "=" * (-len(private_key) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        key = X25519PrivateKey.from_private_bytes(raw)
    except (ValueError, TypeError):
        return None
    raw_public = key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return _b64url(raw_public)
