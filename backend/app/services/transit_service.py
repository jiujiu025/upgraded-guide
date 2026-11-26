"""中转层：在已采购的住宅 IP 之上加一跳 VPS 入站，把裸 IP 变成可交付的专线。

业务口径（2026-11-13 玖夜确认，2026-11-26 补充拓扑）：

- 静态住宅 IP：上游买一个住宅 IP，直出，无中转；
- 社媒运营专线：同一个住宅 IP + 一跳 VPS 中转；
- 直播专线：同一个住宅 IP + 性能更好的 VPS 中转。

中转机不按"客户买哪个国家"一一对应，而是**按区域组承接**：
北美/欧洲的订单落到美国机，东南亚/南非等落到香港机。国家 → 组的映射在
``transit_routes`` 表里，新增国家改配置即可，不用加机器也不用改代码。

一次中转 = 3x-ui 上的三个对象（与人工点面板完全一致）：

1. 入站：vless + reality，remark 写「地区-出口IP」，导出链接给客户；
2. 出站：socks5 指向刚采购的住宅 IP（带账密）；
3. 路由：``inbound-{port}`` → 该 socks 出站的绑定规则。

三条必须守住的边界：

- 中转没配好，订单只能停 ``pending_transit``，**不能**写 CustomerLine、不能发"已开通"；
- IP 已经买了（钱已花），任何中转失败都**不能**把采购标成 failed，否则会诱导重买；
- 同一台机器改 Xray 模板必须串行（模板是全量覆盖写），否则并发会互相丢配置。
"""
from __future__ import annotations

import json
import logging
import uuid as uuid_lib
from dataclasses import dataclass
from types import SimpleNamespace
from urllib.parse import quote

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Order, Region, TransitProvision, TransitRoute, TransitVps
from app.services.log_service import write_log
from app.services.reality_keys import (
    generate_reality_keypair,
    generate_short_id,
    public_key_from_private,
)
from app.services.transit_credentials import (
    TransitCredentialError,
    decrypt_transit_secret,
    encrypt_transit_secret,
)
from app.services.xui_client import (
    XuiAuthError,
    XuiClient,
    XuiConfigError,
    XuiError,
    dumps_field,
    loads_field,
)

logger = logging.getLogger(__name__)

# 需要中转的产品线。静态住宅不在此列（直出）。
TRANSIT_CATEGORIES = {"social_media", "live_stream_mobile", "live_stream_pc"}

# 国家 → 区域组的默认映射，只用于首次播种；之后以 transit_routes 表为准。
# 北美/欧洲/中东 → 美国机；东南亚/南亚/南非/澳洲 → 香港机。
DEFAULT_COUNTRY_GROUPS: dict[str, str] = {
    "US": "us", "CA": "us", "MX": "us", "BR": "us",
    "GB": "us", "FR": "us", "DE": "us", "NL": "us", "ES": "us", "IT": "us",
    "PL": "us", "HU": "us", "BG": "us", "DK": "us", "RU": "us",
    "TR": "us", "IL": "us", "AE": "us", "SA": "us",
    "HK": "hk", "TW": "hk", "JP": "hk", "KR": "hk", "SG": "hk", "MY": "hk",
    "TH": "hk", "VN": "hk", "ID": "hk", "PH": "hk", "IN": "hk",
    "BD": "hk", "PK": "hk", "AU": "hk", "ZA": "hk",
}


class TransitNotConfigured(Exception):
    """没有可用中转机（该国家未映射，或组内机器都停用/满载）。

    调用方应把订单置为 ``pending_transit``：IP 已经买了（钱已花），
    但还没加工完，不能交付。
    """


class TransitProvisionError(Exception):
    """面板调用失败。入站可能已经建好，所以同样只能停在待配中转，不能标 failed。"""

    def __init__(self, message: str, *, code: str = "TRANSIT_PROVISION_FAILED"):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class TransitEndpoint:
    """客户侧实际连的入口（中转 VPS），以及它背后的住宅出口 IP。"""

    server: str
    port: int
    protocol: str
    username: str | None
    password: str | None
    exit_ip: str
    panel_ref: str | None = None
    # 下面两项是 2026-11-26 新增：自动交付要靠它们拼 vless 链接与线路备注
    link: str | None = None
    remark: str | None = None


def requires_transit(resource_category: str | None) -> bool:
    return (resource_category or "") in TRANSIT_CATEGORIES


# ---------------------------------------------------------------- 路由与选机
def ensure_transit_routes(db: Session) -> None:
    """把默认国家映射补进表里。只补缺失的，不覆盖运营改过的值。"""
    existing = set(db.scalars(select(TransitRoute.country_code)).all())
    added = 0
    for code, group in DEFAULT_COUNTRY_GROUPS.items():
        if code in existing:
            continue
        db.add(TransitRoute(country_code=code, group_code=group, is_enabled=True, note="默认映射"))
        added += 1
    if added:
        db.flush()


def group_for_country(db: Session, country_code: str) -> str | None:
    code = (country_code or "").strip().upper()
    if not code:
        return None
    route = db.scalar(select(TransitRoute).where(TransitRoute.country_code == code))
    if route is None:
        ensure_transit_routes(db)
        route = db.scalar(select(TransitRoute).where(TransitRoute.country_code == code))
    if route is None or not route.is_enabled:
        return None
    return route.group_code


def pick_vps(db: Session, group_code: str) -> TransitVps | None:
    """同组里挑一台还能接的机器：启用、本地占用未到上限、占用最少的优先。

    容量按 ``transit_provisions`` 计数，不去逐台请求面板——机器多了那样太慢。
    手工在面板上加的入站不计入，所以 ``max_inbounds`` 要留出余量。
    """
    machines = db.scalars(
        select(TransitVps).where(TransitVps.group_code == group_code, TransitVps.is_enabled.is_(True))
        .order_by(TransitVps.id)
    ).all()
    if not machines:
        return None
    best: tuple[int, TransitVps] | None = None
    for machine in machines:
        used = db.scalar(
            select(func.count()).select_from(TransitProvision).where(
                TransitProvision.transit_vps_id == machine.id,
                TransitProvision.status.in_(("inbound_created", "configured")),
            )
        ) or 0
        if used >= machine.max_inbounds:
            continue
        if best is None or used < best[0]:
            best = (used, machine)
    return best[1] if best else None


def allocate_port(vps: TransitVps, used: set[int]) -> int:
    """在机器的端口段里取最小空闲端口。耗尽时抛错，不要偷偷用段外端口。"""
    for port in range(vps.port_start, vps.port_end + 1):
        if port not in used:
            return port
    raise TransitNotConfigured(f"{vps.name} 的端口段 {vps.port_start}-{vps.port_end} 已用尽")


# ---------------------------------------------------------------- 面板对象
def build_inbound_payload(
    vps: TransitVps,
    *,
    remark: str,
    port: int,
    client_id: str,
    email: str,
    private_key: str,
    public_key: str,
    short_id: str,
) -> dict:
    reality = {
        "show": False,
        "xver": 0,
        "dest": vps.dest,
        "serverNames": [vps.sni],
        "privateKey": private_key,
        "minClient": "",
        "maxClient": "",
        "maxTimediff": 0,
        "shortIds": [short_id],
        "settings": {
            "publicKey": public_key,
            "fingerprint": vps.fingerprint,
            "serverName": "",
            "spiderX": "/",
        },
    }
    return {
        "up": 0,
        "down": 0,
        "total": 0,
        "remark": remark[:120],
        "enable": True,
        "expiryTime": 0,
        "listen": "",
        "port": port,
        "protocol": "vless",
        "settings": dumps_field({
            "clients": [{
                "id": client_id,
                "flow": "",
                "email": email[:80],
                "limitIp": 0,
                "totalGB": 0,
                "expiryTime": 0,
                "enable": True,
            }],
            "decryption": "none",
            "fallbacks": [],
        }),
        "streamSettings": dumps_field({
            "network": "tcp",
            "security": "reality",
            "externalProxy": [],
            "realitySettings": reality,
            "tcpSettings": {"acceptProxyProtocol": False, "header": {"type": "none"}},
        }),
        "sniffing": dumps_field({
            "enabled": True,
            "destOverride": ["http", "tls", "quic", "fakedns"],
            "metadataOnly": False,
            "routeOnly": False,
        }),
        "allocate": dumps_field({"strategy": "always", "refresh": 5, "concurrency": 3}),
    }


def build_outbound(tag: str, *, host: str, port: int, username: str | None, password: str | None) -> dict:
    server: dict = {"address": host, "port": int(port or 0)}
    if username:
        server["users"] = [{"user": username, "pass": password or ""}]
    return {"tag": tag, "protocol": "socks", "settings": {"servers": [server]}}


def build_route_rule(port: int, outbound_tag: str) -> dict:
    return {"type": "field", "inboundTag": [f"inbound-{port}"], "outboundTag": outbound_tag}


def build_client_link(
    *,
    connect_host: str,
    client_id: str,
    port: int,
    public_key: str,
    short_id: str,
    sni: str,
    fingerprint: str,
    remark: str,
) -> str:
    """按面板导出的格式拼 vless 链接。flow 留空，与手工导出一致。"""
    query = (
        f"type=tcp&security=reality&pbk={public_key}"
        f"&fp={fingerprint}&sni={sni}&sid={short_id}&spx=%2F"
    )
    return f"vless://{client_id}@{connect_host}:{port}?{query}#{quote(remark)}"


# ---------------------------------------------------------------- 主流程
def panel_credentials(db: Session, vps: TransitVps) -> tuple[str | None, str | None]:
    """解密面板凭据。Token 优先；没有 Token 用账号密码。

    密码用与供应商同一套 AES-GCM，AAD 带机器名：贴错机器的密文会解密失败，
    而不是静默拿 A 机的密码去登录 B 机。
    """
    password = decrypt_transit_secret(vps.encrypted_password, scope=f"transit:{vps.name}:password") \
        if vps.encrypted_password else None
    token = decrypt_transit_secret(vps.encrypted_api_token, scope=f"transit:{vps.name}:token") \
        if vps.encrypted_api_token else None
    return password, token


def _remember_exit_credentials(
    provision: TransitProvision,
    *,
    order_id: int,
    index: int,
    host: str,
    port: int | None,
    username: str | None,
    password: str | None,
) -> None:
    """把上游 socks 账密加密存到中转记录。

    采购表出于安全只存 ip/port，不存密码。不在这里留一份，一旦出现
    "入站已建、出站未保存"的中断，重试就得再找上游要密码。
    """
    if provision.encrypted_exit_credentials or not host:
        return
    try:
        provision.encrypted_exit_credentials = encrypt_transit_secret(
            json.dumps({"ip": host, "port": int(port or 0), "username": username, "password": password}),
            scope=f"transit-exit:{order_id}:{index}",
        )
    except TransitCredentialError:
        # 加密密钥没配时，优先把线路交付出去；代价是自动重试需要人工补凭据
        logger.warning("中转出口凭据未加密保存（加密密钥未配置）order=%s index=%s", order_id, index)


def _account_from_record(record: TransitProvision, *, order_id: int):
    """从记录恢复上游账号，供"待配中转"重试使用。恢复不了返回 None。"""
    if not record.encrypted_exit_credentials:
        return None
    try:
        data = json.loads(decrypt_transit_secret(
            record.encrypted_exit_credentials,
            scope=f"transit-exit:{order_id}:{record.line_index}",
        ))
    except (TransitCredentialError, ValueError):
        return None
    if not data.get("ip"):
        return None
    return SimpleNamespace(
        ip=data.get("ip"), host=data.get("ip"), port=data.get("port"),
        username=data.get("username"), password=data.get("password"), expires_at=None,
    )


def _new_key_material(vps: TransitVps) -> dict:
    """新建入站用的密钥材料。

    面板现状（实测 34 条入站）是**每条入站独立一对**，所以默认每条生成；
    机器上显式配了固定密钥时才复用——那是给"想统一 pbk"的场景留的口子。
    """
    private_key = (vps.reality_private_key or "").strip()
    public_key = (vps.reality_public_key or "").strip()
    if not private_key:
        private_key, public_key = generate_reality_keypair()
    elif not public_key:
        public_key = public_key_from_private(private_key) or ""
    return {
        "private_key": private_key,
        "public_key": public_key,
        "short_id": (vps.reality_short_id or "").strip() or generate_short_id(),
        "sni": vps.sni,
        "fingerprint": vps.fingerprint,
    }


def _inbound_key_material(inbound: dict, vps: TransitVps) -> dict:
    """从面板已存在的入站读回密钥，保证重试生成的链接与首次一致。"""
    stream = loads_field(inbound.get("streamSettings"))
    reality = stream.get("realitySettings") or {}
    settings = reality.get("settings") or {}
    private_key = (reality.get("privateKey") or "").strip()
    public_key = (settings.get("publicKey") or "").strip()
    if not public_key and private_key:
        public_key = public_key_from_private(private_key) or ""
    short_ids = [item for item in (reality.get("shortIds") or []) if item]
    server_names = [item for item in (reality.get("serverNames") or []) if item]
    return {
        "private_key": private_key,
        "public_key": public_key,
        "short_id": short_ids[0] if short_ids else (vps.reality_short_id or ""),
        "sni": server_names[0] if server_names else vps.sni,
        "fingerprint": settings.get("fingerprint") or vps.fingerprint,
    }


def _provision_one(
    db: Session,
    *,
    order: Order,
    vps: TransitVps,
    provision: TransitProvision,
    account,
    index: int,
    region_name: str,
    client: XuiClient,
) -> TransitEndpoint:
    """一条线路的中转：入站 → 出站 → 路由 → 重启 → 链接。已完成的步骤不重复做。"""
    exit_ip = (getattr(account, "ip", None) or getattr(account, "host", None) or "").strip()
    exit_port = getattr(account, "port", None)
    exit_user = getattr(account, "username", None)
    exit_pass = getattr(account, "password", None)
    remark = f"{region_name}-{exit_ip}".strip("-")
    # 先把出口账密留档：后面任何一步失败，重试都能靠它重建出站而不必回头找上游
    _remember_exit_credentials(
        provision, order_id=order.id, index=index, host=exit_ip,
        port=exit_port, username=exit_user, password=exit_pass,
    )
    db.flush()

    # 1) 入站：已经有 inbound_id 就先确认面板上还在，避免重复建
    existing = client.get_inbound(provision.inbound_id) if provision.inbound_id else None
    if existing:
        inbound_id = provision.inbound_id
        port = provision.inbound_port or int(existing.get("port") or 0)
        clients = loads_field(existing.get("settings")).get("clients") or []
        client_id = provision.uuid or (str(clients[0].get("id")) if clients else str(uuid_lib.uuid4()))
        # 从面板读回这条入站自己的密钥，重试出来的链接与首次交付必须一字不差
        material = _inbound_key_material(existing, vps)
    else:
        port = provision.inbound_port or allocate_port(vps, client.used_ports())
        client_id = provision.uuid or str(uuid_lib.uuid4())
        material = _new_key_material(vps)
        created = client.add_inbound(build_inbound_payload(
            vps, remark=remark, port=port, client_id=client_id,
            email=f"{order.order_no}-{index}",
            private_key=material["private_key"], public_key=material["public_key"],
            short_id=material["short_id"],
        ))
        inbound_id = created.get("id") if isinstance(created, dict) else None
        if inbound_id is None:
            # 面板没回 id 时按端口反查，仍拿不到就失败（不能留一条对不上的记录）
            match = next((item for item in client.list_inbounds() if int(item.get("port") or 0) == port), None)
            inbound_id = match.get("id") if match else None
        if inbound_id is None:
            raise TransitProvisionError(f"入站已提交但面板查不到端口 {port} 的记录", code="XUI_INBOUND_MISSING")
        provision.inbound_id = int(inbound_id)
        provision.inbound_port = port
        provision.uuid = client_id
        provision.exit_ip = exit_ip
        provision.remark = remark
        provision.status = "inbound_created"
        db.flush()

    # 2) 出站 + 路由：模板全量覆盖写，读改写必须在同一把锁里
    outbound_tag = provision.outbound_tag or f"socks-{order.order_no}-{index}"
    config = client.get_xray_template()
    outbounds = config.setdefault("outbounds", [])
    routing = config.setdefault("routing", {})
    rules = routing.setdefault("rules", [])

    has_outbound = any(item.get("tag") == outbound_tag for item in outbounds)
    if not has_outbound:
        outbounds.append(build_outbound(
            outbound_tag, host=exit_ip, port=int(exit_port or 0),
            username=exit_user, password=exit_pass,
        ))
    inbound_tag = f"inbound-{port}"
    has_rule = any(
        inbound_tag in (rule.get("inboundTag") or []) or rule.get("outboundTag") == outbound_tag
        for rule in rules
    )
    if not has_rule:
        # 插在最后一条 inboundTag 规则之后：规则按顺序匹配，这样既不影响面板上
        # 已有的 api/blocked/bittorrent 规则，也不会把新规则顶到兜底策略后面。
        insert_at = len(rules)
        for pos in range(len(rules) - 1, -1, -1):
            if rules[pos].get("inboundTag"):
                insert_at = pos + 1
                break
        rules.insert(insert_at, build_route_rule(port, outbound_tag))

    if not (has_outbound and has_rule):
        try:
            client.save_xray_template(config)
            client.restart_xray()
        except XuiError as exc:
            provision.status = "inbound_created"
            provision.error_code = exc.code
            provision.error_message = str(exc)[:500]
            write_log(db, "error", "transit", "transit_save_template_failed",
                      f"面板 {vps.name} 保存出站/路由失败：{exc.code}",
                      detail=str(exc)[:800], order_id=order.id, vps_id=vps.id)
            db.flush()
            raise TransitProvisionError(f"入站已建，但出站/路由保存失败：{exc}", code=exc.code) from exc

    link = build_client_link(
        connect_host=vps.connect_host, client_id=client_id, port=port,
        public_key=material["public_key"], short_id=material["short_id"],
        sni=material["sni"], fingerprint=material["fingerprint"], remark=remark,
    )
    provision.outbound_tag = outbound_tag
    provision.exit_ip = exit_ip
    provision.remark = remark
    provision.node_link = link
    provision.status = "configured"
    provision.error_code = None
    provision.error_message = None
    db.flush()

    return TransitEndpoint(
        server=vps.connect_host,
        port=port,
        protocol="vless",
        username=None,
        password=None,
        exit_ip=exit_ip,
        panel_ref=f"{vps.name}#{inbound_id}",
        link=link,
        remark=remark,
    )


def _mark_failure(db: Session, provision: TransitProvision, exc: XuiError) -> None:
    """失败留痕。入站已建时状态保持不变，重试只会补出站与路由。"""
    provision.status = provision.status if provision.inbound_id else "failed"
    provision.error_code = exc.code
    provision.error_message = str(exc)[:500]
    db.flush()


def provision_transit(
    db: Session,
    *,
    order,
    accounts: list,
    country_code: str,
    city_name: str | None = None,
) -> list[TransitEndpoint]:
    """给已经买到的住宅 IP 逐个建立中转，返回客户侧接入信息。

    ``accounts`` 是上游返回的代理账号列表（带 ip / port / username / password）。

    没有可用机器时抛 ``TransitNotConfigured``；面板调用失败抛 ``TransitProvisionError``。
    两种情况下调用方都必须把订单停在 ``pending_transit``——IP 的钱已经花了，
    标成 failed 会诱导人工重买。
    """
    if not accounts:
        raise TransitNotConfigured("没有需要配置中转的住宅 IP")

    group = group_for_country(db, country_code)
    if group is None:
        raise TransitNotConfigured(f"{country_code} 没有配置中转区域，请先在后台补国家映射")

    region = db.get(Region, order.region_id) if getattr(order, "region_id", None) else None
    region_name = (region.name if region else "") or country_code

    endpoints: list[TransitEndpoint] = []
    for index, account in enumerate(accounts, start=1):
        provision = db.scalar(select(TransitProvision).where(
            TransitProvision.order_id == order.id,
            TransitProvision.line_index == index,
        ))
        if provision is not None and provision.status == "configured" and provision.node_link:
            vps_done = db.get(TransitVps, provision.transit_vps_id) if provision.transit_vps_id else None
            endpoints.append(TransitEndpoint(
                server=vps_done.connect_host if vps_done else "",
                port=provision.inbound_port or 0,
                protocol="vless",
                username=None, password=None,
                exit_ip=provision.exit_ip or "",
                panel_ref=f"{provision.transit_vps_id}#{provision.inbound_id}",
                link=provision.node_link,
                remark=provision.remark,
            ))
            continue

        vps = db.get(TransitVps, provision.transit_vps_id) if provision and provision.transit_vps_id else None
        if vps is None:
            vps = pick_vps(db, group)
        if vps is None:
            raise TransitNotConfigured(f"区域 {group} 没有可用中转机（停用或已达承载上限）")

        # 锁住机器：同一台机器的模板改动必须串行，并发读改写会丢配置
        db.execute(select(TransitVps.id).where(TransitVps.id == vps.id).with_for_update())

        if provision is None:
            provision = TransitProvision(
                order_id=order.id, line_index=index, transit_vps_id=vps.id, status="pending",
            )
            db.add(provision)
            db.flush()
        else:
            provision.transit_vps_id = provision.transit_vps_id or vps.id

        try:
            password, token = panel_credentials(db, vps)
            with XuiClient(
                vps.base_url, username=vps.username, password=password, api_token=token,
            ) as client:
                endpoints.append(_provision_one(
                    db, order=order, vps=vps, provision=provision, account=account,
                    index=index, region_name=region_name, client=client,
                ))
        except TransitProvisionError:
            # _provision_one 已经分好类（入站已建 / 保存失败…），不要二次包装把 code 盖掉
            raise
        except (XuiAuthError, XuiConfigError, XuiError) as exc:
            _mark_failure(db, provision, exc)
            raise TransitProvisionError(str(exc), code=exc.code) from exc
        except Exception as exc:  # noqa: BLE001
            # 凭据解密失败、字段缺失这类问题不能让裸异常冒到调用方：
            # 调用方只认 TransitProvisionError，裸异常会把订单卡在"采购中"。
            provision.status = provision.status if provision.inbound_id else "failed"
            provision.error_code = "TRANSIT_UNEXPECTED"
            provision.error_message = f"{type(exc).__name__}: {exc}"[:500]
            db.flush()
            raise TransitProvisionError(f"{type(exc).__name__}: {exc}", code="TRANSIT_UNEXPECTED") from exc

    return endpoints


def retry_provision(db: Session, *, order) -> list[TransitEndpoint]:
    """给已采购但卡在待配中转的订单补做中转。

    走的是同一个 ``provision_transit``：已 configured 的记录直接返回原链接，
    inbound_created 的记录只补出站与路由，不会重复建入站。
    """
    records = db.scalars(
        select(TransitProvision).where(TransitProvision.order_id == order.id)
        .order_by(TransitProvision.line_index)
    ).all()
    if not records:
        raise TransitNotConfigured("该订单没有中转记录，请先完成采购")

    region = db.get(Region, order.region_id) if order.region_id else None
    if region is None or not region.country_code:
        raise TransitNotConfigured("订单缺少国家信息，无法判断走哪组中转机")

    accounts = []
    for record in records:
        account = _account_from_record(record, order_id=order.id)
        if account is None:
            raise TransitNotConfigured(
                f"第 {record.line_index} 条线路缺少出口凭据，"
                "自动重试无法重建 socks 出站，需要人工在面板配置"
            )
        accounts.append(account)
    return provision_transit(db, order=order, accounts=accounts, country_code=region.country_code)
