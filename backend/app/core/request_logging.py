"""给每个请求一行带 request_id 的访问日志。

uvicorn 的 access log 只有 ``GET /api/x 200``：没有耗时、没有 request_id，也
不区分"谁"访问的。线上出现"刚才那单报错了"时，这三样恰是唯一能对上号的线索。

实现是纯 ASGI 中间件（不是 BaseHTTPMiddleware）：财务导出用 ``StreamingResponse``
分块返回 CSV，BaseHTTPMiddleware 会额外包一层流并可能拖住响应，纯 ASGI 只在
``http.response.start`` 上加个头，不碰 body。

request_id 优先复用上游传入的 ``X-Request-ID``（反代一般会带），并校验字符集和
长度——日志行是纯文本，未校验的头部会变成日志注入。没带或不合规就现生成一个，
两种情况都把最终值写回响应头，用户报障时可以直接提供这个值。
"""

import logging
import re
import uuid
from time import perf_counter

from starlette.datastructures import Headers, MutableHeaders

from app.core.client_ip import resolve_client_ip
from app.core.config import get_settings

logger = logging.getLogger("app.request")

# 只放行常见的追踪 ID 字符：字母数字、点、下划线、短横线。长度卡在 8~64，
# 既够放 uuid/trace id，又不会让超长头部把日志文件撑爆。
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{8,64}$")

# 编排层每 30 秒探一次，按 INFO 记会淹没真正的业务日志。
_QUIET_PATHS = frozenset({"/healthz", "/readyz"})


def sanitized_request_id(raw: str | None) -> str:
    if raw and _REQUEST_ID_PATTERN.match(raw):
        return raw
    return uuid.uuid4().hex


class RequestLoggingMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        request_id = sanitized_request_id(headers.get("x-request-id"))
        scope.setdefault("state", {})["request_id"] = request_id

        status_code = 500
        started = perf_counter()

        async def send_with_request_id(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                MutableHeaders(scope=message)["X-Request-ID"] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            # 异常也要落一行：没有这行的话，"请求打进来然后没下文"就只能靠猜。
            self._log(scope, headers, status_code, started, request_id)

    def _log(self, scope, headers: Headers, status_code: int, started: float, request_id: str) -> None:
        path = scope.get("path", "")
        peer_host = scope["client"][0] if scope.get("client") else None
        client_ip = resolve_client_ip(peer_host, headers.get("x-forwarded-for"), get_settings().trusted_proxy_networks)
        level = logging.DEBUG if path in _QUIET_PATHS else logging.INFO
        if status_code >= 500:
            level = logging.WARNING
        logger.log(
            level,
            "request method=%s path=%s status=%s duration_ms=%.1f request_id=%s client=%s",
            scope.get("method", ""),
            path,
            status_code,
            (perf_counter() - started) * 1000,
            request_id,
            client_ip,
        )
