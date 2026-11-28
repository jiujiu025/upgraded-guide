"""面板迁移：源机整体搬到目标机，可一键回滚。

为什么要做回滚：迁移是"把 A 的入站复制到 B、再把选择指向 B"。点错目标机、
备份本身有问题、或者客户反馈新机不行，都必须能立刻退回去。
所以迁移前**强制给目标机也备一份**——那份就是回滚用的原状快照。

迁移流程（每一步失败都停在可恢复的位置）：

1. 备份源机（A）——这是要搬过去的内容；
2. 备份目标机（B）——点错了用它还原 B；
3. 把 A 的入站/出站/路由恢复到 B；
4. 把 A 上的中转记录改指向 B，客户链接里的地址换成 B 的 connect_host；
5. **不停用 A**：DNS 还没切换前旧连接可能还在用它，替客户留住兜底。

回滚 = 用第 2 步的备份还原 B + 撤销第 4 步的记录改指向。
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import TransitBackup, TransitProvision, TransitVps
from app.services import transit_backup_service as backups
from app.services.audit_service import write_audit
from app.services.log_service import write_log

logger = logging.getLogger(__name__)


class MigrationError(Exception):
    def __init__(self, message: str, *, code: str = "MIGRATION_ERROR", status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


def _repush_links(db: Session, moved: list[TransitProvision], target: TransitVps) -> int:
    """把中转记录改指到目标机。

    客户链接里写的是 ``connect_host``：只要两台的连接地址相同（同一个域名），
    链接一个字都不用改；不同就把地址换掉，并记成待复核，提醒运营确认新机可连。
    """
    updated = 0
    for provision in moved:
        if provision.node_link:
            route = provision.node_link.split("://", 1)[-1]
            # vless://uuid@host:port?...#remark —— 只替换 host:port
            head, sep, tail = route.partition("@")
            rest, hash_sep, remark = tail.partition("#")
            _, _, port = rest.partition(":")
            provision.node_link = (
                f"{provision.node_link.split('://')[0]}://{head}@{target.connect_host}:"
                f"{port}{hash_sep}{remark}"
            )
        provision.transit_vps_id = target.id
        provision.status = "pending_verify"
        updated += 1
    db.flush()
    return updated


def migrate(db: Session, source: TransitVps, target: TransitVps, *, dry_run: bool = True,
            actor=None) -> dict:
    if source.id == target.id:
        raise MigrationError("源面板和目标面板不能是同一台")
    if source.group_code != target.group_code:
        raise MigrationError(
            f"两台机器不在同一区域组（{source.group_code} → {target.group_code}），"
            "迁移后会按错误的区域承接订单", code="GROUP_MISMATCH")
    if not target.is_enabled:
        # 迁移后新单要落到目标机，停用的机器接不到订单，等于白迁
        raise MigrationError("目标面板当前是停用状态，请先启用再迁移", code="TARGET_DISABLED")

    moved = list(db.scalars(
        select(TransitProvision).where(TransitProvision.transit_vps_id == source.id)
    ).all())
    configured = [item for item in moved if item.status in ("configured", "inbound_created", "pending_verify")]

    plan = {
        "dry_run": dry_run,
        "source": {"id": source.id, "name": source.name, "connect_host": source.connect_host},
        "target": {"id": target.id, "name": target.name, "connect_host": target.connect_host},
        "provisions": len(configured),
        "same_connect_host": source.connect_host == target.connect_host,
        "requires_dns": source.connect_host != target.connect_host,
    }
    if dry_run:
        return plan

    created_backups: list[int] = []
    try:
        source_backup = backups.create_backup(db, source, actor=actor, note=f"迁移前快照 → {target.name}")
        created_backups.append(source_backup.id)
        target_backup = backups.create_backup(db, target, actor=actor, note=f"迁移回滚点 ← {source.name}")
        created_backups.append(target_backup.id)
    except backups.BackupError as exc:
        db.rollback()
        raise MigrationError(f"迁移前置备份失败：{exc}", code=exc.code, status=exc.status) from exc
    db.commit()

    try:
        restore_plan = backups.restore(db, source_backup, target, dry_run=False, actor=actor)
    except backups.BackupError as exc:
        db.rollback()
        raise MigrationError(f"恢复到 {target.name} 失败：{exc}", code=exc.code, status=exc.status) from exc

    updated = _repush_links(db, configured, target)
    write_audit(db, actor, "transit_migrate", "transit_vps", source.id,
                f"{source.name} → {target.name}：恢复 {len(restore_plan.get('created') or [])} 条，"
                f"改指向 {updated} 条，回滚备份 #{target_backup.id}")
    write_log(db, "warning", "transit", "panel_migrated",
              f"面板迁移 {source.name} → {target.name}：入站 {len(restore_plan.get('created') or [])}、"
              f"记录改指向 {updated}",
              detail=f"回滚备份 #{target_backup.id}；目标备份 #{source_backup.id}", actor=actor,
              vps_id=target.id)
    db.commit()

    plan.update({
        "dry_run": False,
        "source_backup_id": source_backup.id,
        "rollback_backup_id": target_backup.id,
        "restored": restore_plan,
        "updated_provisions": updated,
        "next_steps": (
            ["两台连接地址相同，客户链接无需变更；确认新机可用后把旧机停用即可"]
            if source.connect_host == target.connect_host else
            [f"把 {source.connect_host} 的 DNS 解析改到 {target.name} 的机器 IP",
             "客户客户端无需任何改动，解析生效后自动切换",
             "确认新机稳定后，再决定是否停用旧机"]
        ),
    })
    return plan


def rollback_migration(db: Session, rollback_backup_id: int, *, actor=None) -> dict:
    """把目标机还原成迁移前的样子，并撤销记录改指向。

    只认"迁移回滚点"这类备份：随便拿一份备份回滚会把目标机搞成别的状态。
    """
    backup = db.get(TransitBackup, rollback_backup_id)
    if backup is None:
        raise MigrationError("回滚备份不存在", code="BACKUP_MISSING", status=404)
    if not (backup.note or "").startswith("迁移回滚点"):
        raise MigrationError("这份备份不是迁移回滚点，不能用于回滚", code="NOT_ROLLBACK_POINT")
    target = db.get(TransitVps, backup.vps_id) if backup.vps_id else None
    if target is None:
        raise MigrationError("回滚备份对应的面板已被删除", code="TARGET_MISSING", status=404)

    try:
        plan = backups.restore(db, backup, target, dry_run=False, actor=actor)
    except backups.BackupError as exc:
        db.rollback()
        raise MigrationError(f"回滚失败：{exc}", code=exc.code, status=exc.status) from exc

    write_audit(db, actor, "transit_migrate_rollback", "transit_vps", target.id,
                f"回滚 {target.name} 到迁移前状态：恢复 {len(plan.get('created') or [])} 条")
    write_log(db, "warning", "transit", "panel_migration_rolled_back",
              f"面板 {target.name} 已回滚到迁移前状态", actor=actor, vps_id=target.id)
    db.commit()
    return {"target": target.name, "restored": plan}
