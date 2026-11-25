"""中转机面板凭据的加解密。

复用与供应商同一把 AES-GCM 密钥（``SUPPLIER_SECRET_ENCRYPTION_KEY``），
但 AAD 换成 ``transit:{机器名}:{用途}``：这样把 A 机的密文贴到 B 机、
或把密码密文贴到 token 字段，都会解密失败，而不是静默串用。
"""
from __future__ import annotations

import base64
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import get_settings

PREFIX = "v1:"


class TransitCredentialError(Exception):
    pass


def _cipher() -> AESGCM:
    configured = get_settings().supplier_secret_encryption_key
    if not configured:
        raise TransitCredentialError("后台凭据加密未配置，请联系运维")
    try:
        key = bytes.fromhex(configured.get_secret_value())
    except ValueError as exc:
        raise TransitCredentialError("后台凭据加密配置无效，请联系运维") from exc
    if len(key) != 32:
        raise TransitCredentialError("后台凭据加密配置无效，请联系运维")
    return AESGCM(key)


def _aad(scope: str) -> bytes:
    return f"99ipxin:transit:{scope}:v1".encode()


def encrypt_transit_secret(value: str, *, scope: str) -> str:
    nonce = os.urandom(12)
    encrypted = _cipher().encrypt(nonce, value.encode(), _aad(scope))
    return PREFIX + base64.urlsafe_b64encode(nonce + encrypted).decode("ascii")


def decrypt_transit_secret(value: str, *, scope: str) -> str:
    if not value.startswith(PREFIX):
        raise TransitCredentialError("中转机凭据格式无效，请重新录入")
    try:
        payload = base64.urlsafe_b64decode(value[len(PREFIX):].encode("ascii"))
        if len(payload) < 29:
            raise ValueError("ciphertext too short")
        return _cipher().decrypt(payload[:12], payload[12:], _aad(scope)).decode("utf-8")
    except (InvalidTag, UnicodeDecodeError, ValueError) as exc:
        raise TransitCredentialError("中转机凭据无法解密，请检查加密密钥或重新录入") from exc
