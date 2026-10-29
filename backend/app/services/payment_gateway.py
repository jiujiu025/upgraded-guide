import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from uuid import uuid4

import httpx
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import PaymentChannelConfig


PAYMENT_API_URL = "https://pay.suisuizhifu.com/mapi.php"
PAYMENT_AAD = b"99ipxin:payment:suisui:v1:secret"


class PaymentGatewayError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def _cipher() -> AESGCM:
    configured = get_settings().payment_secret_encryption_key
    if not configured:
        raise PaymentGatewayError("PAYMENT_ENCRYPTION_NOT_CONFIGURED", "后台凭据加密未配置，请联系运维")
    try:
        key = bytes.fromhex(configured.get_secret_value())
    except ValueError as exc:
        raise PaymentGatewayError("PAYMENT_ENCRYPTION_INVALID", "后台凭据加密配置无效，请联系运维") from exc
    if len(key) != 32:
        raise PaymentGatewayError("PAYMENT_ENCRYPTION_INVALID", "后台凭据加密配置无效，请联系运维")
    return AESGCM(key)


def encrypt_secret(value: str) -> str:
    from os import urandom

    nonce = urandom(12)
    encrypted = _cipher().encrypt(nonce, value.encode("utf-8"), PAYMENT_AAD)
    return "v1:" + __import__("base64").urlsafe_b64encode(nonce + encrypted).decode("ascii")


def decrypt_secret(value: str) -> str:
    import base64

    if not value.startswith("v1:"):
        raise PaymentGatewayError("PAYMENT_SECRET_INVALID", "支付密钥格式无效，请重新录入")
    try:
        payload = base64.urlsafe_b64decode(value[3:].encode("ascii"))
        return _cipher().decrypt(payload[:12], payload[12:], PAYMENT_AAD).decode("utf-8")
    except (InvalidTag, UnicodeDecodeError, ValueError) as exc:
        raise PaymentGatewayError("PAYMENT_SECRET_UNREADABLE", "支付密钥无法解密，请检查加密密钥或重新录入") from exc


def config_for_update(db: Session) -> PaymentChannelConfig:
    config = db.scalar(select(PaymentChannelConfig).where(PaymentChannelConfig.id == 1).with_for_update())
    if not config:
        config = PaymentChannelConfig(id=1, channel_types=[], min_amount_cents=100, max_amount_cents=10000000, online_enabled=False)
        db.add(config)
        db.flush()
    return config


def signature(parameters: dict[str, str], secret: str) -> str:
    pairs = [f"{key}={parameters[key]}" for key in sorted(parameters)
             if key not in {"sign", "sign_type"} and parameters[key] not in (None, "")]
    raw = "&".join(pairs) + secret
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def verify_signature(parameters: dict[str, str], secret: str) -> bool:
    supplied = parameters.get("sign", "")
    return bool(supplied) and hmac.compare_digest(signature(parameters, secret).lower(), supplied.lower())


def create_provider_payment(config: PaymentChannelConfig, payment_order, payment_type: str, user_role: str) -> str:
    if not config.merchant_id or not config.encrypted_secret:
        raise PaymentGatewayError("PAYMENT_NOT_CONFIGURED", "支付通道尚未配置商户凭据")
    secret = decrypt_secret(config.encrypted_secret)
    origin = get_settings().frontend_origin.rstrip("/")
    if not origin.startswith("https://"):
        raise PaymentGatewayError("PAYMENT_HTTPS_REQUIRED", "线上收款要求正式 HTTPS 域名")
    parameters = {
        "pid": config.merchant_id,
        "type": payment_type,
        "out_trade_no": payment_order.out_trade_no,
        "notify_url": f"{origin}/api/payments/suisui/notify",
        "return_url": f"{origin}/{'sales' if user_role == 'sales' else 'client'}/wallet?out_trade_no={payment_order.out_trade_no}",
        "name": "玖玖云账户充值" if payment_order.purpose == "wallet_recharge" else "玖玖云线路订单",
        "money": f"{payment_order.amount_cents // 100}.{payment_order.amount_cents % 100:02d}",
        "sign_type": "MD5",
    }
    parameters["sign"] = signature(parameters, secret)
    try:
        with httpx.Client(timeout=10, follow_redirects=False) as client:
            response = client.post(PAYMENT_API_URL, data=parameters, headers={"Accept": "application/json"})
    except httpx.TimeoutException as exc:
        raise PaymentGatewayError("PAYMENT_TIMEOUT_UNKNOWN", "支付平台响应超时，订单进入人工核查，请勿重复创建") from exc
    except httpx.HTTPError as exc:
        raise PaymentGatewayError("PAYMENT_CONNECTION_FAILED", "连接支付平台失败，订单进入人工核查") from exc
    if response.status_code != 200:
        raise PaymentGatewayError("PAYMENT_HTTP_ERROR", f"支付平台返回 HTTP {response.status_code}，订单状态需人工核查")
    try:
        data = response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise PaymentGatewayError("PAYMENT_INVALID_RESPONSE", "支付平台返回格式无法识别，订单进入人工核查") from exc
    if not isinstance(data, dict) or data.get("code") != 1:
        raise PaymentGatewayError("PAYMENT_REJECTED", "支付平台未受理该订单，请在后台核对配置")
    pay_url = next((data.get(key) for key in ("payurl", "qrcode", "urlscheme")
                    if isinstance(data.get(key), str) and data.get(key).startswith(("https://", "weixin://", "alipays://"))), None)
    if not pay_url:
        raise PaymentGatewayError("PAYMENT_INVALID_RESPONSE", "支付平台响应缺少有效支付地址，订单进入人工核查")
    return pay_url


def new_payment_order_values(user_id: int, purpose: str, amount_cents: int, channel: str, order_id: int | None = None, sales_id: int | None = None) -> dict:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    token = uuid4().hex
    return {
        "out_trade_no": f"PAY{now.strftime('%Y%m%d%H%M%S')}{token[:12].upper()}",
        "idempotency_key": token,
        "user_id": user_id,
        "purpose": purpose,
        "order_id": order_id,
        "sales_id_snapshot": sales_id,
        "amount_cents": amount_cents,
        "channel": channel,
        "status": "pending",
        "expires_at": now + timedelta(minutes=30),
    }
