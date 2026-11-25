"""3x-ui 面板只读探测：确认 API 契约，不改动任何配置。

为什么先探测再写适配器：
- 不同 3x-ui 版本的路由前缀、鉴权方式（Bearer / session cookie / CSRF）不一致；
- 入站的 settings / streamSettings / sniffing 是**嵌套 JSON 字符串**，字段大小写各版本有差异；
- Xray 模板（outbounds + routing）的读写路径在 api 组和登录组之间来回搬过。

本脚本只发 GET 和一次登录 POST，绝不调用 add / update / del / restart。
探测结果用来确定 ``app/services/xui_client.py`` 的实现细节。

用法：
    # 推荐：面板「设置 → 安全」里建 API Token
    python -m scripts.xui_probe --base https://1.2.3.4:54321/panelpath --token xxx

    # 或者用面板账号密码（会尝试 /panel/api/login 和 /login 两种入口）
    python -m scripts.xui_probe --base https://1.2.3.4:54321/panelpath --user admin --password xxx

    # 把原始响应存下来（含私钥，注意保存位置）
    python -m scripts.xui_probe ... --save-raw ./xui-raw
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import httpx

# 面板自身常用自签证书，探测阶段允许跳过校验；正式适配器由配置决定。
DEFAULT_TIMEOUT = 15.0

INBOUND_ENDPOINTS = [
    "/panel/api/inbounds/list",
    "/panel/api/inbounds/options",
    "/panel/api/inbounds/list/slim",
]
SERVER_ENDPOINTS = [
    "/panel/api/server/status",
    "/panel/api/server/getXrayVersion",
]
# Xray 模板的候选路径：api 组与登录组都试一遍，看哪个真的返回配置。
XRAY_ENDPOINTS = [
    "/panel/api/xray/getDefaultJson",
    "/panel/api/xray/",
    "/panel/api/xray/template",
    "/panel/xray/getDefaultJson",
    "/panel/xray/",
]


def _base(value: str) -> str:
    return value.rstrip("/")


def _mask(secret: str | None, keep: int = 6) -> str:
    if not secret:
        return "(空)"
    if len(secret) <= keep:
        return "*" * len(secret)
    return f"{secret[:keep]}...({len(secret)} 位)"


def _login_with_password(client: httpx.Client, base: str, user: str, password: str) -> str:
    """返回一句人类可读的登录结果。不抛异常，让后续探测自己暴露 401。"""
    # 1) 新版 api 组登录：JSON body，直接种 cookie
    try:
        resp = client.post(f"{base}/panel/api/login", json={"username": user, "password": password})
        if resp.status_code == 200 and _ok(resp):
            return f"[OK] /panel/api/login 登录成功（{resp.status_code}）"
        first = f"/panel/api/login -> {resp.status_code} {_snippet(resp)}"
    except httpx.HTTPError as exc:
        first = f"/panel/api/login -> {type(exc).__name__}"
    # 2) 老版表单登录
    try:
        resp = client.post(f"{base}/login", data={"username": user, "password": password})
        if resp.status_code == 200 and _ok(resp):
            return f"[OK] /login 表单登录成功（{resp.status_code}）"
        second = f"/login -> {resp.status_code} {_snippet(resp)}"
    except httpx.HTTPError as exc:
        second = f"/login -> {type(exc).__name__}"
    # 3) CSRF 流程：先拿首页 cookie，再换 csrf-token，最后带 header 登录
    try:
        client.get(f"{base}/")
        csrf = client.get(f"{base}/csrf-token", headers={"X-Requested-With": "XMLHttpRequest"})
        token = ""
        if csrf.status_code == 200:
            body = csrf.json() if "json" in csrf.headers.get("content-type", "") else {}
            token = (body.get("obj") or body.get("token") or "") if isinstance(body, dict) else ""
        headers = {"X-CSRF-Token": token} if token else {}
        resp = client.post(f"{base}/login", data={"username": user, "password": password}, headers=headers)
        if resp.status_code == 200 and _ok(resp):
            return f"[OK] /login + CSRF 登录成功（csrf={'有' if token else '无'}）"
        third = f"/login(csrf) -> {resp.status_code} {_snippet(resp)}"
    except (httpx.HTTPError, ValueError) as exc:
        third = f"/login(csrf) -> {type(exc).__name__}"
    return f"[!!] 三种登录方式都失败：{first} | {second} | {third}"


def _ok(resp: httpx.Response) -> bool:
    try:
        body = resp.json()
    except ValueError:
        return False
    return bool(isinstance(body, dict) and body.get("success"))


def _snippet(resp: httpx.Response, limit: int = 120) -> str:
    text = (resp.text or "").replace("\n", " ").strip()
    return text[:limit]


def _try_post(client: httpx.Client, path: str) -> tuple[int, Any]:
    try:
        resp = client.post(path)
        try:
            return resp.status_code, resp.json()
        except ValueError:
            return resp.status_code, _snippet(resp, 200)
    except httpx.HTTPError as exc:
        return 0, f"{type(exc).__name__}: {exc}"


def _try_get(client: httpx.Client, path: str) -> tuple[int, Any]:
    try:
        resp = client.get(path)
        try:
            return resp.status_code, resp.json()
        except ValueError:
            return resp.status_code, _snippet(resp, 200)
    except httpx.HTTPError as exc:
        return 0, f"{type(exc).__name__}: {exc}"


def _summarize_inbounds(items: list[dict]) -> None:
    print(f"    共 {len(items)} 条入站")
    for item in items:
        settings = _loads(item.get("settings"))
        stream = _loads(item.get("streamSettings"))
        clients = settings.get("clients") or []
        reality = (stream.get("realitySettings") or {})
        print(
            "    - id={id} remark={remark!r} port={port} proto={proto} security={sec} "
            "clients={n} sni={sni}".format(
                id=item.get("id"),
                remark=(item.get("remark") or "")[:40],
                port=item.get("port"),
                proto=item.get("protocol"),
                sec=stream.get("security"),
                n=len(clients),
                sni=(reality.get("serverNames") or [""])[0],
            )
        )
        if clients and item.get("protocol") == "vless":
            client0 = clients[0]
            print(
                "      client: id={cid} flow={flow!r} email={email!r}".format(
                    cid=_mask(str(client0.get("id", "")), 8),
                    flow=client0.get("flow"),
                    email=client0.get("email"),
                )
            )
        if stream.get("security") == "reality":
            print(
                "      reality: privateKey={pk} shortIds={sids} dest={dest} fp={fp}".format(
                    pk=_mask(reality.get("privateKey"), 8),
                    sids=reality.get("shortIds"),
                    dest=reality.get("dest"),
                    fp=(reality.get("settings") or {}).get("fingerprint"),
                )
            )
        print(f"      settings 键: {sorted(settings.keys())}")
        print(f"      streamSettings 键: {sorted(stream.keys())}")


def _summarize_xray(payload: Any) -> None:
    """逐层剥：{obj: "..."} → {"xraySetting": {...}} → 纯 xray 配置。"""
    template: Any = payload
    for _ in range(4):
        if isinstance(template, dict) and "obj" in template and "outbounds" not in template:
            template = template["obj"]
            continue
        if isinstance(template, str):
            try:
                template = json.loads(template) if template.strip() else {}
            except ValueError:
                print(f"    [!!] 不是 JSON：{template[:120]}")
                return
            continue
        if isinstance(template, dict) and isinstance(template.get("xraySetting"), (dict, str)):
            template = template["xraySetting"]
            continue
        break
    if not isinstance(template, dict):
        print(f"    [!!] 返回的不是配置对象：{str(template)[:160]}")
        return
    outbounds = template.get("outbounds") or []
    routing = template.get("routing") or {}
    rules = routing.get("rules") or []
    print(f"    顶层键: {sorted(template.keys())}")
    print(f"    outbounds({len(outbounds)}): {[o.get('tag') for o in outbounds]}")
    print(f"    routing.domainStrategy={routing.get('domainStrategy')} rules={len(rules)}")
    for rule in rules[:5]:
        print(
            "      rule: inboundTag={inb} outboundTag={out} domain={dom}".format(
                inb=rule.get("inboundTag"),
                out=(rule.get("outboundTag") or rule.get("balancerTag")),
                dom=(rule.get("domain") or [])[:3],
            )
        )
    socks = [o for o in outbounds if o.get("protocol") == "socks"]
    print(f"    现有 socks 出站: {len(socks)} 条")
    for item in socks[:5]:
        servers = (item.get("settings") or {}).get("servers") or []
        first = servers[0] if servers else {}
        print(
            "      {tag}: {addr}:{port} users={n}".format(
                tag=item.get("tag"), addr=first.get("address"),
                port=first.get("port"), n=len(first.get("users") or []),
            )
        )


def _loads(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except ValueError:
            return {}
    return {}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="3x-ui 面板只读探测")
    parser.add_argument("--base", required=True, help="面板地址，含面板路径，如 https://1.2.3.4:54321/AbCd")
    parser.add_argument("--token", default=os.getenv("XUI_API_TOKEN", ""), help="API Token（Bearer）")
    parser.add_argument("--user", default=os.getenv("XUI_USER", ""), help="面板用户名")
    parser.add_argument("--password", default=os.getenv("XUI_PASSWORD", ""), help="面板密码")
    parser.add_argument("--save-raw", default="", help="把原始响应保存到该目录（含凭据，慎用）")
    parser.add_argument("--verify-tls", action="store_true", help="校验面板证书（默认跳过，面板多为自签）")
    args = parser.parse_args(argv)

    base = _base(args.base)
    headers = {"Accept": "application/json"}
    if args.token:
        headers["Authorization"] = f"Bearer {args.token}"
    client = httpx.Client(base_url=base, timeout=DEFAULT_TIMEOUT,
                          verify=args.verify_tls, follow_redirects=True, headers=headers)

    print(f"面板：{base}")
    print(f"鉴权：{'API Token ' + _mask(args.token) if args.token else '账号密码'}")

    if not args.token:
        if not (args.user and args.password):
            print("[!!] 没有 token，也没给账号密码；后续请求大概率 401")
        else:
            print("登录：" + _login_with_password(client, base, args.user, args.password))

    raw: dict[str, Any] = {}
    inbounds: list[dict] = []

    print("\n[1] 入站")
    for path in INBOUND_ENDPOINTS:
        status, body = _try_get(client, path)
        if status == 200 and isinstance(body, dict) and body.get("success"):
            obj = body.get("obj")
            items = obj if isinstance(obj, list) else (obj.get("items") if isinstance(obj, dict) else [])
            print(f"  [OK] {path} -> {status}，{len(items or [])} 条")
            raw[path] = body
            if path.endswith("/list") and isinstance(items, list):
                inbounds = items
                _summarize_inbounds(items)
                break
        else:
            print(f"  [--] {path} -> {status} {str(body)[:100]}")

    print("\n[2] 面板 / Xray 状态")
    for path in SERVER_ENDPOINTS:
        status, body = _try_get(client, path)
        flag = "[OK]" if status == 200 and isinstance(body, dict) and body.get("success") else "[--]"
        print(f"  {flag} {path} -> {status} {str(body)[:120] if flag == '[--]' else ''}")
        if flag == "[OK]":
            raw[path] = body

    print("\n[3] Xray 模板（出站 / 路由）")
    xray_found = False
    # 2.4.x 实测：GET 只返回页面 HTML，真正的读取是 POST /panel/xray/
    status, body = _try_post(client, "/panel/xray/")
    if status == 200 and isinstance(body, dict) and body.get("success") is not False:
        print(f"  [OK] POST /panel/xray/ -> {status}")
        raw["POST /panel/xray/"] = body
        _summarize_xray(body)
        xray_found = True
    for path in ([] if xray_found else XRAY_ENDPOINTS):
        status, body = _try_get(client, path)
        if status == 200 and isinstance(body, (dict, str)) and (not isinstance(body, dict) or body.get("success") is not False):
            print(f"  [OK] {path} -> {status}")
            raw[path] = body
            _summarize_xray(body)
            xray_found = True
            break
        print(f"  [--] {path} -> {status} {str(body)[:100]}")

    if not xray_found:
        print("  [!!] 没探到 Xray 模板。可能版本把接口放在其它路径，或 token 权限不含设置项。")
        print("       下一步：用浏览器 F12 在「Xray 设置」保存时看真实请求，把路径补进 XRAY_ENDPOINTS。")

    print("\n[4] 入站 tag 规则确认")
    if inbounds:
        ports = sorted({item.get("port") for item in inbounds if item.get("port")})
        print(f"  现有端口：{ports}")
        print("  3x-ui 自动生成的入站 tag 约定为 inbound-{port}，路由规则按它匹配；")
        print("  若 xray 模板里已有 inboundTag，可直接比对上一步打印的规则。")
    else:
        print("  没有读到入站，先解决鉴权再判断。")

    if args.save_raw:
        os.makedirs(args.save_raw, exist_ok=True)
        target = os.path.join(args.save_raw, "xui-probe.json")
        with open(target, "w", encoding="utf-8") as fh:
            json.dump(raw, fh, ensure_ascii=False, indent=2)
        print(f"\n原始响应已写入 {target}（含私钥/账号，别提交到仓库）")

    print("\n探测结束：本次没有修改任何配置。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
