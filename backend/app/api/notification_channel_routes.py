"""通知渠道管理 API。

渠道配置（企微 secret / webhook url）加密落库，接口只回掩码。
站内信本体（/api/notifications）不动；这里只管"往哪推"。
"""

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.api.deps import current_user, require_roles
from app.models import NotificationChannel, NotificationChannelBinding, NotificationDelivery, User
from app.models.base import utcnow
from app.services.audit_service import write_audit
from app.services.notification_channel_service import (
    CHANNEL_TYPES, channel_encryption_ready, encrypt_channel_config, send_to_channel,
)

router = APIRouter(tags=["通知渠道"])


class ChannelCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel_type: str = Field(max_length=30)
    name: str = Field(min_length=1, max_length=60)
    config: dict
    push_categories: str | None = Field(default=None, max_length=200)


class ChannelUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=60)
    config: dict | None = None
    push_categories: str | None = Field(default=None, max_length=200)
    is_enabled: bool | None = None


class ChannelBindingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: int
    target: str = Field(min_length=1, max_length=120)


class ChannelTestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str = Field(min_length=1, max_length=120)


class ChannelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    channel_type: str
    channel_type_label: str
    name: str
    push_categories: str
    is_enabled: bool
    needs_binding: bool
    created_at: object


class ChannelBindingOut(BaseModel):
    id: int
    channel_id: int
    channel_name: str
    channel_type: str
    user_id: int
    username: str
    display_name: str
    target: str
    is_enabled: bool


class DeliveryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    notification_id: int
    channel_id: int
    target: str
    status: str
    attempts: int
    last_error: str | None
    sent_at: object
    created_at: object


def _validate_channel_config(channel_type: str, config: dict) -> None:
    if channel_type not in CHANNEL_TYPES:
        raise HTTPException(status_code=422, detail=f"不支持的渠道类型：{channel_type}（当前支持 {'/'.join(CHANNEL_TYPES)}）")
    if channel_type == "wecom_app":
        missing = {"corpid", "secret", "agent_id"} - {k for k in ("corpid", "secret", "agent_id") if config.get(k)}
        if missing:
            raise HTTPException(status_code=422, detail=f"企微应用消息缺少必填项：{', '.join(sorted(missing))}")
    elif channel_type == "wecom_webhook":
        if not config.get("webhook_url"):
            raise HTTPException(status_code=422, detail="企微群机器人必须填写 webhook_url")


def _ensure_encryption_ready() -> None:
    if not channel_encryption_ready():
        raise HTTPException(status_code=503, detail="通知渠道加密未配置，请先在环境变量中配置 SUPPLIER_SECRET_ENCRYPTION_KEY")


def _channel_out(channel: NotificationChannel) -> ChannelOut:
    return ChannelOut(
        id=channel.id, channel_type=channel.channel_type,
        channel_type_label=CHANNEL_TYPES[channel.channel_type]["label"],
        name=channel.name, push_categories=channel.push_categories,
        is_enabled=channel.is_enabled, needs_binding=CHANNEL_TYPES[channel.channel_type]["needs_binding"],
        created_at=channel.created_at,
    )


def _binding_out(binding: NotificationChannelBinding, channel: NotificationChannel, user: User) -> ChannelBindingOut:
    return ChannelBindingOut(
        id=binding.id, channel_id=channel.id, channel_name=channel.name, channel_type=channel.channel_type,
        user_id=user.id, username=user.username, display_name=user.display_name,
        target=binding.target, is_enabled=binding.is_enabled,
    )


@router.get("/admin/notification-channels", response_model=list[ChannelOut])
def list_channels(db: Session = Depends(get_db), _: User = Depends(require_roles("super_admin"))):
    # 配置掩码：config 内容（secret/webhook）永远不出接口。
    return [_channel_out(channel) for channel in
            db.scalars(select(NotificationChannel).order_by(NotificationChannel.id)).all()]


@router.post("/admin/notification-channels", response_model=ChannelOut, status_code=201)
def create_channel(payload: ChannelCreateIn, db: Session = Depends(get_db),
                   actor: User = Depends(require_roles("super_admin"))):
    _ensure_encryption_ready()
    _validate_channel_config(payload.channel_type, payload.config)
    channel = NotificationChannel(
        channel_type=payload.channel_type, name=payload.name,
        config_encrypted=encrypt_channel_config(json.dumps(payload.config, ensure_ascii=False)),
        push_categories=(payload.push_categories or "").strip(),
        is_enabled=True,
    )
    db.add(channel)
    db.flush()
    write_audit(db, actor, "create", "notification_channel", channel.id,
                f"类型={payload.channel_type}; 名称={payload.name}")
    db.commit()
    db.refresh(channel)
    return _channel_out(channel)


@router.patch("/admin/notification-channels/{channel_id}", response_model=ChannelOut)
def update_channel(channel_id: int, payload: ChannelUpdateIn, db: Session = Depends(get_db),
                   actor: User = Depends(require_roles("super_admin"))):
    channel = db.get(NotificationChannel, channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="通知渠道不存在")
    changes = []
    if payload.name is not None:
        channel.name = payload.name
        changes.append("名称")
    if payload.push_categories is not None:
        channel.push_categories = payload.push_categories.strip()
        changes.append("推送范围")
    if payload.is_enabled is not None:
        channel.is_enabled = payload.is_enabled
        changes.append("启停")
    if payload.config is not None:
        _ensure_encryption_ready()
        _validate_channel_config(channel.channel_type, payload.config)
        channel.config_encrypted = encrypt_channel_config(json.dumps(payload.config, ensure_ascii=False))
        changes.append("配置")
    if changes:
        write_audit(db, actor, "update", "notification_channel", channel.id, "; ".join(changes))
        db.commit()
    db.refresh(channel)
    return _channel_out(channel)


@router.post("/admin/notification-channels/{channel_id}/test")
def test_channel(channel_id: int, payload: ChannelTestIn, db: Session = Depends(get_db),
                 actor: User = Depends(require_roles("super_admin"))):
    channel = db.get(NotificationChannel, channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="通知渠道不存在")
    try:
        send_to_channel(channel, payload.target, "玖玖云通知渠道测试", f"这是管理员 {actor.display_name} 发起的测试消息。")
    except Exception as exc:  # noqa: BLE001 - 测试接口要把真实错误带给管理员
        raise HTTPException(status_code=502, detail=f"测试发送失败：{exc}") from exc
    write_audit(db, actor, "test", "notification_channel", channel.id, f"目标={payload.target}")
    db.commit()
    return {"ok": True}


@router.get("/admin/notification-channels/{channel_id}/bindings", response_model=list[ChannelBindingOut])
def list_bindings(channel_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles("super_admin"))):
    channel = db.get(NotificationChannel, channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="通知渠道不存在")
    rows = db.execute(
        select(NotificationChannelBinding, User)
        .join(User, NotificationChannelBinding.user_id == User.id)
        .where(NotificationChannelBinding.channel_id == channel_id)
        .order_by(NotificationChannelBinding.id)
    ).all()
    return [_binding_out(binding, channel, user) for binding, user in rows]


@router.post("/admin/notification-channels/{channel_id}/bindings", response_model=ChannelBindingOut, status_code=201)
def create_binding(channel_id: int, payload: ChannelBindingIn, db: Session = Depends(get_db),
                   actor: User = Depends(require_roles("super_admin"))):
    channel = db.get(NotificationChannel, channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="通知渠道不存在")
    if not CHANNEL_TYPES[channel.channel_type]["needs_binding"]:
        raise HTTPException(status_code=422, detail="群机器人渠道不需要用户绑定，直接推群")
    user = db.get(User, payload.user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=422, detail="目标用户不存在或已停用")
    if db.scalar(select(NotificationChannelBinding.id).where(
            NotificationChannelBinding.user_id == payload.user_id,
            NotificationChannelBinding.channel_id == channel_id)):
        raise HTTPException(status_code=409, detail="该用户已绑定此渠道，请先删除原绑定")
    binding = NotificationChannelBinding(user_id=payload.user_id, channel_id=channel_id, target=payload.target.strip())
    db.add(binding)
    db.flush()
    write_audit(db, actor, "create", "notification_channel_binding", binding.id,
                f"渠道={channel.name}; 用户={user.username}; 目标={payload.target.strip()}")
    db.commit()
    db.refresh(binding)
    return _binding_out(binding, channel, user)


@router.delete("/admin/notification-channels/{channel_id}/bindings/{binding_id}", status_code=204)
def delete_binding(channel_id: int, binding_id: int, db: Session = Depends(get_db),
                   actor: User = Depends(require_roles("super_admin"))):
    binding = db.scalar(select(NotificationChannelBinding).where(
        NotificationChannelBinding.id == binding_id,
        NotificationChannelBinding.channel_id == channel_id))
    if not binding:
        raise HTTPException(status_code=404, detail="绑定不存在")
    db.delete(binding)
    write_audit(db, actor, "delete", "notification_channel_binding", binding_id, f"渠道ID={channel_id}; 用户ID={binding.user_id}")
    db.commit()


@router.get("/admin/notification-deliveries", response_model=list[DeliveryOut])
def list_deliveries(
    channel_id: int | None = Query(default=None),
    status: str | None = Query(default=None, max_length=20),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("super_admin", "admin")),
):
    query = select(NotificationDelivery)
    if channel_id is not None:
        query = query.where(NotificationDelivery.channel_id == channel_id)
    if status:
        query = query.where(NotificationDelivery.status == status)
    return list(db.scalars(query.order_by(NotificationDelivery.id.desc()).limit(limit)).all())


@router.get("/notifications/channels/me", response_model=list[ChannelBindingOut])
def my_channels(db: Session = Depends(get_db), user: User = Depends(current_user)):
    rows = db.execute(
        select(NotificationChannelBinding, NotificationChannel)
        .join(NotificationChannel, NotificationChannelBinding.channel_id == NotificationChannel.id)
        .where(NotificationChannelBinding.user_id == user.id)
        .order_by(NotificationChannelBinding.id)
    ).all()
    return [_binding_out(binding, channel, user) for binding, channel in rows]


@router.post("/notifications/channels/me/{binding_id}/toggle", response_model=ChannelBindingOut)
def toggle_my_channel(binding_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    row = db.execute(
        select(NotificationChannelBinding, NotificationChannel)
        .join(NotificationChannel, NotificationChannelBinding.channel_id == NotificationChannel.id)
        .where(NotificationChannelBinding.id == binding_id, NotificationChannelBinding.user_id == user.id)
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="绑定不存在")
    binding, channel = row
    binding.is_enabled = not binding.is_enabled
    binding.updated_at = utcnow()
    db.commit()
    db.refresh(binding)
    return _binding_out(binding, channel, user)
