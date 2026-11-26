"""面板备份与迁移接口。

全部限定 super_admin：备份文件里有 reality 私钥和客户 UUID，恢复又是覆盖性操作，
不适合普通管理员。

典型迁移路径：
1. 对旧面板「立即备份」→ 生成 .json.gz；
2. 在新机器装好 3x-ui，在「中转面板」里录入为新面板（先停用）；
3. 备份记录上点「恢复」选新面板，先 dry-run 看端口冲突，再执行；
4. 改 DNS 解析指到新机器，客户链接不用换。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.db import get_db
from app.models import TransitBackup, TransitVps, User
from app.services import transit_backup_service as backups
from app.services.audit_service import write_audit

router = APIRouter(prefix="/admin/transit", tags=["面板备份与迁移"])


class RestoreIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_vps_id: int
    # 默认只预检：迁移是覆盖性操作，先看冲突再决定执行
    dry_run: bool = True


def _backup_view(item: TransitBackup) -> dict:
    return {
        "id": item.id,
        "vps_id": item.vps_id,
        "vps_name": item.vps_name,
        "filename": item.filename,
        "size_bytes": item.size_bytes,
        "checksum": item.checksum,
        "inbound_count": item.inbound_count,
        "outbound_count": item.outbound_count,
        "rule_count": item.rule_count,
        "status": item.status,
        "note": item.note,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "restored_at": item.restored_at.isoformat() if item.restored_at else None,
        "restored_to_vps_id": item.restored_to_vps_id,
        "error_message": item.error_message,
    }


@router.post("/vps/{vps_id}/backups")
def create_backup(vps_id: int, db: Session = Depends(get_db),
                  user: User = Depends(require_roles("super_admin"))):
    vps = db.get(TransitVps, vps_id)
    if vps is None:
        raise HTTPException(status_code=404, detail="面板不存在")
    try:
        record = backups.create_backup(db, vps, actor=user)
    except backups.BackupError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    write_audit(db, user, "backup_create", "transit_backup", record.id,
                f"备份面板 {vps.name}：{record.inbound_count} 条入站")
    db.commit()
    db.refresh(record)
    return _backup_view(record)


@router.get("/backups")
def list_backups(
    vps_id: int | None = Query(default=None),
    status: str | None = Query(default=None, max_length=20),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("super_admin")),
):
    query = select(TransitBackup).order_by(TransitBackup.id.desc()).limit(limit)
    if vps_id:
        query = select(TransitBackup).where(TransitBackup.vps_id == vps_id).order_by(
            TransitBackup.id.desc()).limit(limit)
    if status:
        query = query.where(TransitBackup.status == status)
    rows = list(db.scalars(query).all())
    return [_backup_view(item) for item in rows]


@router.get("/backups/{backup_id}/download")
def download_backup(backup_id: int, db: Session = Depends(get_db),
                    user: User = Depends(require_roles("super_admin"))):
    record = db.get(TransitBackup, backup_id)
    if record is None:
        raise HTTPException(status_code=404, detail="备份不存在")
    try:
        path = backups.resolve_path(record)
    except backups.BackupError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    if not path.exists():
        raise HTTPException(status_code=404, detail="备份文件不存在，可能已被清理")
    write_audit(db, user, "backup_download", "transit_backup", record.id,
                f"下载面板 {record.vps_name} 的备份 {record.filename}")
    db.commit()
    return FileResponse(path, media_type="application/gzip", filename=record.filename)


@router.post("/backups/{backup_id}/restore")
def restore_backup(backup_id: int, payload: RestoreIn, db: Session = Depends(get_db),
                   user: User = Depends(require_roles("super_admin"))):
    record = db.get(TransitBackup, backup_id)
    if record is None:
        raise HTTPException(status_code=404, detail="备份不存在")
    target = db.get(TransitVps, payload.target_vps_id)
    if target is None:
        raise HTTPException(status_code=404, detail="目标面板不存在")

    try:
        plan = backups.restore(db, record, target, dry_run=payload.dry_run, actor=user)
    except backups.BackupError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc

    if not payload.dry_run:
        write_audit(db, user, "backup_restore", "transit_backup", record.id,
                    f"把 {record.vps_name} 的备份恢复到 {target.name}："
                    f"新建 {len(plan.get('created') or [])}、跳过 {len(plan.get('skipped') or [])}")
        db.commit()
    return plan


@router.delete("/backups/{backup_id}")
def delete_backup(backup_id: int, db: Session = Depends(get_db),
                  user: User = Depends(require_roles("super_admin"))):
    record = db.get(TransitBackup, backup_id)
    if record is None:
        raise HTTPException(status_code=404, detail="备份不存在")
    backups.remove_file(record)
    write_audit(db, user, "backup_delete", "transit_backup", record.id,
                f"删除面板 {record.vps_name} 的备份 {record.filename}")
    db.delete(record)
    db.commit()
    return {"ok": True}
