"""支付通道验收：用"证据"代替"写死关闭"。

背景：线上收款原来被硬编码关死（`finance_routes.update_payment_config` 见到
`online_enabled=true` 直接 409，然后无条件写回 False）。这么写是对的——一次
验签没跑过就开收款，等于拿真钱试错。但它没有退出条件：商户号、密钥、回调
地址全配齐了也开不了，只能有人手工改库绕过检查，而改库这件事没有任何审计。

本模块把约束换个表达方式：**没证据不许开**，而不是**永远不许开**。

证据指一条 ``PaymentChannelAcceptance`` 记录，来自 ``scripts/payment_selftest.py``
实际跑过的一轮核对。记录绑定 ``merchant_id`` 与密钥指纹，任一项变化，旧记录
立即失效——换密钥之后必须重新验，这条不能省。

两种证据强度，差别体现在限额上：

- ``simulated``：在临时库里跑完整闭环（下单 → 模拟回调 → 验签 → 入账 → 巡检）。
  它证明代码路径和当前配置格式没问题，但**不证明服务商真的会回调**。
  因此只允许小额收款（``PAYMENT_SANDBOX_MAX_CENTS``，默认 100 元）。
- ``live``：核对一笔真实发生且已入账的支付单。到这一步才说明回调真的通了，
  解除限额。

宁可让第一次真实收款限额 100 元，也不要让"验过了"变成一句没有依据的话。
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.base import utcnow
from app.models import PaymentChannelAcceptance, PaymentChannelConfig
from app.services.payment_gateway import PaymentGatewayError, decrypt_secret

MODE_SIMULATED = "simulated"
MODE_LIVE = "live"
STATUS_PASSED = "passed"
STATUS_FAILED = "failed"
VALID_MODES = (MODE_SIMULATED, MODE_LIVE)


def _yuan(cents: int) -> str:
    return f"{Decimal(int(cents)) / Decimal(100):.2f}"


def secret_fingerprint(merchant_id: str, secret: str) -> str:
    """给密钥算指纹，用来判断"验收之后密钥有没有换过"。

    HMAC 的 key 用服务端主密钥，输入是商户号+明文密钥。同一个密钥每次算出来
    一样，换一个字节就完全不同；指纹本身不能反推密钥，落库也不敏感。
    """
    configured = get_settings().payment_secret_encryption_key
    # 主密钥没配置时退化为纯哈希：指纹仍然可用，且此时本来也开不了解密与收款。
    key = configured.get_secret_value().encode("utf-8") if configured else b"99ipxin:payment:acceptance"
    return hmac.new(key, f"{merchant_id}:{secret}".encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def channel_types_key(config: PaymentChannelConfig | None) -> str:
    """支付方式的规范化快照，用于判断"验收之后有没有加过新通道"。"""
    if not config:
        return ""
    return ",".join(sorted({str(item).strip() for item in (config.channel_types or []) if str(item).strip()}))


def current_scope(config: PaymentChannelConfig | None) -> tuple[str, str, str] | None:
    """返回 ``(merchant_id, 密钥指纹, 支付方式快照)``；配置不全或密钥解不开时返回 None。"""
    if not config or not config.merchant_id or not config.encrypted_secret:
        return None
    try:
        secret = decrypt_secret(config.encrypted_secret)
    except PaymentGatewayError:
        return None
    return config.merchant_id, secret_fingerprint(config.merchant_id, secret), channel_types_key(config)


def valid_acceptance(db: Session, config: PaymentChannelConfig | None) -> PaymentChannelAcceptance | None:
    """取当前配置下最新一条通过的验收记录。商户号或密钥变了就算无效。"""
    scope = current_scope(config)
    if scope is None:
        return None
    merchant_id, fingerprint, types_key = scope
    return db.scalar(
        select(PaymentChannelAcceptance)
        .where(
            PaymentChannelAcceptance.merchant_id == merchant_id,
            PaymentChannelAcceptance.secret_fingerprint == fingerprint,
            PaymentChannelAcceptance.channel_types_key == types_key,
            PaymentChannelAcceptance.status == STATUS_PASSED,
        )
        .order_by(PaymentChannelAcceptance.verified_at.desc(), PaymentChannelAcceptance.id.desc())
    )


def sandbox_limit_cents() -> int:
    return int(get_settings().payment_sandbox_max_cents)


def online_limit_cents(config: PaymentChannelConfig | None, acceptance: PaymentChannelAcceptance | None) -> int:
    """线上单笔上限。只有 simulated 验收时压到沙箱上限，live 验收后放开到配置值。"""
    if config is None:
        return 0
    configured = int(config.max_amount_cents or 0)
    if acceptance is not None and acceptance.mode == MODE_LIVE:
        return configured
    return min(configured, sandbox_limit_cents())


def enable_blockers(db: Session, config: PaymentChannelConfig | None) -> list[str]:
    """列出"现在不能开线上收款"的原因。空列表表示可以开。"""
    blockers: list[str] = []
    if config is None or not config.merchant_id or not config.encrypted_secret:
        blockers.append("商户号或支付密钥尚未配置")
    elif not config.channel_types:
        blockers.append("未选择支付方式")
    else:
        try:
            decrypt_secret(config.encrypted_secret)
        except PaymentGatewayError as exc:
            blockers.append(f"支付密钥无法解密，请重新录入：{exc.message}")
    if not blockers and valid_acceptance(db, config) is None:
        blockers.append(
            "尚未通过支付通道验收：先运行 scripts/payment_selftest.py 完成一轮闭环核对"
            "（换过商户号或密钥后必须重新验收）"
        )
    return blockers


def record_acceptance(
    db: Session,
    *,
    config: PaymentChannelConfig,
    mode: str,
    checks: list[dict],
    out_trade_no: str | None = None,
    amount_cents: int = 0,
    note: str | None = None,
    verified_by: int | None = None,
    verified_at: datetime | None = None,
) -> PaymentChannelAcceptance:
    """写入一条验收记录。``checks`` 逐项记录做了什么、结果如何。"""
    if mode not in VALID_MODES:
        raise ValueError(f"未知的验收模式：{mode}")
    scope = current_scope(config)
    if scope is None:
        raise ValueError("支付通道尚未配置完整，无法登记验收")
    merchant_id, fingerprint, types_key = scope
    passed = bool(checks) and all(bool(item.get("ok")) for item in checks)
    item = PaymentChannelAcceptance(
        mode=mode,
        status=STATUS_PASSED if passed else STATUS_FAILED,
        merchant_id=merchant_id,
        secret_fingerprint=fingerprint,
        channel_types_key=types_key,
        out_trade_no=out_trade_no,
        amount_cents=int(amount_cents),
        checks=[{**item, "name": str(item.get("name", ""))} for item in checks],
        note=note,
        verified_by=verified_by,
        verified_at=verified_at or utcnow(),
    )
    db.add(item)
    db.flush()
    if passed:
        # 验收时间戳同时回写到通道配置，供页面直接展示"上次验收"
        config.verified_at = item.verified_at
    return item


def acceptance_view(item: PaymentChannelAcceptance | None) -> dict | None:
    if item is None:
        return None
    return {
        "id": item.id,
        "mode": item.mode,
        "status": item.status,
        "out_trade_no": item.out_trade_no,
        "amount_cents": item.amount_cents,
        "checks": item.checks,
        "note": item.note,
        "verified_by": item.verified_by,
        "verified_at": item.verified_at,
    }


def status_summary(db: Session, config: PaymentChannelConfig | None) -> dict:
    """给配置接口用的一段摘要，前端据此解释"为什么开不了 / 现在能收多少"。"""
    acceptance = valid_acceptance(db, config)
    limit = online_limit_cents(config, acceptance)
    return {
        "accepted": acceptance is not None,
        "acceptance_mode": acceptance.mode if acceptance else None,
        "acceptance_verified_at": acceptance.verified_at if acceptance else None,
        "sandbox_limited": acceptance is not None and acceptance.mode == MODE_SIMULATED,
        "online_limit_cents": limit,
        "sandbox_max_cents": sandbox_limit_cents(),
        "blockers": enable_blockers(db, config),
        "limit_note": (
            "尚未通过验收，线上收款不可用"
            if acceptance is None
            else (
                f"仅完成本地闭环验收，单笔上限 {_yuan(limit)} 元；"
                "跑通一笔真实支付并登记 live 验收后可放开"
                if acceptance.mode == MODE_SIMULATED
                else "已通过真实支付验收，单笔上限取通道配置值"
            )
        ),
    }
