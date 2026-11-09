"""通知渠道分发：站内信照旧，渠道推送是附加动作。

设计约束（docs/通知渠道分发设计.md）：
- 业务事务只写 notifications（现有调用点零改动），分发由调度任务异步完成——
  外部 IM API 慢或挂不能拖垮下单事务；
- 投递流水 (notification_id, channel_id, target) 唯一约束做幂等：任务重跑、
  进程重启都不会重复推送；
- 只补发 24 小时内的通知，陈年通知再推是骚扰不是可靠性；
- 渠道连续失败 10 条 → 通知管理员并把渠道降频（只记流水不发送），
  避免 secret 失效后每分钟空转打日志；
- 分发任务自己产生的通知不再分发（防风暴环），最多进群机器人。

渠道密钥加密复用供应商凭据的 AES-GCM 设施（同一密钥材料、不同的关联数据，
解不开彼此），生产校验要求供应商密钥存在，这里顺势复用。
"""

from __future__ import annotations

import base64
import os
from datetime import timedelta

import httpx
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Notification, NotificationChannel, NotificationChannelBinding, NotificationDelivery
from app.models.base import utcnow

_ASSOCIATED_DATA = b"99ipxin:notification:channel:config:v1"

# 渠道类型 → 是否需要用户绑定（群机器人推的是整个群，没有个人目标）。
CHANNEL_TYPES = {
    "wecom_app": {"label": "企业微信应用消息", "needs_binding": True},
    "wecom_webhook": {"label": "企业微信群机器人", "needs_binding": False},
    # telegram_bot 二期实施：Adapter 与绑定轮询到位后在此注册。
}

DISPATCH_WINDOW_HOURS = 24
MAX_ATTEMPTS = 3
DEGRADE_THRESHOLD = 10
# 分发任务自己产生的告警通知带这个标记，分发时跳过，防止"渠道失败→告警→
# 再推渠道→再失败"的风暴环。
DISPATCH_MARKER = "[notify-dispatch]"


class ChannelConfigError(Exception):
    """渠道配置缺失或无法解密。"""


class ChannelError(Exception):
    """渠道发送失败（网络/凭据/限流），可重试。"""


def _cipher() -> AESGCM:
    configured = get_settings().supplier_secret_encryption_key
    if not configured:
        raise ChannelConfigError("通知渠道加密未配置，请联系运维")
    try:
        key = bytes.fromhex(configured.get_secret_value())
    except ValueError as exc:
        raise ChannelConfigError("通知渠道加密配置无效，请联系运维") from exc
    if len(key) != 32:
        raise ChannelConfigError("通知渠道加密配置无效，请联系运维")
    return AESGCM(key)


def channel_encryption_ready() -> bool:
    try:
        _cipher()
        return True
    except ChannelConfigError:
        return False


def encrypt_channel_config(value: str) -> str:
    nonce = os.urandom(12)
    encrypted = _cipher().encrypt(nonce, value.encode("utf-8"), _ASSOCIATED_DATA)
    return "v1:" + base64.urlsafe_b64encode(nonce + encrypted).decode("ascii")


def decrypt_channel_config(value: str) -> str:
    if not value.startswith("v1:"):
        raise ChannelConfigError("通知渠道配置格式无效，请重新录入")
    try:
        payload = base64.urlsafe_b64decode(value[3:].encode("ascii"))
        if len(payload) < 29:
            raise ValueError("ciphertext too short")
        return _cipher().decrypt(payload[:12], payload[12:], _ASSOCIATED_DATA).decode("utf-8")
    except (InvalidTag, UnicodeDecodeError, ValueError) as exc:
        raise ChannelConfigError("通知渠道配置无法解密，请检查加密密钥或重新录入") from exc


# ---- Adapter：一个渠道一个发送函数，统一 (config, target, title, content) 契约 ----

def _http_client() -> httpx.Client:
    return httpx.Client(timeout=get_settings().notification_http_timeout_seconds)


def _wecom_access_token(client: httpx.Client, corpid: str, secret: str) -> str:
    response = client.get("https://qyapi.weixin.qq.com/cgi-bin/gettoken",
                          params={"corpid": corpid, "corpsecret": secret})
    payload = response.json()
    if payload.get("errcode") != 0:
        raise ChannelError(f"企微获取 access_token 失败: {payload.get('errmsg')}")
    return payload["access_token"]


def send_wecom_app(config: dict, target: str, title: str, content: str) -> None:
    corpid = config.get("corpid")
    secret = config.get("secret")
    agent_id = config.get("agent_id")
    if not corpid or not secret or not agent_id:
        raise ChannelConfigError("企微应用消息缺少 corpid/secret/agent_id")
    with _http_client() as client:
        token = _wecom_access_token(client, str(corpid), str(secret))
        response = client.post("https://qyapi.weixin.qq.com/cgi-bin/message/send",
                               params={"access_token": token},
                               json={"touser": target, "msgtype": "text", "agentid": int(agent_id),
                                     "text": {"content": f"{title}\n{content}"}})
    payload = response.json()
    if payload.get("errcode") != 0:
        # 42001 token 过期等可重试错误与 81013 无效 userid 混在一起，统一按
        # ChannelError 上抛，由流水里的 last_error 区分人工处理。
        raise ChannelError(f"企微应用消息发送失败: {payload.get('errmsg')}")


def send_wecom_webhook(config: dict, target: str, title: str, content: str) -> None:
    webhook_url = config.get("webhook_url")
    if not webhook_url:
        raise ChannelConfigError("企微群机器人缺少 webhook_url")
    with _http_client() as client:
        response = client.post(str(webhook_url), json={"msgtype": "markdown",
                                                       "markdown": {"content": f"**{title}**\n{content}"}})
    if response.status_code >= 400:
        raise ChannelError(f"企微群机器人发送失败: HTTP {response.status_code}")
    payload = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
    if payload.get("errcode") not in (None, 0):
        raise ChannelError(f"企微群机器人发送失败: {payload.get('errmsg')}")


ADAPTERS = {
    "wecom_app": send_wecom_app,
    "wecom_webhook": send_wecom_webhook,
}


def send_to_channel(channel: NotificationChannel, target: str, title: str, content: str) -> None:
    adapter = ADAPTERS.get(channel.channel_type)
    if adapter is None:
        raise ChannelConfigError(f"未知渠道类型: {channel.channel_type}")
    config = decrypt_channel_config(channel.config_encrypted)
    import json
    adapter(json.loads(config), target, title, content)


# ---- 分发 ----

def _category_allowed(channel: NotificationChannel, category: str) -> bool:
    if not channel.push_categories:
        return True
    allowed = {item.strip() for item in channel.push_categories.split(",") if item.strip()}
    return category in allowed


def _degraded_channel_ids(db: Session) -> set[int]:
    """连续失败达到阈值的渠道：只记流水不再发送，等管理员处理。"""
    since = utcnow() - timedelta(hours=1)
    rows = db.execute(
        select(NotificationDelivery.channel_id, func.count())
        .where(NotificationDelivery.status == "failed",
               NotificationDelivery.created_at >= since)
        .group_by(NotificationDelivery.channel_id)
    ).all()
    return {channel_id for channel_id, count in rows if count >= DEGRADE_THRESHOLD}


def dispatch_pending_notifications(db: Session, now) -> dict:
    """扫描窗口内未投递的通知，生成投递流水并发送。

    由调度任务每分钟调用；幂等靠唯一约束：并发重入只会在 flush 时撞约束，
    撞到就跳过那一条。
    """
    settings = get_settings()
    window_start = now - timedelta(hours=DISPATCH_WINDOW_HOURS)
    degraded = _degraded_channel_ids(db)
    channels = list(db.scalars(select(NotificationChannel).where(NotificationChannel.is_enabled.is_(True))).all())
    if not channels:
        return {"channels": 0, "delivered": 0, "failed": 0, "skipped": 0}

    notifications = db.scalars(
        select(Notification)
        .where(Notification.created_at >= window_start, Notification.created_at <= now)
        .order_by(Notification.created_at.asc(), Notification.id.asc())
        .limit(settings.notification_dispatch_batch_limit * 10)
    ).all()

    created = sent = failed = skipped = 0
    for notification in notifications:
        if DISPATCH_MARKER in notification.content:
            # 分发自身的告警：只留在站内信，不再推渠道（防风暴环）。
            continue
        for channel in channels:
            if not _category_allowed(channel, notification.category):
                continue
            channel_info = CHANNEL_TYPES[channel.channel_type]
            if channel_info["needs_binding"]:
                bindings = db.scalars(select(NotificationChannelBinding).where(
                    NotificationChannelBinding.channel_id == channel.id,
                    NotificationChannelBinding.is_enabled.is_(True),
                    NotificationChannelBinding.user_id == notification.user_id,
                )).all()
                targets = [binding.target for binding in bindings]
            else:
                # 群机器人：通知的接收人无关紧要，推的是群本身。
                targets = ["group"]
            for target in targets:
                delivery = db.scalar(select(NotificationDelivery).where(
                    NotificationDelivery.notification_id == notification.id,
                    NotificationDelivery.channel_id == channel.id,
                    NotificationDelivery.target == target,
                ))
                if delivery is not None:
                    if delivery.status == "sent" or delivery.status == "skipped":
                        continue
                    # failed / 遗留 pending：有限重试（进程崩在发送中途也会走到这里）。
                    if delivery.attempts >= MAX_ATTEMPTS:
                        continue
                else:
                    delivery = NotificationDelivery(
                        notification_id=notification.id, channel_id=channel.id,
                        target=target, status="pending",
                    )
                    db.add(delivery)
                    db.flush()
                    created += 1
                if channel.id in degraded:
                    delivery.status = "skipped"
                    delivery.last_error = "渠道连续失败已降频，待管理员处理"
                    skipped += 1
                    continue
                try:
                    send_to_channel(channel, target, notification.title, notification.content)
                except (ChannelError, ChannelConfigError, ValueError) as exc:
                    delivery.status = "failed"
                    delivery.attempts += 1
                    delivery.last_error = str(exc)[:300]
                    failed += 1
                else:
                    delivery.status = "sent"
                    delivery.attempts += 1
                    delivery.sent_at = utcnow()
                    sent += 1
                # 每轮批量上限按"已处理投递"计，达到就收尾，剩下的下轮继续。
                if created + sent + failed >= settings.notification_dispatch_batch_limit:
                    db.flush()
                    return {"channels": len(channels), "created": created, "delivered": sent,
                            "failed": failed, "skipped": skipped}
    db.flush()
    return {"channels": len(channels), "created": created, "delivered": sent,
            "failed": failed, "skipped": skipped}
