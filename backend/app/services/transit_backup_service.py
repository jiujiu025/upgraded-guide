"""面板备份与迁移。

一次备份 = 平台侧机器配置 + 面板上**每条入站的完整定义** + Xray 模板（出站与路由）。
之所以要这么全：迁移时端口、UUID、reality 私钥、shortId 必须一模一样，客户手里的
链接才不会失效；换机器只需要把域名解析指到新 IP。

文件落在服务器本地（``TRANSIT_BACKUP_DIR``，默认 ``backend/data/transit-backups``），
gzip 压缩、权限 600。备份里有私钥和客户 UUID，所以只给超级管理员下载。
"""
from __future__ import annotations

import gzip
import hashlib
import json
import logging
import re
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import TransitBackup, TransitVps
from app.models.base import utcnow
from app.services.log_service import write_log
from app.services.transit_service import panel_credentials
from app.services.xui_client import XuiClient, XuiError

logger = logging.getLogger(__name__)

FORMAT_VERSION = 1
_DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "data" / "transit-backups"
_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")


class BackupError(Exception):
    def __init__(self, message: str, *, code: str = "BACKUP_ERROR", status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


def storage_root() -> Path:
    import os

    configured = os.environ.get("TRANSIT_BACKUP_DIR")
    root = Path(configured) if configured else _DEFAULT_ROOT
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_stem(name: str) -> str:
    return _SAFE_NAME.sub("-", name).strip("-.")[:60] or "panel"


def resolve_path(backup: TransitBackup) -> Path:
    """只允许解析到存储根目录内部，避免数据库被改写后读到任意文件。"""
    root = storage_root().resolve()
    path = (root / backup.storage_key).resolve()
    if root not in path.parents:
        raise BackupError("备份文件路径非法", code="BAD_PATH", status=400)
    return path


def _client_for(db: Session, vps: TransitVps) -> XuiClient:
    password, token = panel_credentials(db, vps)
    return XuiClient(vps.base_url, username=vps.username, password=password, api_token=token)


def create_backup(db: Session, vps: TransitVps, *, actor=None, note: str | None = None) -> TransitBackup:
    try:
        with _client_for(db, vps) as client:
            inbounds = client.list_inbounds()
            xray = client.get_xray_template()
    except XuiError as exc:
        raise BackupError(f"读取面板失败：{exc}", code=exc.code, status=502) from exc

    payload = {
        "format": FORMAT_VERSION,
        "created_at": utcnow().isoformat() + "Z",
        "vps": {
            "name": vps.name, "group_code": vps.group_code,
            "connect_host": vps.connect_host, "base_url": vps.base_url,
            "sni": vps.sni, "dest": vps.dest, "fingerprint": vps.fingerprint,
            "port_start": vps.port_start, "port_end": vps.port_end,
            "max_inbounds": vps.max_inbounds,
        },
        "inbounds": inbounds,
        "xray": {
            "outbounds": xray.get("outbounds") or [],
            "rules": (xray.get("routing") or {}).get("rules") or [],
            "domainStrategy": (xray.get("routing") or {}).get("domainStrategy"),
        },
    }
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    compressed = gzip.compress(raw, compresslevel=6)

    stamp = utcnow().strftime("%Y%m%d-%H%M%S")
    filename = f"{_safe_stem(vps.name)}-{stamp}.json.gz"
    path = storage_root() / filename
    path.write_bytes(compressed)
    try:
        path.chmod(0o600)   # 里面有私钥与客户 UUID
    except OSError:
        pass

    record = TransitBackup(
        vps_id=vps.id, vps_name=vps.name, filename=filename, storage_key=filename,
        size_bytes=len(compressed), checksum=hashlib.sha256(compressed).hexdigest(),
        inbound_count=len(inbounds), outbound_count=len(payload["xray"]["outbounds"]),
        rule_count=len(payload["xray"]["rules"]), status="ready",
        note=(note or "")[:255] or None, created_by=actor.id if actor else None,
    )
    db.add(record)
    write_log(db, "info", "transit", "backup_created",
              f"面板 {vps.name} 已备份：{len(inbounds)} 条入站、{len(payload['xray']['outbounds'])} 个出站",
              detail=filename, actor=actor, vps_id=vps.id)
    db.flush()
    return record


def read_payload(backup: TransitBackup) -> dict:
    path = resolve_path(backup)
    if not path.exists():
        raise BackupError("备份文件不存在，可能已被清理", code="FILE_MISSING", status=404)
    try:
        data = json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise BackupError(f"备份文件损坏：{exc}", code="FILE_CORRUPT", status=422) from exc
    if data.get("format") != FORMAT_VERSION:
        raise BackupError("备份格式版本不匹配，请用相同版本恢复", code="FORMAT_MISMATCH", status=422)
    return data


def _inbound_for_create(item: dict) -> dict:
    """去掉面板自己维护的字段（id/tag/流量统计），其余原样重建。

    端口、UUID、reality 私钥、shortId 必须原样带过去，否则客户链接就失效了。
    """
    def as_text(value):
        return value if isinstance(value, str) else json.dumps(value or {}, ensure_ascii=False)

    payload = {
        "up": 0, "down": 0, "total": item.get("total") or 0,
        "remark": (item.get("remark") or "")[:120],
        "enable": bool(item.get("enable", True)),
        "expiryTime": item.get("expiryTime") or 0,
        "listen": item.get("listen") or "",
        "port": int(item["port"]),
        "protocol": item.get("protocol") or "vless",
        "settings": as_text(item.get("settings")),
        "streamSettings": as_text(item.get("streamSettings")),
        "sniffing": as_text(item.get("sniffing")),
    }
    if item.get("allocate"):
        payload["allocate"] = as_text(item.get("allocate"))
    return payload


def restore(db: Session, backup: TransitBackup, target: TransitVps, *, dry_run: bool = True,
            actor=None) -> dict:
    if backup.status == "failed":
        raise BackupError("这份备份上次恢复失败，请先检查错误信息", code="BACKUP_FAILED", status=409)
    data = read_payload(backup)
    inbounds = data.get("inbounds") or []

    try:
        with _client_for(db, target) as client:
            used = client.used_ports()
            conflicts = [int(item["port"]) for item in inbounds if int(item["port"]) in used]
            plan = {
                "backup_id": backup.id,
                "target_vps": {"id": target.id, "name": target.name, "connect_host": target.connect_host},
                "total_inbounds": len(inbounds),
                "conflicts": conflicts,
                "will_create": len(inbounds) - len(conflicts),
                "outbounds_to_add": len(data["xray"]["outbounds"]),
                "rules_to_add": len(data["xray"]["rules"]),
                "dry_run": dry_run,
            }
            if dry_run:
                return plan

            created, skipped, failed = [], [], []
            for item in inbounds:
                port = int(item["port"])
                if port in used:
                    skipped.append({"port": port, "reason": "目标面板已有同端口入站"})
                    continue
                try:
                    client.add_inbound(_inbound_for_create(item))
                    created.append(port)
                except XuiError as exc:
                    failed.append({"port": port, "reason": str(exc)[:160]})

            if created:
                config = client.get_xray_template()
                outbounds = config.setdefault("outbounds", [])
                existing = {item.get("tag") for item in outbounds}
                for outbound in data["xray"]["outbounds"]:
                    if outbound.get("tag") not in existing:
                        outbounds.append(outbound)
                        existing.add(outbound.get("tag"))
                routing = config.setdefault("routing", {})
                rules = routing.setdefault("rules", [])
                known = {tuple(rule.get("inboundTag") or []) for rule in rules}
                for rule in data["xray"]["rules"]:
                    key = tuple(rule.get("inboundTag") or [])
                    if key and key not in known:
                        rules.append(rule)
                        known.add(key)
                client.save_xray_template(config)
                client.restart_xray()

            plan.update({"created": created, "skipped": skipped, "failed": failed,
                         "dry_run": False})
    except XuiError as exc:
        backup.status = "failed"
        backup.error_message = str(exc)[:500]
        write_log(db, "error", "transit", "backup_restore_failed",
                  f"备份 #{backup.id} 恢复到 {target.name} 失败：{exc.code}",
                  detail=str(exc)[:800], actor=actor, vps_id=target.id)
        db.flush()
        raise BackupError(f"恢复失败：{exc}", code=exc.code, status=502) from exc

    if plan["failed"]:
        backup.status = "failed"
        backup.error_message = f"{len(plan['failed'])} 条入站恢复失败"[:500]
    else:
        backup.status = "restored"
        backup.restored_at = utcnow()
        backup.restored_to_vps_id = target.id
    write_log(db, "warning", "transit", "backup_restored",
              f"备份 #{backup.id} 已恢复到 {target.name}：新建 {len(plan['created'])}、跳过 {len(plan['skipped'])}、失败 {len(plan['failed'])}",
              actor=actor, vps_id=target.id)
    db.flush()
    return plan


def remove_file(backup: TransitBackup) -> None:
    try:
        resolve_path(backup).unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("删除备份文件失败 %s: %s", backup.filename, exc)
