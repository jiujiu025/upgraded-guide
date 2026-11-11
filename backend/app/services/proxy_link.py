"""代理分享链接的解析与反向生成。

交付时粘贴一条分享链接（vless / vmess / trojan / ss / hysteria2 / tuic），解析成结构化
节点，用于三件事：

1. 自动创建或复用 Node（地区仍按订单地区校验，不从链接里猜国家）；
2. 生成 Clash 订阅里的 proxy 条目；
3. 手动模式下把表单拼回一条标准链接。

不认识的协议、缺 host/端口/凭据一律抛 ProxyLinkError，由调用方转 422——不做猜测性修复。
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from urllib.parse import parse_qs, quote, unquote, urlsplit

SUPPORTED_PROTOCOLS = ("vless", "vmess", "trojan", "ss", "hysteria2", "tuic", "socks5", "http")

# 上游交付的静态住宅就是代理账号（socks5/http + 账密），不是隧道协议，
# 这里把它们也当成一种"节点"，才能走同一套二维码与订阅。
PROXY_ACCOUNT_PROTOCOLS = {"socks5", "http"}

# 这些协议默认走 TLS，链接里没写 security 时按 tls 处理（Clash 侧需要 servername）。
TLS_DEFAULT_PROTOCOLS = {"trojan", "hysteria2", "tuic"}


class ProxyLinkError(ValueError):
    """链接无法解析：协议不支持或字段缺失。"""


@dataclass(frozen=True)
class ParsedNode:
    protocol: str
    server: str
    port: int
    # vless/vmess/tuic 是 uuid；trojan/hysteria2/ss 是密码（ss 是 "method:password"）
    credential: str
    name: str = ""
    params: dict[str, str] = field(default_factory=dict)
    raw: str = ""

    @property
    def security(self) -> str:
        explicit = (self.params.get("security") or "").lower()
        if explicit:
            return explicit
        return "tls" if self.protocol in TLS_DEFAULT_PROTOCOLS else "none"

    @property
    def sni(self) -> str:
        return self.params.get("sni") or self.params.get("peer") or self.params.get("host") or ""


def _b64decode(value: str) -> str:
    """分享链接里的 base64 常常缺 padding，且可能用 urlsafe 字符集。"""
    text = value.strip().replace("\n", "").replace(" ", "")
    padding = "=" * (-len(text) % 4)
    try:
        return base64.urlsafe_b64decode(text + padding).decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001 - 统一转成自己的错误类型
        raise ProxyLinkError("链接里的 base64 数据无法解码") from exc


def _check_host_port(server: str, port: int) -> None:
    if not server:
        raise ProxyLinkError("链接缺少服务器地址")
    if not 1 <= port <= 65535:
        raise ProxyLinkError("链接端口不在 1-65535 之间")


def _split_host_port(netloc: str) -> tuple[str, int]:
    """host:port，兼容 IPv6 的 [::1]:443 写法。"""
    try:
        parts = urlsplit(f"//{netloc}")
        host, port = parts.hostname or "", parts.port
    except ValueError as exc:
        raise ProxyLinkError("服务器地址格式无法识别") from exc
    if port is None:
        raise ProxyLinkError("链接缺少端口")
    _check_host_port(host, port)
    return host, port


def _query_params(query: str) -> dict[str, str]:
    parsed = parse_qs(query or "", keep_blank_values=False)
    return {key: values[0] for key, values in parsed.items() if values and values[0]}


def _parse_standard(raw: str, protocol: str) -> ParsedNode:
    """vless/trojan/hysteria2/tuic：userinfo@host:port?query#fragment"""
    parts = urlsplit(raw)
    credential = unquote(parts.username or "")
    if not credential:
        raise ProxyLinkError(f"{protocol} 链接缺少凭据（uuid 或密码）")
    # hysteria2/tuic 支持 user:pass 两段式，把 password 拼回去更接近原意
    if parts.password:
        credential = f"{credential}:{unquote(parts.password)}"
    host, port = _split_host_port(parts.netloc)
    return ParsedNode(
        protocol=protocol,
        server=host,
        port=port,
        credential=credential,
        name=unquote(parts.fragment or ""),
        params=_query_params(parts.query),
        raw=raw,
    )


def _parse_vmess(raw: str) -> ParsedNode:
    body = raw.split("://", 1)[1].strip()
    payload = _b64decode(body)
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ProxyLinkError("vmess 链接的 base64 内容不是合法 JSON") from exc
    if not isinstance(data, dict):
        raise ProxyLinkError("vmess 链接格式无效")

    server = str(data.get("add") or "").strip()
    try:
        port = int(str(data.get("port") or "").strip())
    except (TypeError, ValueError) as exc:
        raise ProxyLinkError("vmess 端口无效") from exc
    _check_host_port(server, port)
    credential = str(data.get("id") or "").strip()
    if not credential:
        raise ProxyLinkError("vmess 链接缺少 uuid")

    # 把 v2ray 的字段名换成统一参数名，下游（Clash 生成）只认这一套
    params: dict[str, str] = {}
    if data.get("net"):
        params["type"] = str(data["net"])
    if data.get("tls"):
        params["security"] = str(data["tls"])
    if data.get("sni"):
        params["sni"] = str(data["sni"])
    elif data.get("host"):
        params["host"] = str(data["host"])
    for source, target in (("path", "path"), ("host", "host"), ("type", "headerType"),
                           ("alpn", "alpn"), ("fp", "fp")):
        if data.get(source) not in (None, ""):
            params[target] = str(data[source])
    if data.get("aid") not in (None, "", 0, "0"):
        params["alterId"] = str(data["aid"])
    if data.get("scy"):
        params["cipher"] = str(data["scy"])

    return ParsedNode(
        protocol="vmess",
        server=server,
        port=port,
        credential=credential,
        name=str(data.get("ps") or ""),
        params=params,
        raw=raw,
    )


def _parse_ss(raw: str) -> ParsedNode:
    """SIP002：ss://base64(method:password)@host:port#name，也兼容整段 base64 的老格式。"""
    body = raw.split("://", 1)[1]
    fragment = ""
    if "#" in body:
        body, fragment = body.split("#", 1)
    query = ""
    if "?" in body:
        body, query = body.split("?", 1)

    if "@" in body:
        userinfo, netloc = body.rsplit("@", 1)
        method_password = unquote(userinfo)
        if ":" not in method_password:
            method_password = _b64decode(method_password)
    else:
        # 老格式：整段 base64 里包含 method:password@host:port
        decoded = _b64decode(body)
        if "@" not in decoded:
            raise ProxyLinkError("ss 链接格式无法识别")
        userinfo, netloc = decoded.rsplit("@", 1)
        method_password = userinfo

    if ":" not in method_password:
        raise ProxyLinkError("ss 链接缺少加密方式或密码")
    host, port = _split_host_port(netloc)
    params = _query_params(query)
    return ParsedNode(
        protocol="ss",
        server=host,
        port=port,
        credential=method_password,
        name=unquote(fragment),
        params=params,
        raw=raw,
    )


def parse_link(raw: str) -> ParsedNode:
    """把一条分享链接解析成 ParsedNode；失败抛 ProxyLinkError。"""
    text = (raw or "").strip()
    if not text:
        raise ProxyLinkError("链接为空")
    if "://" not in text:
        raise ProxyLinkError("不是合法的分享链接（缺少协议头）")
    scheme = text.split("://", 1)[0].strip().lower()
    if scheme == "hy2":
        scheme = "hysteria2"
    if scheme == "https":
        scheme = "http"
    if scheme not in SUPPORTED_PROTOCOLS:
        raise ProxyLinkError(f"暂不支持 {scheme} 协议，目前支持 {' / '.join(SUPPORTED_PROTOCOLS)}")

    if scheme == "vmess":
        node = _parse_vmess(text)
    elif scheme == "ss":
        node = _parse_ss(text)
    else:
        node = _parse_standard(text, scheme)
    if not node.name:
        # 没写备注时用「协议-服务器」当名字，客户在客户端里能认出来
        object.__setattr__(node, "name", f"{node.protocol}-{node.server}")
    return node


def _credential_parts(node: ParsedNode) -> tuple[str, str]:
    """ss 的凭据是 method:password，其他协议整段就是密码/uuid。"""
    if node.protocol == "ss" and ":" in node.credential:
        method, password = node.credential.split(":", 1)
        return method, password
    return "", node.credential


def to_clash_proxy(node: ParsedNode, tag: str) -> dict:
    """转成 Clash / mihomo 的 proxy 条目。字段按各协议的实际要求给，不塞无用键。"""
    proxy: dict = {"name": tag, "type": node.protocol, "server": node.server, "port": node.port}
    method, secret = _credential_parts(node)
    params = node.params

    if node.protocol in PROXY_ACCOUNT_PROTOCOLS:
        # socks5://user:pass@host:port —— 用户名口令拆成 Clash 的两个字段
        username, _, password = secret.partition(":")
        proxy["type"] = node.protocol
        if username:
            proxy["username"] = username
            proxy["password"] = password
        return proxy

    if node.protocol == "ss":
        proxy["cipher"] = method or "aes-256-gcm"
        proxy["password"] = secret
    elif node.protocol == "vmess":
        proxy["uuid"] = node.credential
        proxy["alterId"] = int(params.get("alterId") or 0)
        proxy["cipher"] = params.get("cipher") or "auto"
    elif node.protocol in ("vless", "tuic"):
        proxy["uuid"] = node.credential
        if node.protocol == "tuic" and ":" in node.credential:
            uuid, password = node.credential.split(":", 1)
            proxy["uuid"], proxy["password"] = uuid, password
    else:  # trojan / hysteria2
        proxy["password"] = secret

    network = params.get("type") or "tcp"
    if node.protocol in ("vless", "vmess", "trojan"):
        if network in ("ws", "grpc", "h2", "http"):
            proxy["network"] = network
        if network == "ws":
            ws_opts: dict = {"path": params.get("path") or "/"}
            if params.get("host"):
                ws_opts["headers"] = {"Host": params["host"]}
            proxy["ws-opts"] = ws_opts
        elif network == "grpc":
            proxy["grpc-opts"] = {"grpc-service-name": params.get("serviceName") or params.get("path") or ""}
        elif network == "h2":
            proxy["h2-opts"] = {"path": params.get("path") or "/",
                                "host": [params["host"]] if params.get("host") else []}

    tls = node.security in ("tls", "reality", "xtls")
    if tls:
        proxy["tls"] = True
        if node.sni:
            proxy["servername"] = node.sni
        if params.get("alpn"):
            proxy["alpn"] = [item for item in params["alpn"].split(",") if item]
        if params.get("fp"):
            proxy["client-fingerprint"] = params["fp"]
        if params.get("allowInsecure") in ("1", "true", "True"):
            proxy["skip-cert-verify"] = True
    if node.security == "reality":
        reality: dict = {}
        if params.get("pbk"):
            reality["public-key"] = params["pbk"]
        if params.get("sid"):
            reality["short-id"] = params["sid"]
        if reality:
            proxy["reality-opts"] = reality
    if node.protocol == "vless" and params.get("flow"):
        proxy["flow"] = params["flow"]
    return proxy


def to_share_link(node: ParsedNode) -> str:
    """手动模式：把结构化字段拼回一条标准分享链接。"""
    params = {key: value for key, value in node.params.items() if value}
    query = "&".join(f"{quote(str(key))}={quote(str(value))}" for key, value in params.items())
    host = f"[{node.server}]" if ":" in node.server else node.server
    link = f"{node.protocol}://{quote(node.credential, safe=':@')}@{host}:{node.port}"
    if query:
        link += f"?{query}"
    if node.name:
        link += f"#{quote(node.name)}"
    return link
