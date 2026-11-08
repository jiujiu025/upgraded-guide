"""钱包账本一致性巡检（只读）。

核对三个真相源是否互相自洽——权威余额（整数分）、明细流水、客户表上的浮点副本，
并检查已付款的支付单是否都能在钱包里找到对应流水。

只读，不修任何数据。退出码 1 表示存在阻断级问题（账本身错了）。

用法：
    python scripts/audit_wallet_ledger.py
    python scripts/audit_wallet_ledger.py --csv wallet_audit.csv
    python scripts/audit_wallet_ledger.py --limit 200      # 每类问题最多列 200 条
"""
import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import SessionLocal
from app.services.wallet_audit_service import audit_wallet_ledger

# 打印顺序固定，便于比对两次巡检结果
KIND_LABELS = {
    "balance_vs_ledger": "账户余额与流水累计不一致",
    "ledger_chain": "流水首尾不接或单条不平",
    "missing_wallet_transaction": "支付单在钱包里找不到对应流水",
    "missing_refund_transaction": "余额退款没有退回流水",
    "negative_balance": "账户余额为负",
    "customer_float_drift": "客户浮点副本与钱包余额不一致",
    "orphan_customer_balance": "客户有余额但没有钱包账户",
    "ledger_opening": "最早流水期初余额不为 0",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="钱包账本一致性巡检（只读）")
    parser.add_argument("--csv", help="把明细写到 CSV")
    parser.add_argument("--limit", type=int, default=50, help="每类问题最多列出条数")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        report = audit_wallet_ledger(db, detail_limit=args.limit)
    finally:
        db.close()

    print(f"钱包账户 {report.accounts} 个，流水 {report.transactions} 条，客户 {report.customers} 个")
    print()

    if not report.issues:
        print("结论：三个真相源完全一致，无任何问题。")
        return 0

    grouped: dict[str, list] = {}
    for issue in report.issues:
        grouped.setdefault(issue.kind, []).append(issue)

    blocking_total = 0
    for kind, label in KIND_LABELS.items():
        items = grouped.get(kind)
        if not items:
            continue
        real_count = report.stats.get(kind, len(items))
        flag = "阻断" if items[0].blocking else "提示"
        if items[0].blocking:
            blocking_total += real_count
        shown = f"，仅列前 {len(items)} 条" if real_count > len(items) else ""
        print(f"[{flag}] {label}：{real_count} 项{shown}")
        for issue in items:
            print(f"    {issue.subject}  {issue.detail}")
        print()

    print(f"结论：阻断 {blocking_total} 项，提示 {len(report.warnings)} 项（含已折叠明细）")

    if args.csv:
        rows = [issue.as_dict() for issue in report.issues]
        with open(args.csv, "w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=["kind", "subject", "detail", "blocking"])
            writer.writeheader()
            writer.writerows(rows)
        print(f"明细已写入 {args.csv}")

    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
