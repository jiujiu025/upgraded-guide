from ipaddress import ip_address

from fastapi import Request

from app.core.config import get_settings


def resolve_client_ip(peer_host: str | None, forwarded_for: str | None, trusted_networks) -> str:
    """仅从可信直连代理接收转发链，并从右向左剥离可信代理。"""
    try:
        peer = ip_address(peer_host or "")
    except ValueError:
        return "unknown"
    if not any(peer in network for network in trusted_networks) or not forwarded_for:
        return str(peer)

    forwarded = []
    try:
        for value in forwarded_for.split(","):
            forwarded.append(ip_address(value.strip()))
    except ValueError:
        return str(peer)
    if not forwarded:
        return str(peer)

    for address in reversed(forwarded):
        if any(address in network for network in trusted_networks):
            continue
        return str(address)
    return str(forwarded[0])


def get_client_ip(request: Request) -> str:
    settings = get_settings()
    peer_host = request.client.host if request.client else None
    return resolve_client_ip(peer_host, request.headers.get("x-forwarded-for"), settings.trusted_proxy_networks)
