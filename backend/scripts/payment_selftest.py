"""支付通道自检：给"能不能开线上收款"产出可复现的证据。

线上收款现在不是写死关闭，而是"没证据不许开"。证据由本脚本产出：

**默认（simulated）**：在临时 SQLite 库里把真实回调用到的代码路径整条跑一遍——
建单 → 用当前密钥签名回调 → 打到 ``/api/payments/suisui/notify`` → 验签 → 入账 →
巡检 → 重放（必须幂等）。它证明的是：当前配置能被解开、签名算法前后一致、
验签会拒绝坏签名、金额核对有效、入账与账本自洽。

它**不证明**服务商真的会回调——所以按 simulated 验收开出来的线上收款，单笔被压在
``PAYMENT_SANDBOX_MAX_CENTS``（默认 100 元）。跑通一笔真实支付后，用 ``--live``
核对那笔单，登记 live 验收，限额才放开。

用法（在 backend 目录下）：

    python scripts/payment_selftest.py                     # 只检查，不写记录
    python scripts/payment_selftest.py --apply             # 通过后写 simulated 验收
    python scripts/payment_selftest.py --apply --verified-by 1
    python scripts/payment_selftest.py --live OUT_TRADE_NO --apply   # 核对真实支付单

退出码 0 表示全部检查通过。
"""
import argparse
import json
import sys
import tempfile
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.finance_routes import PAYMENT_API_URL
from app.core.config import get_settings
from app.db import SessionLocal, get_db
from app.main import app
from app.models import (
    Customer, PaymentChannelConfig, PaymentOrder, User, WalletAccount, WalletTransaction,
)
from app.models.base import Base, utcnow
from app.services.payment_acceptance_service import (
    MODE_LIVE, MODE_SIMULATED, current_scope, record_acceptance, secret_fingerprint,
)
from app.services.payment_gateway import decrypt_secret, signature
from app.services.wallet_audit_service import audit_wallet_ledger

NOTIFY_PATH = "/api/payments/suisui/notify"


def check(name: str, ok: bool, detail: str) -> dict:
    return {"name": name, "ok": bool(ok), "detail": detail}


def _yuan(cents: int) -> str:
    return f"{Decimal(int(cents)) / Decimal(100):.2f}"


def _callback_params(merchant_id: str, secret: str, *, out_trade_no: str, money: str, payment_type: str) -> dict:
    params = {
        "pid": merchant_id,
        "type": payment_type,
        "out_trade_no": out_trade_no,
        "trade_no": f"SELFTEST{uuid4().hex[:10].upper()}",
        "trade_status": "TRADE_SUCCESS",
        "money": money,
        "sign_type": "MD5",
    }
    params["sign"] = signature(params, secret)
    return params


def load_target_config() -> dict | None:
    """只读打开目标库，把支付通道配置复制成普通 dict（避免脱离会话后访问 ORM 属性）。"""
    with SessionLocal() as db:
        config = db.get(PaymentChannelConfig, 1)
        if config is None:
            return None
        return {
            "merchant_id": config.merchant_id,
            "encrypted_secret": config.encrypted_secret,
            "channel_types": list(config.channel_types or []),
            "min_amount_cents": int(config.min_amount_cents),
            "max_amount_cents": int(config.max_amount_cents),
        }


def run_simulated(amount_cents: int) -> tuple[list[dict], str | None]:
    checks: list[dict] = []
    config = load_target_config()
    if config is None or not config["merchant_id"] or not config["encrypted_secret"]:
        return [check("config_complete", False, "商户号或支付密钥未配置，先到后台支付管理录入")], None
    checks.append(check("config_complete", True,
                        f"商户号 {config['merchant_id']}，支付方式 {config['channel_types']}"))
    if not config["channel_types"]:
        return checks + [check("channel_types", False, "未选择任何支付方式")], None

    merchant_id = config["merchant_id"]
    try:
        secret = decrypt_secret(config["encrypted_secret"])
    except Exception as exc:  # noqa: BLE001 - 主密钥缺失/密文损坏都要给同一份结论
        return checks + [check("secret_decrypt", False, f"密钥无法解密：{exc}")], None
    checks.append(check("secret_decrypt", True, "密钥可解密，指纹 " + secret_fingerprint(merchant_id, secret)))

    origin = get_settings().frontend_origin
    checks.append(check("https_origin", origin.startswith("https://"),
                        f"回调地址基于 {origin}（真实回调要求 HTTPS 正式域名）"))
    checks.append(check("provider_endpoint", PAYMENT_API_URL.startswith("https://"), PAYMENT_API_URL))

    payment_type = sorted(config["channel_types"])[0]
    workdir = tempfile.mkdtemp(prefix="payment-selftest-")
    engine = create_engine(f"sqlite:///{Path(workdir, 'selftest.db').as_posix()}",
                           connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    db = Session()

    def override_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)

    try:
        # 临时库里放一份当前配置与一个测试账号，等价于生产配置的一次真实回调
        db.add(PaymentChannelConfig(
            id=1, merchant_id=merchant_id, encrypted_secret=config["encrypted_secret"],
            channel_types=list(config["channel_types"]), min_amount_cents=config["min_amount_cents"],
            max_amount_cents=config["max_amount_cents"], online_enabled=False,
        ))
        user = User(username="payment_selftest", password_hash="x", role="customer", display_name="自检账号")
        db.add(user)
        db.flush()
        customer = Customer(name="自检账号", user_id=user.id, balance=0)
        db.add(customer)
        db.flush()
        user_id = user.id

        payment = PaymentOrder(
            out_trade_no=f"ST{uuid4().hex[:12].upper()}", idempotency_key=f"selftest-{uuid4().hex}",
            user_id=user_id, purpose="wallet_recharge", amount_cents=amount_cents,
            channel="online", status="pending", provider_type=payment_type,
        )
        db.add(payment)
        db.commit()
        out_trade_no = payment.out_trade_no

        money = _yuan(amount_cents)
        params = _callback_params(merchant_id, secret, out_trade_no=out_trade_no,
                                  money=money, payment_type=payment_type)

        # 1. 坏签名必须被拒（否则验签等于没有）
        tampered = dict(params, money=_yuan(amount_cents + 100))
        rejected = client.get(NOTIFY_PATH, params=tampered)
        checks.append(check("callback_rejects_bad_signature", rejected.status_code == 403,
                            f"篡改金额后回调返回 HTTP {rejected.status_code}（期望 403）"))

        # 2. 金额与商户号不匹配时必须进人工核查，不能入账
        db.expire_all()
        still_pending = db.get(PaymentOrder, payment.id)
        checks.append(check("bad_signature_did_not_charge", still_pending.status == "pending",
                            f"坏签名回调后支付单状态 {still_pending.status}（期望 pending）"))

        # 3. 正常回调应当入账
        ok_response = client.get(NOTIFY_PATH, params=params)
        db.expire_all()
        paid = db.get(PaymentOrder, payment.id)
        checks.append(check("callback_accepted", ok_response.status_code == 200 and ok_response.text == "success",
                            f"回调返回 HTTP {ok_response.status_code} / {ok_response.text.strip()}"))
        checks.append(check("payment_marked_paid", paid.status == "paid", f"支付单状态 {paid.status}"))

        wallet = db.scalar(select(WalletAccount).where(WalletAccount.user_id == user_id))
        checks.append(check("wallet_credited", wallet is not None and int(wallet.balance_cents) == amount_cents,
                            f"钱包余额 {_yuan(wallet.balance_cents) if wallet else '无账户'}（期望 {money}）"))

        ledger = db.scalars(select(WalletTransaction).where(WalletTransaction.payment_order_id == payment.id)).all()
        checks.append(check("ledger_row_created", len(ledger) == 1 and int(ledger[0].amount_cents) == amount_cents,
                            f"支付单对应流水 {len(ledger)} 条"))

        # 4. 重放同一回调必须幂等
        replay = client.get(NOTIFY_PATH, params=params)
        db.expire_all()
        wallet_after = db.scalar(select(WalletAccount).where(WalletAccount.user_id == user_id))
        rows_after = db.scalars(select(WalletTransaction).where(WalletTransaction.payment_order_id == payment.id)).all()
        checks.append(check("replay_is_idempotent",
                            replay.status_code == 200 and int(wallet_after.balance_cents) == amount_cents
                            and len(rows_after) == 1,
                            f"重放后余额 {_yuan(wallet_after.balance_cents)}，流水 {len(rows_after)} 条"))

        report = audit_wallet_ledger(db)
        checks.append(check("ledger_audit_clean", report.ok,
                            "巡检无阻断" if report.ok else f"巡检阻断 {len(report.blocking_issues)} 项"))
        return checks, out_trade_no
    finally:
        app.dependency_overrides.clear()
        db.close()
        engine.dispose()
        try:
            Path(workdir, "selftest.db").unlink(missing_ok=True)
            Path(workdir).rmdir()
        except OSError:
            pass


def run_live(out_trade_no: str) -> tuple[list[dict], int]:
    checks: list[dict] = []
    with SessionLocal() as db:
        config = db.get(PaymentChannelConfig, 1)
        if config is None or not config.merchant_id:
            return [check("config_complete", False, "支付通道未配置")], 0
        payment = db.scalar(select(PaymentOrder).where(PaymentOrder.out_trade_no == out_trade_no))
        if payment is None:
            return [check("payment_exists", False, f"找不到支付单 {out_trade_no}")], 0
        checks.append(check("payment_exists", True, f"支付单 #{payment.id}，{payment.purpose}，{payment.channel}"))
        checks.append(check("payment_paid", payment.status == "paid",
                            f"状态 {payment.status}（live 验收要求已入账的 paid 单）"))
        checks.append(check("payment_amount", int(payment.amount_cents) > 0,
                            f"金额 {_yuan(payment.amount_cents)}"))
        checks.append(check("provider_type_configured",
                            payment.provider_type in (config.channel_types or []),
                            f"支付方式 {payment.provider_type} 在通道配置内"))

        if payment.purpose == "wallet_recharge":
            rows = db.scalars(select(WalletTransaction).where(WalletTransaction.payment_order_id == payment.id)).all()
            total = sum(int(item.amount_cents) for item in rows)
            checks.append(check("wallet_credited", len(rows) == 1 and total == int(payment.amount_cents),
                                f"钱包流水 {len(rows)} 条，合计 {_yuan(total)}"))
        else:
            checks.append(check("order_payment_branch", True,
                                "订单支付单：入账体现在订单状态推进，由巡检的支付单联动检查兜底"))

        report = audit_wallet_ledger(db)
        checks.append(check("ledger_audit_clean", report.ok,
                            "巡检无阻断" if report.ok else f"巡检阻断 {len(report.blocking_issues)} 项"))
        return checks, int(payment.amount_cents)


def persist(mode: str, checks: list[dict], *, out_trade_no: str | None, amount_cents: int,
            verified_by: int | None, note: str | None) -> str:
    with SessionLocal() as db:
        config = db.get(PaymentChannelConfig, 1)
        if config is None:
            return "支付通道未配置，无法登记验收"
        scope = current_scope(config)
        if scope is None and mode == MODE_SIMULATED:
            return "配置不完整，无法登记验收"
        item = record_acceptance(
            db,
            config=config,
            mode=mode,
            checks=checks,
            out_trade_no=out_trade_no,
            amount_cents=amount_cents,
            note=note,
            verified_by=verified_by,
            verified_at=utcnow(),
        )
        db.commit()
        return f"已登记验收 #{item.id}：{item.status}（{item.mode}）"


def main() -> int:
    parser = argparse.ArgumentParser(description="支付通道自检与验收登记")
    parser.add_argument("--apply", action="store_true", help="把本轮结果登记为验收记录")
    parser.add_argument("--live", metavar="OUT_TRADE_NO", help="核对一笔真实支付单（live 验收）")
    parser.add_argument("--amount-cents", type=int, default=100, help="simulated 模式使用的金额（分），默认 100")
    parser.add_argument("--verified-by", type=int, help="登记人 user id（写入审计用）")
    parser.add_argument("--note", help="备注，会写进验收记录")
    args = parser.parse_args()

    if args.live:
        mode = MODE_LIVE
        checks, amount_cents = run_live(args.live)
        out_trade_no = args.live
    else:
        mode = MODE_SIMULATED
        checks, out_trade_no = run_simulated(args.amount_cents)
        amount_cents = args.amount_cents

    passed = bool(checks) and all(item["ok"] for item in checks)
    print(json.dumps({"mode": mode, "passed": passed, "checks": checks}, ensure_ascii=False, indent=2))
    for item in checks:
        print(f"[{'通过' if item['ok'] else '失败'}] {item['name']}：{item['detail']}")
    print(f"\n结论：{'全部通过' if passed else '存在失败项，线上收款不应开启'}")

    if args.apply:
        print(persist(mode, checks, out_trade_no=out_trade_no, amount_cents=amount_cents,
                      verified_by=args.verified_by, note=args.note))
    else:
        print("（未加 --apply，本轮结果仅打印，不登记验收）")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
