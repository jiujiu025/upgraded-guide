"""后台：中转机池、国家映射、待配中转与重试。

运营在这里录入 3x-ui 面板（地址、账号密码或 Token、reality 密钥、端口段），
系统才能把社媒/直播订单自动加工成 vless 专线。

安全口径：
- 面板密码/Token 只写不读，列表接口只回"已配置/未配置"和脱敏账号；
- 测试连通会真实请求面板，但不改任何配置（只读入站与 Xray 模板）；
- 重试中转只在订单处于 pending_transit 时可用，交付走与自动采购同一套线路生成逻辑。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.db import get_db
from app.models import Order, TransitProvision, TransitRoute, TransitVps, User
from app.services.audit_service import write_audit
from app.services.procurement_service import deliver_pending_transit
from app.services.settings_service import auto_procurement_enabled
from app.services.transit_credentials import (
    TransitCredentialError,
    decrypt_transit_secret,
    encrypt_transit_secret,
)
from app.services.transit_service import (
    TransitNotConfigured,
    TransitProvisionError,
    ensure_transit_routes,
)
from app.services.xui_client import XuiClient, XuiError

router = APIRouter(prefix="/admin", tags=["管理员中转机"])


class TransitVpsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2, max_length=60)
    group_code: str = Field(min_length=1, max_length=20)
    base_url: str = Field(min_length=8, max_length=255)
    username: str | None = Field(default=None, max_length=80)
    password: str | None = Field(default=None, max_length=200)
    api_token: str | None = Field(default=None, max_length=300)
    connect_host: str = Field(min_length=3, max_length=120)
    # 留空 = 每条入站自动生成独立密钥对（与手动操作一致）
    reality_private_key: str | None = Field(default=None, max_length=120)
    reality_public_key: str | None = Field(default=None, max_length=120)
    reality_short_id: str | None = Field(default=None, max_length=32)
    sni: str = Field(default="yahoo.com", max_length=120)
    dest: str = Field(default="yahoo.com:443", max_length=160)
    fingerprint: str = Field(default="random", max_length=20)
    port_start: int = Field(default=52000, ge=1024, le=65535)
    port_end: int = Field(default=52999, ge=1024, le=65535)
    max_inbounds: int = Field(default=200, ge=1, le=2000)
    is_enabled: bool = True
    note: str | None = Field(default=None, max_length=255)


class TransitVpsUpdateIn(BaseModel):
    """只改传了的字段；密码/Token 传空字符串表示清除。"""

    model_config = ConfigDict(extra="forbid")

    group_code: str | None = Field(default=None, max_length=20)
    base_url: str | None = Field(default=None, max_length=255)
    username: str | None = Field(default=None, max_length=80)
    password: str | None = Field(default=None, max_length=200)
    api_token: str | None = Field(default=None, max_length=300)
    connect_host: str | None = Field(default=None, max_length=120)
    reality_private_key: str | None = Field(default=None, max_length=120)
    reality_public_key: str | None = Field(default=None, max_length=120)
    reality_short_id: str | None = Field(default=None, max_length=32)
    sni: str | None = Field(default=None, max_length=120)
    dest: str | None = Field(default=None, max_length=160)
    fingerprint: str | None = Field(default=None, max_length=20)
    port_start: int | None = Field(default=None, ge=1024, le=65535)
    port_end: int | None = Field(default=None, ge=1024, le=65535)
    max_inbounds: int | None = Field(default=None, ge=1, le=2000)
    is_enabled: bool | None = None
    note: str | None = Field(default=None, max_length=255)


class TransitRouteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_code: str = Field(min_length=1, max_length=20)
    is_enabled: bool = True
    note: str | None = Field(default=None, max_length=200)


def _vps_view(vps: TransitVps, used_inbounds: int = 0) -> dict:
    return {
        "id": vps.id,
        "name": vps.name,
        "group_code": vps.group_code,
        "base_url": vps.base_url,
        "username": vps.username,
        "connect_host": vps.connect_host,
        "sni": vps.sni,
        "dest": vps.dest,
        "fingerprint": vps.fingerprint,
        "port_start": vps.port_start,
        "port_end": vps.port_end,
        "max_inbounds": vps.max_inbounds,
        # 停用前运营要能看清影响面：这台上已经有多少条线路
        "used_inbounds": used_inbounds,
        "is_enabled": vps.is_enabled,
        "note": vps.note,
        # 只暴露"配没配"，不回密文也不回明文
        "has_password": bool(vps.encrypted_password),
        "has_api_token": bool(vps.encrypted_api_token),
        "reality_public_key": vps.reality_public_key,
    }


@router.get("/transit/summary")
def transit_summary(db: Session = Depends(get_db), user: User = Depends(require_roles("super_admin", "admin"))):
    """页面顶部的一句话状态：自动开通开没开、有多少机器、多少单卡在中转。

    自动采购是资金开关，不能由中转页代开；这里只如实告诉运营"为什么面板没动"。
    """
    return {
        "auto_procurement_enabled": auto_procurement_enabled(db),
        "panels": db.scalar(select(func.count()).select_from(TransitVps)) or 0,
        "enabled_panels": db.scalar(
            select(func.count()).select_from(TransitVps).where(TransitVps.is_enabled.is_(True))
        ) or 0,
        "pending_transit_orders": db.scalar(
            select(func.count()).select_from(Order).where(Order.status == "pending_transit")
        ) or 0,
    }


@router.get("/transit/vps")
def list_transit_vps(db: Session = Depends(get_db), user: User = Depends(require_roles("super_admin", "admin"))):
    rows = db.scalars(select(TransitVps).order_by(TransitVps.group_code, TransitVps.id)).all()
    # 已用条数一次算完，别在循环里逐台查（机器多了就是 N+1）
    used = dict(db.execute(
        select(TransitProvision.transit_vps_id, func.count())
        .where(TransitProvision.status.in_(("inbound_created", "configured")))
        .group_by(TransitProvision.transit_vps_id)
    ).all())
    return [_vps_view(item, used.get(item.id, 0)) for item in rows]


@router.post("/transit/vps")
def create_transit_vps(payload: TransitVpsIn, db: Session = Depends(get_db),
                       user: User = Depends(require_roles("super_admin", "admin"))):
    if payload.port_start > payload.port_end:
        raise HTTPException(status_code=422, detail="端口段起始不能大于结束")
    if not (payload.api_token or (payload.username and payload.password)):
        raise HTTPException(status_code=422, detail="请填写 API Token，或面板账号与密码")
    vps = TransitVps(
        name=payload.name, group_code=payload.group_code, base_url=payload.base_url.rstrip("/"),
        username=payload.username, connect_host=payload.connect_host,
        reality_private_key=payload.reality_private_key,
        reality_public_key=payload.reality_public_key,
        reality_short_id=payload.reality_short_id,
        sni=payload.sni, dest=payload.dest, fingerprint=payload.fingerprint,
        port_start=payload.port_start, port_end=payload.port_end,
        max_inbounds=payload.max_inbounds, is_enabled=payload.is_enabled, note=payload.note,
    )
    _write_credentials(vps, password=payload.password, token=payload.api_token)
    try:
        db.add(vps)
        db.flush()
        write_audit(db, user, "create", "transit_vps", vps.id, f"新增中转机 {vps.name}（{vps.group_code}）")
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="中转机名称已存在") from exc
    db.refresh(vps)
    return _vps_view(vps)


@router.patch("/transit/vps/{vps_id}")
def update_transit_vps(vps_id: int, payload: TransitVpsUpdateIn, db: Session = Depends(get_db),
                       user: User = Depends(require_roles("super_admin", "admin"))):
    vps = db.get(TransitVps, vps_id)
    if vps is None:
        raise HTTPException(status_code=404, detail="中转机不存在")
    data = payload.model_dump(exclude_unset=True)
    password = data.pop("password", None)
    token = data.pop("api_token", None)
    for field, value in data.items():
        setattr(vps, field, value)
    if vps.port_start > vps.port_end:
        raise HTTPException(status_code=422, detail="端口段起始不能大于结束")
    if password is not None or token is not None:
        _write_credentials(vps, password=password, token=token)
    if "is_enabled" in data:
        detail = f"{'启用' if data['is_enabled'] else '停用'}中转机 {vps.name}"
    else:
        detail = f"修改中转机 {vps.name}"
    write_audit(db, user, "update", "transit_vps", vps.id, detail)
    db.commit()
    db.refresh(vps)
    return _vps_view(vps, _used_count(db, vps.id))


def _used_count(db: Session, vps_id: int) -> int:
    return db.scalar(
        select(func.count()).select_from(TransitProvision).where(
            TransitProvision.transit_vps_id == vps_id,
            TransitProvision.status.in_(("inbound_created", "configured")),
        )
    ) or 0


def _write_credentials(vps: TransitVps, *, password: str | None, token: str | None) -> None:
    """空字符串 = 清除；None = 不动。密文按机器名做 AAD。"""
    if password is not None:
        vps.encrypted_password = (
            encrypt_transit_secret(password, scope=f"transit:{vps.name}:password") if password else None
        )
    if token is not None:
        vps.encrypted_api_token = (
            encrypt_transit_secret(token, scope=f"transit:{vps.name}:token") if token else None
        )


@router.post("/transit/vps/{vps_id}/test")
def test_transit_vps(vps_id: int, db: Session = Depends(get_db),
                     user: User = Depends(require_roles("super_admin", "admin"))):
    """只读摸排：登录、列出入站、读 Xray 模板。不改任何配置。"""
    vps = db.get(TransitVps, vps_id)
    if vps is None:
        raise HTTPException(status_code=404, detail="中转机不存在")
    try:
        password = decrypt_transit_secret(vps.encrypted_password, scope=f"transit:{vps.name}:password") \
            if vps.encrypted_password else None
        token = decrypt_transit_secret(vps.encrypted_api_token, scope=f"transit:{vps.name}:token") \
            if vps.encrypted_api_token else None
    except TransitCredentialError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    try:
        with XuiClient(vps.base_url, username=vps.username, password=password, api_token=token) as client:
            inbounds = client.list_inbounds()
            template = client.get_xray_template()
    except XuiError as exc:
        raise HTTPException(status_code=400, detail=f"{exc.code}: {exc}") from exc
    outbounds = template.get("outbounds") or []
    rules = (template.get("routing") or {}).get("rules") or []
    ports = {int(item.get("port")) for item in inbounds if str(item.get("port", "")).isdigit()}
    free = [port for port in range(vps.port_start, min(vps.port_end, vps.port_start + 20) + 1) if port not in ports]
    return {
        "ok": True,
        "inbound_count": len(inbounds),
        "socks_outbounds": sum(1 for item in outbounds if item.get("protocol") == "socks"),
        "routing_rules": len(rules),
        "next_free_port": free[0] if free else None,
    }


@router.get("/transit/routes")
def list_transit_routes(db: Session = Depends(get_db),
                        user: User = Depends(require_roles("super_admin", "admin"))):
    ensure_transit_routes(db)
    db.commit()
    rows = db.scalars(select(TransitRoute).order_by(TransitRoute.group_code, TransitRoute.country_code)).all()
    return [
        {"country_code": item.country_code, "group_code": item.group_code,
         "is_enabled": item.is_enabled, "note": item.note}
        for item in rows
    ]


@router.put("/transit/routes/{country_code}")
def update_transit_route(country_code: str, payload: TransitRouteIn, db: Session = Depends(get_db),
                         user: User = Depends(require_roles("super_admin", "admin"))):
    code = country_code.strip().upper()
    route = db.scalar(select(TransitRoute).where(TransitRoute.country_code == code))
    if route is None:
        route = TransitRoute(country_code=code, group_code=payload.group_code,
                             is_enabled=payload.is_enabled, note=payload.note)
        db.add(route)
        db.flush()
    else:
        route.group_code = payload.group_code
        route.is_enabled = payload.is_enabled
        route.note = payload.note
    write_audit(db, user, "update", "transit_route", route.id,
                f"{code} → {payload.group_code}（{'启用' if payload.is_enabled else '停用'}）")
    db.commit()
    return {"country_code": code, "group_code": payload.group_code,
            "is_enabled": payload.is_enabled, "note": payload.note}


@router.get("/transit/provisions")
def list_transit_provisions(
    status: str | None = Query(default=None, max_length=20),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("super_admin", "admin")),
):
    query = select(TransitProvision, Order.order_no, TransitVps.name).join(
        Order, Order.id == TransitProvision.order_id, isouter=True,
    ).join(TransitVps, TransitVps.id == TransitProvision.transit_vps_id, isouter=True)
    if status:
        query = query.where(TransitProvision.status == status)
    rows = db.execute(query.order_by(TransitProvision.id.desc()).limit(limit)).all()
    return [
        {
            "id": provision.id,
            "order_id": provision.order_id,
            "order_no": order_no,
            "line_index": provision.line_index,
            "vps_name": vps_name,
            "status": provision.status,
            "inbound_port": provision.inbound_port,
            "exit_ip": provision.exit_ip,
            "remark": provision.remark,
            "error_code": provision.error_code,
            "error_message": provision.error_message,
        }
        for provision, order_no, vps_name in rows
    ]


@router.post("/transit/provisions/retry-all")
def retry_all_pending_transit(
    limit: int = Query(default=5, ge=1, le=20),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("super_admin", "admin")),
):
    """把积压的待配中转订单批量补做一遍。

    每单独立事务：一台订单失败（面板满、面板连不上）不会把其它订单一起回滚。
    默认只跑 5 单——每单都可能触发一次 Xray 重启，一次跑太多等于让同机客户
    反复瞬断，运营看着也不安心。
    """
    orders = list(db.scalars(
        select(Order).where(Order.status == "pending_transit").order_by(Order.id).limit(limit)
    ).all())
    results = []
    for order in orders:
        try:
            created = deliver_pending_transit(db, order, actor=user)
            results.append({"order_id": order.id, "order_no": order.order_no,
                            "ok": True, "line_ids": created})
        except Exception as exc:  # noqa: BLE001 - 单笔失败不能拖垮整批
            db.rollback()
            results.append({"order_id": order.id, "order_no": order.order_no,
                            "ok": False, "error": str(exc)[:200]})
    succeeded = sum(1 for item in results if item["ok"])
    return {
        "attempted": len(results), "succeeded": succeeded,
        "failed": len(results) - succeeded, "results": results,
    }


@router.post("/orders/{order_id}/retry-transit")
def retry_transit(order_id: int, db: Session = Depends(get_db),
                  user: User = Depends(require_roles("super_admin", "admin"))):
    """待配中转订单：补做中转并交付。已配置好的线路不会重建。"""
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="订单不存在")
    if order.status != "pending_transit":
        raise HTTPException(status_code=409, detail="订单不在待配中转状态")
    try:
        created = deliver_pending_transit(db, order, actor=user)
    except (TransitNotConfigured, TransitProvisionError) as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.refresh(order)
    return {"status": order.status, "line_ids": created}
