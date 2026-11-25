"""3x-ui 面板客户端：只做 HTTP 调用，不掺业务判断。

为什么要单独一层：
- 面板版本之间路径会搬（``/panel/xray/update`` 与 ``/panel/api/xray/update`` 都在用过），
  把候选路径集中在这里，业务层不用关心；
- 入站的 ``settings`` / ``streamSettings`` / ``sniffing`` 在 API 里是**嵌套 JSON 字符串**，
  不是对象，拼错一个引号面板就 500，转换只在这一层做；
- 登录有三种形态（Bearer Token / api 组账号密码 / 表单+CSRF），由本类自动降级。

所有失败统一抛 ``XuiError`` 子类，调用方据此决定停在哪个状态，不要吞异常。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Self

import httpx

logger = logging.getLogger(__name__)

INBOUND_LIST_PATH = "/panel/api/inbounds/list"
INBOUND_ADD_PATH = "/panel/api/inbounds/add"
INBOUND_GET_PATH = "/panel/api/inbounds/get/{inbound_id}"
INBOUND_UPDATE_PATH = "/panel/api/inbounds/update/{inbound_id}"
INBOUND_DEL_PATH = "/panel/api/inbounds/del/{inbound_id}"

# 读 Xray 模板的候选路径：先 api 组，再登录组。
# 2026-11-26 实测 2.4.6：GET 都返回 HTML 页面，真正的读取是 **POST /panel/xray/**，
# 返回 {"success":true,"obj":"{\"xraySetting\":{...},\"inboundTags\":[...]}"}。
XRAY_GET_PATHS = (
    "/panel/api/xray/",
    "/panel/xray/",
    "/panel/api/xray/getDefaultJson",
    "/panel/xray/getDefaultJson",
)
# 写 Xray 模板：3x-ui 一直是表单提交 xraySetting 这个 JSON 字符串。
XRAY_UPDATE_PATHS = (
    "/panel/xray/update",
    "/panel/api/xray/update",
)
# 重启：2.4.6 页面上「Restart Xray」按钮打的是 server/restartXrayService（相对 /panel/）
RESTART_PATHS = (
    "/panel/server/restartXrayService",
    "/panel/api/server/restartXray",
    "/panel/server/restartXray",
)

DEFAULT_TIMEOUT = 20.0


class XuiError(Exception):
    """面板调用失败。``code`` 落库，``detail`` 给人看。"""

    def __init__(self, message: str, *, code: str = "XUI_ERROR", status: int | None = None):
        super().__init__(message)
        self.code = code
        self.status = status


class XuiAuthError(XuiError):
    def __init__(self, message: str = "3x-ui 面板登录失败", *, status: int | None = None):
        super().__init__(message, code="XUI_AUTH_FAILED", status=status)


class XuiConfigError(XuiError):
    """面板返回了 200 但内容不是预期结构（版本差异/配置损坏）。"""

    def __init__(self, message: str, *, code: str = "XUI_BAD_RESPONSE"):
        super().__init__(message, code=code)


def loads_field(value: Any) -> dict:
    """把面板返回的嵌套 JSON 字符串字段转成 dict。空的按空对象处理。"""
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except ValueError:
            return {}
    return {}


def dumps_field(value: dict) -> str:
    """转回面板要的字符串形态。面板不接受对象，只接受字符串。"""
    return json.dumps(value, ensure_ascii=False)


class XuiClient:
    def __init__(
        self,
        base_url: str,
        *,
        username: str | None = None,
        password: str | None = None,
        api_token: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        verify_tls: bool = False,
    ) -> None:
        self.base = (base_url or "").rstrip("/")
        if not self.base:
            raise XuiConfigError("3x-ui 面板地址为空", code="XUI_NO_BASE_URL")
        if not api_token and not (username and password):
            raise XuiConfigError("缺少面板凭据（Token 或账号密码）", code="XUI_NO_CREDENTIAL")
        self.api_token = api_token or None
        self.username = username or None
        self.password = password or None
        headers = {"Accept": "application/json"}
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"
        self.client = httpx.Client(
            timeout=timeout, verify=verify_tls, follow_redirects=True, headers=headers,
        )
        self._logged_in = bool(self.api_token)

    # ---- 生命周期 -------------------------------------------------------
    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # ---- 登录 -----------------------------------------------------------
    def login(self) -> None:
        if self._logged_in:
            return
        if not (self.username and self.password):
            raise XuiAuthError()
        attempts: list[str] = []
        # 1) 新版 api 组：JSON 登录，成功后 cookie 自动留在 jar 里
        status, body = self._raw("POST", "/panel/api/login",
                                 json_body={"username": self.username, "password": self.password})
        if status == 200 and isinstance(body, dict) and body.get("success"):
            self._logged_in = True
            return
        attempts.append(f"/panel/api/login={status}")
        # 2) 表单登录（老版本）
        status, body = self._raw("POST", "/login",
                                 data={"username": self.username, "password": self.password})
        if status == 200 and isinstance(body, dict) and body.get("success"):
            self._logged_in = True
            return
        attempts.append(f"/login={status}")
        # 3) 带 CSRF 的表单登录：先访问首页种 cookie，再换 token
        csrf = self._csrf_token()
        headers = {"X-CSRF-Token": csrf} if csrf else {}
        status, body = self._raw("POST", "/login", headers=headers,
                                 data={"username": self.username, "password": self.password})
        if status == 200 and isinstance(body, dict) and body.get("success"):
            self._logged_in = True
            return
        attempts.append(f"/login+csrf={status}")
        raise XuiAuthError("3x-ui 面板登录失败（" + "，".join(attempts) + "）")

    def _csrf_token(self) -> str:
        self._raw("GET", "/", expect_json=False)
        status, payload = self._raw("GET", "/csrf-token", headers={"X-Requested-With": "XMLHttpRequest"})
        if status != 200 or not isinstance(payload, dict):
            return ""
        obj = payload.get("obj") or payload.get("token")
        return obj if isinstance(obj, str) else ""

    # ---- 底层请求 -------------------------------------------------------
    def _raw(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        data: Any = None,
        headers: dict | None = None,
        expect_json: bool = True,
    ) -> tuple[int, Any]:
        url = f"{self.base}{path}"
        try:
            resp = self.client.request(method, url, json=json_body, data=data, headers=headers)
        except httpx.HTTPError as exc:
            raise XuiError(f"面板请求失败：{type(exc).__name__} {exc}", code="XUI_NETWORK") from exc
        if resp.status_code in (401, 403):
            raise XuiAuthError(f"面板拒绝访问（{resp.status_code}），请检查账号或 Token", status=resp.status_code)
        if not expect_json:
            return resp.status_code, None
        try:
            return resp.status_code, resp.json()
        except ValueError:
            return resp.status_code, (resp.text or "")[:500]

    def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        self.login()
        status, body = self._raw(method, path, **kwargs)
        if status >= 400:
            raise XuiError(f"{path} 返回 {status}：{str(body)[:200]}", code="XUI_HTTP", status=status)
        if not isinstance(body, dict):
            raise XuiConfigError(f"{path} 返回非 JSON：{str(body)[:200]}")
        if body.get("success") is False:
            raise XuiError(f"{path} 失败：{body.get('msg') or body}", code="XUI_REJECTED", status=status)
        return body.get("obj")

    # ---- 入站 -----------------------------------------------------------
    def list_inbounds(self) -> list[dict]:
        obj = self._call("GET", INBOUND_LIST_PATH)
        if isinstance(obj, list):
            return obj
        if isinstance(obj, dict) and isinstance(obj.get("items"), list):
            return obj["items"]
        return []

    def get_inbound(self, inbound_id: int) -> dict | None:
        """查不到时返回 None：幂等重试要靠它判断入站是否已经建过。"""
        try:
            obj = self._call("GET", INBOUND_GET_PATH.format(inbound_id=inbound_id))
        except XuiError as exc:
            if exc.status == 404:
                return None
            raise
        return obj if isinstance(obj, dict) else None

    def add_inbound(self, payload: dict) -> dict:
        obj = self._call("POST", INBOUND_ADD_PATH, json_body=payload)
        return obj if isinstance(obj, dict) else {}

    def update_inbound(self, inbound_id: int, payload: dict) -> dict:
        obj = self._call("POST", INBOUND_UPDATE_PATH.format(inbound_id=inbound_id), json_body=payload)
        return obj if isinstance(obj, dict) else {}

    def delete_inbound(self, inbound_id: int) -> None:
        self._call("POST", INBOUND_DEL_PATH.format(inbound_id=inbound_id))

    def used_ports(self) -> set[int]:
        ports: set[int] = set()
        for item in self.list_inbounds():
            try:
                ports.add(int(item.get("port")))
            except (TypeError, ValueError):
                continue
        return ports

    # ---- Xray 模板（出站 + 路由） ---------------------------------------
    def get_xray_template(self) -> dict:
        """读当前 Xray 模板。

        2.4.x 的正确姿势是 POST `/panel/xray/`（GET 返回的是页面 HTML），
        这里先按 POST 试一遍，再回退 GET 候选，兼容老版本。
        """
        self.login()
        errors: list[str] = []
        status, body = self._raw("POST", "/panel/xray/")
        if status == 200 and isinstance(body, dict) and body.get("success") is not False:
            template = _extract_template(body)
            if template:
                return template
        errors.append(f"POST /panel/xray/={status}")
        for path in XRAY_GET_PATHS:
            status, body = self._raw("GET", path)
            if status != 200:
                errors.append(f"{path}={status}")
                continue
            template = _extract_template(body)
            if template:
                return template
            errors.append(f"{path}=empty")
        raise XuiConfigError("读不到 Xray 模板（" + "，".join(errors) + "）", code="XUI_NO_TEMPLATE")

    def save_xray_template(self, config: dict) -> None:
        self.login()
        payload = {"xraySetting": dumps_field(config)}
        errors: list[str] = []
        for path in XRAY_UPDATE_PATHS:
            status, _body = self._raw("POST", path, data=payload, expect_json=False)
            if status < 400:
                return
            errors.append(f"{path}={status}")
        raise XuiError("保存 Xray 模板失败（" + "，".join(errors) + "）", code="XUI_SAVE_TEMPLATE")

    def restart_xray(self) -> None:
        """保存模板后必须重启才生效。重启会让同机客户瞬断，调用方要合并变更。"""
        self.login()
        errors: list[str] = []
        for path in RESTART_PATHS:
            status, _body = self._raw("POST", path, expect_json=False)
            if status < 400:
                return
            errors.append(f"{path}={status}")
        raise XuiError("重启 Xray 失败（" + "，".join(errors) + "）", code="XUI_RESTART")


def _extract_template(body: Any) -> dict:
    """把面板返回的模板剥成纯 xray 配置。

    实测 2.4.6：``{success, msg, obj}`` 里的 obj 是**字符串**，
    再解析一层是 ``{"xraySetting": {...}, "inboundTags": [...]}``；
    老版本可能直接给对象。这里逐层剥，剥到含 outbounds/routing 的那一层。
    """
    candidate: Any = body
    if isinstance(body, dict):
        candidate = body.get("obj", body)

    for _ in range(3):
        if isinstance(candidate, str):
            try:
                candidate = json.loads(candidate)
            except ValueError:
                return {}
            continue
        if isinstance(candidate, dict) and isinstance(candidate.get("xraySetting"), (dict, str)):
            candidate = candidate["xraySetting"]
            continue
        break

    if not isinstance(candidate, dict):
        return {}
    if "outbounds" not in candidate and "routing" not in candidate:
        return {}
    return candidate
