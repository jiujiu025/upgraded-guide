"""把 customers.balance 上的历史余额补进钱包（默认只预演）。

为什么要做：余额的权威表示是 wallet_accounts.balance_cents，但历史客户可能只有
客户表上的浮点余额、没有钱包账户。这类客户在界面能看到余额，消费时却会被告知
「余额不足」——因为系统会新建一个 0 余额的钱包。本脚本为他们补建钱包并记一笔期初流水。

不做的事：
- 钱包已有非零余额但与浮点副本不一致 -> 只报告，不覆盖（谁对说不清，交给人工）；
- 客户没有登录账号 -> 跳过（建账号属业务决策）；
- 余额为负 -> 跳过（无法用期初流水建账）。

用法：
    python scripts/migrate_customer_balance_to_wallet.py                 # 预演
    python scripts/migrate_customer_balance_to_wallet.py --apply         # 真正执行
    python scripts/migrate_customer_balance_to_wallet.py --customer-id 12 --apply
    python scripts/migrate_customer_balance_to_wallet.py --csv plan.csv
"""
import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import SessionLocal
from app.services.wallet_migration_service import (
    STATUS_LABELS, apply_balance_migration, plan_balance_migration,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="历史余额迁移到钱包（默认只预演）")
    parser.add_argument("--apply", action="store_true", help="真正写库；不加则只预演")
    parser.add_argument("--customer-id", type=int, help="只处理指定客户")
    parser.add_argument("--csv", help="把计划写到 CSV")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        plan = plan_balance_migration(db)
        if args.customer_id:
            plan = [item for item in plan if item.customer_id == args.customer_id]
            if not plan:
                print(f"客户 #{args.customer_id} 无需迁移（无余额或已一致）")
                return 0

        counts: dict[str, int] = {}
        for item in plan:
            counts[item.status] = counts.get(item.status, 0) + 1

        print(f"扫描 {len(plan)} 个与余额相关的客户：")
        for status, label in STATUS_LABELS.items():
            if counts.get(status):
                print(f"  {label}：{counts[status]} 个")
        print()

        pending = [item for item in plan if item.actionable]
        for item in plan:
            if item.actionable:
                continue
            print(f"  [跳过] 客户 #{item.customer_id} {item.name}：{item.detail}")
        if any(not item.actionable for item in plan):
            print()

        if not pending:
            print("没有需要补建的钱包。")
        else:
            total = sum(item.target_cents for item in pending)
            funds = f"{total / 100:.2f}"
            if not args.apply:
                print(f"预演：将为 {len(pending)} 个客户补建钱包，合计 {funds} 元。")
                for item in pending:
                    print(f"  + 客户 #{item.customer_id} {item.name}（user_id={item.user_id}）"
                          f" → {item.target_cents / 100:.2f} 元")
                print("\n确认无误后加 --apply 执行。")
            else:
                done, failed = 0, 0
                for item in pending:
                    try:
                        apply_balance_migration(db, item)
                        db.commit()
                        done += 1
                        print(f"  ✓ 客户 #{item.customer_id} {item.name} → {item.target_cents / 100:.2f} 元")
                    except Exception as exc:                       # 单个失败不影响其他
                        db.rollback()
                        failed += 1
                        print(f"  ✗ 客户 #{item.customer_id} {item.name} 失败：{exc}")
                print(f"\n完成：成功 {done} 个，失败 {failed} 个，合计 {funds} 元。")
                print("建议随后执行：python scripts/audit_wallet_ledger.py")
                if failed:
                    return 1

        if args.csv:
            with open(args.csv, "w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.DictWriter(handle, fieldnames=["customer_id", "name", "user_id",
                                                            "target_cents", "status", "status_label", "detail"])
                writer.writeheader()
                writer.writerows(item.as_dict() for item in plan)
            print(f"计划已写入 {args.csv}")

        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
