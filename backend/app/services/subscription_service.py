"""客户订阅：把客户名下线路的分享链接聚合成 Clash 配置或通用订阅。

订阅地址是**客户级**的：一个 token 对应一个客户，返回他名下所有有效线路。客户换了线路
不用换订阅地址，客户端里更新一次就同步了。

token 是无状态签名（customer_id + HMAC），不落明文：数据库里只有线路的 token 哈希，
即使库被读走也拼不出可用订阅地址。签名的用途只是「证明这个客户确实存在」，不承担登录态，
所以订阅接口允许匿名访问（Clash 拉订阅时没有 Cookie）。
"""
from __future__ import annotations

import base64
import hashlib
import hmac

import yaml

from app.core.config import get_settings
from app.services.proxy_link import ParsedNode, ProxyLinkError, parse_link, to_clash_proxy

SUBSCRIBE_PREFIX = "99ipxin:subscription:v1"


def _secret() -> bytes:
    settings = get_settings()
    raw = settings.jwt_secret_key or "99ipxin-subscription"
    return f"{SUBSCRIBE_PREFIX}:{raw}".encode()


def _sign(customer_id: int) -> str:
    digest = hmac.new(_secret(), f"customer:{customer_id}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")[:24]


def issue_token(customer_id: int) -> str:
    """生成客户订阅 token：{customer_id 的 base64}.{签名}，可直接放进 URL。"""
    payload = base64.urlsafe_b64encode(str(customer_id).encode()).decode().rstrip("=")
    return f"{payload}.{_sign(customer_id)}"


def parse_token(token: str) -> int | None:
    """校验并取出 customer_id；签名不对或格式损坏返回 None。"""
    try:
        payload, signature = (token or "").split(".", 1)
    except ValueError:
        return None
    try:
        customer_id = int(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (ValueError, TypeError):
        return None
    if customer_id <= 0:
        return None
    # 定长比较，避免按字节试探签名
    return customer_id if hmac.compare_digest(_sign(customer_id), signature) else None


def nodes_from_links(links: list[str]) -> list[ParsedNode]:
    """解析线路里的分享链接；解析不了的单条跳过，不能让一条坏链接拖垮整个订阅。"""
    nodes: list[ParsedNode] = []
    for link in links:
        if not link:
            continue
        try:
            nodes.append(parse_link(link))
        except ProxyLinkError:
            continue
    return nodes


def _unique_tag(name: str, used: set[str]) -> str:
    """Clash 里节点重名会直接报错，重名时补序号。"""
    base = name or "线路"
    tag = base
    index = 2
    while tag in used:
        tag = f"{base}-{index}"
        index += 1
    used.add(tag)
    return tag


def build_clash_config(links: list[str], *, subscription_name: str = "99IP") -> str:
    """生成 Clash / mihomo 可直接导入的完整配置。

    没有可用节点时也给一份能加载的配置（只有 DIRECT），避免客户端更新时直接报错。
    """
    nodes = nodes_from_links(links)
    used: set[str] = set()
    proxies = [to_clash_proxy(node, _unique_tag(node.name, used)) for node in nodes]
    names = [str(proxy["name"]) for proxy in proxies]

    config: dict = {
        "mixed-port": 7890,
        "allow-lan": False,
        "mode": "rule",
        "log-level": "info",
        "proxies": proxies,
        # 每组各拿一份 list：共用同一个对象时 pyyaml 会写成 &id001 / *id001 锚点，
        # 虽然合法，但有些客户端自带的 YAML 解析器对锚点支持不完整，直接展开更稳。
        "proxy-groups": [{
            "name": "节点选择",
            "type": "select",
            "proxies": list(names) or ["DIRECT"],
        }] + ([{
            "name": "自动选择",
            "type": "url-test",
            "url": "http://www.gstatic.com/generate_204",
            "interval": 300,
            "proxies": list(names),
        }] if names else []),
        "rules": [f"MATCH,{'节点选择' if names else 'DIRECT'}"],
    }
    header = f"# {subscription_name} 自动生成，节点随线路到期自动失效\n"
    return header + yaml.safe_dump(config, allow_unicode=True, sort_keys=False, default_flow_style=False)


def build_base64_subscription(links: list[str]) -> str:
    """通用订阅：换行拼接的分享链接再整体 base64，v2rayN / 小火箭 / NekoBox 都认。"""
    nodes = nodes_from_links(links)
    raw = "\n".join(node.raw for node in nodes)
    return base64.b64encode(raw.encode("utf-8")).decode("ascii")


def wants_clash(user_agent: str, explicit_target: str | None) -> bool:
    """按 target 参数或 UA 判断返回哪种格式。

    Clash 系客户端会带 `clash` / `mihomo`；v2rayN、小火箭、NekoBox 期望 base64 列表。
    """
    if explicit_target:
        return explicit_target.lower() in {"clash", "mihomo", "yaml"}
    ua = (user_agent or "").lower()
    return any(token in ua for token in ("clash", "mihomo", "stash"))
