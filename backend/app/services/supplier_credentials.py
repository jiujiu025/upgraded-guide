import base64
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Supplier, User
from app.services.audit_service import write_audit

SUPPLIER_CODE = "quanqiudaili"
_ASSOCIATED_DATA = b"99ipxin:supplier:quanqiudaili:api-key:v1"


class SupplierCredentialError(Exception):
    pass


def _cipher() -> AESGCM:
    configured = get_settings().supplier_secret_encryption_key
    if not configured:
        raise SupplierCredentialError("后台凭据加密未配置，请联系运维")
    try:
        key = bytes.fromhex(configured.get_secret_value())
    except ValueError as exc:
        raise SupplierCredentialError("后台凭据加密配置无效，请联系运维") from exc
    if len(key) != 32:
        raise SupplierCredentialError("后台凭据加密配置无效，请联系运维")
    return AESGCM(key)


def credential_encryption_ready() -> bool:
    try:
        _cipher()
        return True
    except SupplierCredentialError:
        return False


def encrypt_api_key(value: str) -> str:
    nonce = os.urandom(12)
    encrypted = _cipher().encrypt(nonce, value.encode("utf-8"), _ASSOCIATED_DATA)
    return "v1:" + base64.urlsafe_b64encode(nonce + encrypted).decode("ascii")


def decrypt_api_key(value: str) -> str:
    if not value.startswith("v1:"):
        raise SupplierCredentialError("供应商凭据格式无效，请重新录入")
    try:
        payload = base64.urlsafe_b64decode(value[3:].encode("ascii"))
        if len(payload) < 29:
            raise ValueError("ciphertext too short")
        return _cipher().decrypt(payload[:12], payload[12:], _ASSOCIATED_DATA).decode("utf-8")
    except (InvalidTag, UnicodeDecodeError, ValueError) as exc:
        raise SupplierCredentialError("供应商凭据无法解密，请检查加密密钥或重新录入") from exc


def get_supplier(db: Session) -> Supplier | None:
    return db.scalar(select(Supplier).where(Supplier.code == SUPPLIER_CODE))


def _environment_api_key() -> str | None:
    configured = get_settings().global_proxy_api_key
    if not configured:
        return None
    value = configured.get_secret_value()
    return value if value.strip() else None


def has_configured_api_key(db: Session) -> bool:
    supplier = get_supplier(db)
    return bool((supplier and supplier.encrypted_api_key) or _environment_api_key())


def credential_source(db: Session) -> str:
    supplier = get_supplier(db)
    if supplier and supplier.encrypted_api_key:
        return "admin"
    if _environment_api_key():
        return "environment"
    return "none"


def resolve_api_key(db: Session) -> str | None:
    supplier = get_supplier(db)
    if supplier and supplier.encrypted_api_key:
        return decrypt_api_key(supplier.encrypted_api_key)
    return _environment_api_key()


def save_api_key(db: Session, actor: User, value: str) -> None:
    supplier = get_supplier(db)
    if not supplier:
        raise SupplierCredentialError("供应商尚未初始化")
    supplier.encrypted_api_key = encrypt_api_key(value)
    write_audit(db, actor, "credential_update", "supplier_credential", supplier.id, "已更新后台加密凭据")


def clear_api_key(db: Session, actor: User) -> None:
    supplier = get_supplier(db)
    if not supplier:
        raise SupplierCredentialError("供应商尚未初始化")
    if not supplier.encrypted_api_key:
        raise SupplierCredentialError("没有可清除的后台凭据")
    supplier.encrypted_api_key = None
    write_audit(db, actor, "credential_clear", "supplier_credential", supplier.id, "已清除后台加密凭据；环境配置仍可用")
