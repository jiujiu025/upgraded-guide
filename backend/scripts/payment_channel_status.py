"""支付通道体检：一条命令回答"为什么客户端还是显示通道未启用"。

客户端「在线充值」的开关只看一个字段——`payment_channel_configs.online_enabled`。
它只有在「商户号 + 密钥可解密 + 已选支付方式 + 有一条通过的验收记录」同时满足时
才会被打开。任何一环缺失，客户端就只会看到"通道未启用"，而不会告诉你是哪一环——
运维细节不该出现在客户页面，所以排查工具放在这里。

只读：不写库、不发请求、不打印密钥。输出里只说"有没有配置""能不能解密"。

用法（在 backend 目录下）：
    python scripts/payment_channel_status.py
    python scripts/payment_channel_status.py --database-url postgresql+psycopg://...

退出码：0 已可收款；1 还不能开（原因见输出）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.models import PaymentChannelAcceptance, PaymentChannelConfig
from app.services.payment_acceptance_service import (
    MODE_LIVE,
    enable_blockers,
    online_limit_cents,
    sandbox_limit_cents,
    valid_acceptance,
)


def mask(value: str | None) -> str:
    """商户号只露后四位，用来确认"填的是不是同一家"。"""
    if not value:
        return "（未配置）"
    if len(value) <= 4:
        return "*" * len(value)
    return f"{'*' * (len(value) - 4)}{value[-4:]}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="支付通道配置与验收体检（只读）")
    parser.add_argument("--database-url", default=None, help="默认取 settings.database_url")
    args = parser.parse_args(argv)

    settings = get_settings()
    url = args.database_url or settings.database_url
    engine = create_engine(url)
    Session = sessionmaker(bind=engine)

    with Session() as db:
        config = db.get(PaymentChannelConfig, 1)
        if config is None:
            print("✗ 没有任何支付通道配置记录（payment_channel_configs id=1 不存在）")
            print("  → 到后台「财务中心 → 支付管理」填商户号与密钥并保存")
            return 1

        blockers = enable_blockers(db, config)
        acceptance = valid_acceptance(db, config)
        history = db.scalars(
            select(PaymentChannelAcceptance).order_by(PaymentChannelAcceptance.id.desc()).limit(5)
        ).all()

        print("== 支付通道配置 ==")
        print(f"商户号      : {mask(config.merchant_id)}")
        print(f"密钥        : {'已保存（密文）' if config.encrypted_secret else '（未配置）'}")
        if not config.encrypted_secret:
            # 没有密钥时不要报"解密失败"，那会把"还没填"误读成"服务端配错了"
            decryptable = False
            print("密钥可解密  : —（还没有保存密钥）")
        else:
            try:
                from app.services.payment_gateway import decrypt_secret
                decryptable = bool(decrypt_secret(config.encrypted_secret))
            except Exception:  # noqa: BLE001 - 解密失败只影响判断，不影响体检继续
                decryptable = False
            print(f"密钥可解密  : {'是' if decryptable else '否（PAYMENT_SECRET_ENCRYPTION_KEY 缺失或与写入时不一致）'}")
        print(f"支付方式    : {', '.join(config.channel_types or []) or '（未选择）'}")
        print(f"线上收款    : {'已开启' if config.online_enabled else '关闭'}")
        print(f"单笔区间    : {config.min_amount_cents / 100:.2f} ~ {config.max_amount_cents / 100:.2f} 元")
        print(f"更新时间    : {config.updated_at}")

        print("\n== 验收 ==")
        if acceptance is None:
            print("✗ 当前配置没有通过的验收记录")
        else:
            mode = "真实支付已核对（live）" if acceptance.mode == MODE_LIVE else "本地闭环（simulated）"
            print(f"✓ {mode} · {acceptance.verified_at}")
            limit = online_limit_cents(config, acceptance)
            print(f"  当前单笔上限 {limit / 100:.2f} 元"
                  + (f"（simulated 封顶 {sandbox_limit_cents() / 100:.2f} 元，跑通真实支付后加 --live 才放开）"
                     if acceptance.mode != MODE_LIVE else ""))
        if history:
            print("  最近记录：")
            for item in history:
                print(f"    #{item.id} {item.mode}/{item.status} {item.verified_at or item.created_at}")

        print("\n== 结论 ==")
        if not blockers and config.online_enabled:
            print("✓ 线上收款已可用；客户端应显示「通道可用」")
            return 0
        if blockers:
            print("✗ 现在还不能开线上收款：")
            for item in blockers:
                print(f"  - {item}")
        elif not config.online_enabled:
            print("✗ 验收已通过，但开关是关的：到后台「支付管理」把「启用线上收款」打开并保存")
        print("\n下一步：")
        print("  1) 后台「财务中心 → 支付管理」填真实商户号与密钥，保存")
        print("  2) 在服务器上跑：python scripts/payment_selftest.py --apply")
        print("  3) 回到支付管理打开「启用线上收款」并保存")
        print("  4) 跑通一笔真实支付后：python scripts/payment_selftest.py --live <商户单号> --apply 解除 100 元限额")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
