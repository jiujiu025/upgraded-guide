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
# 每个供应商的环境变量回退来源。985 没有对应配置时就是未配置，不会去请求上游。
_ENV_KEY_FIELDS = {
    "quanqiudaili": "global_proxy_api_key",
    "985proxy": "nineeightfive_api_key",
}


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


def _associated_data(supplier_code: str) -> bytes:
    # 每个供应商用不同 AAD：把 A 家的密文贴到 B 家会解密失败，而不是静默串用。
    # quanqiudaili 与旧常量 `99ipxin:supplier:quanqiudaili:api-key:v1` 完全一致，
    # 历史密文不需要迁移。
    return f"99ipxin:supplier:{supplier_code}:api-key:v1".encode("utf-8")


def encrypt_api_key(value: str, *, supplier_code: str = SUPPLIER_CODE) -> str:
    nonce = os.urandom(12)
    encrypted = _cipher().encrypt(nonce, value.encode("utf-8"), _associated_data(supplier_code))
    return "v1:" + base64.urlsafe_b64encode(nonce + encrypted).decode("ascii")


def decrypt_api_key(value: str, *, supplier_code: str = SUPPLIER_CODE) -> str:
    if not value.startswith("v1:"):
        raise SupplierCredentialError("供应商凭据格式无效，请重新录入")
    try:
        payload = base64.urlsafe_b64decode(value[3:].encode("ascii"))
        if len(payload) < 29:
            raise ValueError("ciphertext too short")
        return _cipher().decrypt(payload[:12], payload[12:], _associated_data(supplier_code)).decode("utf-8")
    except (InvalidTag, UnicodeDecodeError, ValueError) as exc:
        raise SupplierCredentialError("供应商凭据无法解密，请检查加密密钥或重新录入") from exc


def get_supplier(db: Session, supplier_id: int | None = None) -> Supplier | None:
    if supplier_id is not None:
        return db.get(Supplier, supplier_id)
    return db.scalar(select(Supplier).where(Supplier.code == SUPPLIER_CODE))


def _environment_api_key() -> str | None:
    configured = get_settings().global_proxy_api_key
    if not configured:
        return None
    value = configured.get_secret_value()
    return value if value.strip() else None


def has_configured_api_key(db: Session, supplier: Supplier | None = None) -> bool:
    supplier = supplier or get_supplier(db)
    if supplier is None:
        return False
    ready, _source = supplier_key_status(db, supplier)
    return ready


def supplier_key_status(db: Session, supplier: Supplier) -> tuple[bool, str]:
    """任意供应商的 Key 状态（不依赖"当前只有一个供应商"的假设）。

    密文凭据优先；否则读该供应商对应的环境变量。第二家上游（985）没有配置时
    返回 (False, "none")，页面据此提示"未配置"，不会静默去请求。
    """
    if supplier.encrypted_api_key:
        return True, "admin"
    field = _ENV_KEY_FIELDS.get(supplier.code)
    if not field:
        return False, "none"
    configured = getattr(get_settings(), field, None)
    if configured and configured.get_secret_value().strip():
        return True, "environment"
    return False, "none"


def credential_source(db: Session, supplier: Supplier | None = None) -> str:
    supplier = supplier or get_supplier(db)
    if supplier is None:
        return "none"
    ready, source = supplier_key_status(db, supplier)
    return source if ready else "none"


def resolve_api_key(db: Session, supplier: Supplier | None = None) -> str | None:
    supplier = supplier or get_supplier(db)
    if supplier is None:
        return None
    if supplier.encrypted_api_key:
        return decrypt_api_key(supplier.encrypted_api_key, supplier_code=supplier.code)
    field = _ENV_KEY_FIELDS.get(supplier.code)
    if not field:
        return None
    configured = getattr(get_settings(), field, None)
    return configured.get_secret_value() if configured and configured.get_secret_value().strip() else None


def save_api_key(db: Session, actor: User, value: str, supplier: Supplier | None = None) -> None:
    supplier = supplier or get_supplier(db)
    if not supplier:
        raise SupplierCredentialError("供应商尚未初始化")
    supplier.encrypted_api_key = encrypt_api_key(value, supplier_code=supplier.code)
    write_audit(db, actor, "credential_update", "supplier_credential", supplier.id,
                f"已更新后台加密凭据（{supplier.name}）")


def clear_api_key(db: Session, actor: User, supplier: Supplier | None = None) -> None:
    supplier = supplier or get_supplier(db)
    if not supplier:
        raise SupplierCredentialError("供应商尚未初始化")
    if not supplier.encrypted_api_key:
        raise SupplierCredentialError("没有可清除的后台凭据")
    supplier.encrypted_api_key = None
    write_audit(db, actor, "credential_clear", "supplier_credential", supplier.id,
                f"已清除后台加密凭据（{supplier.name}）；环境配置仍可用")
