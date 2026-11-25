"""出口节点 API 探针：先确认这套接口真能当中转层用，再谈写适配器。

为什么要先探测：
- 文档里的出口地址（203.0.113.10 / ss.example.com）都是示例，照抄必然被拒；
- 香港节点只给订阅地址、美国节点只给 vless 链接，**订阅里到底是什么**没人见过；
- 订阅是「节点级」的，而我们的客户订阅是「客户级聚合」的（见 subscription_service），
  两者能不能对上，决定交付形态是「直接转发供应商链接」还是「解析后塞进自己的订阅」；
- 出口连通性检测要 15-60 秒，本脚本负责把它等完，不靠猜。

本脚本只做真实业务调用，但**不改动**本地数据库、不写订单、不碰 CustomerLine。
所有请求都带 request_id，重复执行同一个业务不会重复开通（服务端幂等）。

用法（在 backend 目录下，或任意目录，脚本不依赖 app 包）：

    export CHUKOU_API_KEY=ck_xxxx

    # 1. 看有没有可分配节点（顺便验证 Key 能用）
    python scripts/chukou_probe.py nodes

    # 2. 用真实住宅出口开一个测试业务，自动等任务结束并打印链接
    python scripts/chukou_probe.py provision \
        --business probe-20261126-01 \
        --socks5 198.51.100.20:4700:user01:pass01 \
        --link-type subscription

    # 3. 看任务结果（job_id 从上一步拿）
    python scripts/chukou_probe.py job <job_id>

    # 4. 查业务当前绑定
    python scripts/chukou_probe.py status --business probe-20261126-01

    # 5. 换出口（节点和链接不变）
    python scripts/chukou_probe.py change \
        --business probe-20261126-01 --socks5 198.51.100.21:4700:user01:pass01

    # 6. 重新取链接
    python scripts/chukou_probe.py link --business probe-20261126-01

    # 7. 拉订阅内容并逐行解析（判断能不能转成我们自己的节点）
    python scripts/chukou_probe.py sub --business probe-20261126-01

    # 8. 归档一次完整探测
    python scripts/chukou_probe.py provision ... --save-raw ./chukou-raw

退出码 0 = 全部成功；1 = 有步骤失败；2 = 参数或配置缺失。
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import unquote, urlsplit

try:
    import httpx
except ImportError:  # 容器里没装依赖时给一句人话，不要抛 traceback
    print("[!!] 缺少 httpx，先 pip install httpx")
    sys.exit(2)

DEFAULT_BASE = "https://chukou.154769.xyz/api/integration/v1"
DEFAULT_STATE = Path(__file__).resolve().parent.parent / ".chukou-state.json"

# 服务端明确说「等一会再来」的错误码，可以和同一个 request_id 重发
RETRYABLE = {"busy", "conflict", "panel_unavailable", "syncing", "panel_timeout"}

# 出口只支持普通 TCP 的 SOCKS5 / Shadowsocks，这些参数出现就是会被拒
FORBIDDEN_QUERY = ("plugin", "security", "headerType", "type")
ALLOWED_TYPE = {"", "tcp", "raw"}
FORBIDDEN_SCHEMES = ("vmess", "vless", "trojan", "http", "https", "hysteria2", "tuic")


# --------------------------------------------------------------------------- #
# 出口链接的本地预校验
# --------------------------------------------------------------------------- #
# 拦不住的那部分（域名解析到内网）只能等服务端检测，但协议/参数/多链接
# 这几类 400 是最常见的手误，本地先挡掉能省一整轮往返。

def _private_host(host: str) -> str | None:
    """内网/回环/保留地址直接拒绝，服务端也会拒，这里提前给个明白话。"""
    text = (host or "").strip().strip("[]").lower()
    if not text:
        return "地址为空"
    if text in {"localhost", "localhost.localdomain"}:
        return "是回环主机名"
    parts = text.split(".")
    if len(parts) == 4 and all(part.isdigit() for part in parts):
        octets = [int(part) for part in parts]
        if any(o > 255 for o in octets):
            return "IPv4 段越界"
        a, b = octets[0], octets[1]
        if a == 10:
            return "是 10/8 内网地址"
        if a == 127:
            return "是 127/8 回环地址"
        if a == 0:
            return "是 0.0.0.0/8 保留地址"
        if a == 169 and b == 254:
            return "是 169.254/16 链路本地地址"
        if a == 172 and 16 <= b <= 31:
            return "是 172.16/12 内网地址"
        if a == 192 and b == 168:
            return "是 192.168/16 内网地址"
        if a == 100 and 64 <= b <= 127:
            return "是 100.64/10 运营商级 NAT 地址"
        if a >= 224:
            return "是组播/保留地址"
    return None


def _check_port(value) -> str | None:
    try:
        port = int(value)
    except (TypeError, ValueError):
        return f"端口不是整数：{value!r}"
    if not 1 <= port <= 65535:
        return f"端口超出 1-65535：{port}"
    return None


def _check_single_link(text: str) -> str | None:
    """服务端按全文判断「是否多条」，连 # 备注里出现链接都算。"""
    if len(text) > 4096:
        return f"长度 {len(text)} 超过 4096"
    if any(ch in text for ch in (" ", "\n", "\r", "\t")):
        return "含空格或换行"
    # 备注里出现第二个 :// 也会被拒
    if text.count("://") > 1:
        return f"全文出现 {text.count('://')} 个 ://（备注里带链接也算多条）"
    return None


def validate_outbound(text: str) -> str | None:
    """返回错误说明，None 表示本地看没问题。不保证服务端一定接受。"""
    if not text:
        return "出口为空"
    problem = _check_single_link(text)
    if problem:
        return problem

    scheme = text.split("://", 1)[0].strip().lower() if "://" in text else ""
    if scheme in FORBIDDEN_SCHEMES:
        return f"不支持 {scheme} 协议（只支持 ss / socks5）"

    if scheme in ("socks5", "socks", "ss"):
        try:
            parts = urlsplit(text)
            host = parts.hostname or ""
            port = parts.port
        except ValueError as exc:
            # urlsplit 对越界端口直接抛 ValueError，文案要能指向端口而不是地址
            if "Port" in str(exc) or "port" in str(exc):
                return f"端口超出 1-65535（{exc}）"
            return f"地址格式无法解析：{exc}"
        if not host:
            return "缺少地址"
        bad = _private_host(host)
        if bad:
            return f"地址 {host} {bad}"
        problem = _check_port(port)
        if problem:
            return problem
        if parts.path not in ("", "/"):
            return f"带协议头的链接不能带路径：{parts.path!r}"
        if parts.query:
            for pair in parts.query.split("&"):
                key = unquote(pair.split("=", 1)[0])
                value = unquote(pair.split("=", 1)[1]) if "=" in pair else ""
                if key == "plugin":
                    return "带 plugin 插件参数会被拒绝"
                if key in FORBIDDEN_QUERY:
                    if key == "type" and value.lower() not in ALLOWED_TYPE:
                        return f"type 只能是空 / tcp / raw，收到 {value!r}"
                    if key in ("security", "headerType") and value.lower() not in ("", "none"):
                        return f"{key} 只能是空或 none，收到 {value!r}"
        # 非 Base64 的用户名/密码里裸露的 @ / 会被服务端拒
        userinfo = text.split("://", 1)[1].split("@")[0] if "@" in text else ""
        if userinfo and "%40" not in userinfo and userinfo.count("@") > 0:
            return "用户名/密码含未编码的 @"
        return None

    # 无协议头：地址:端口[:用户名:密码]
    pieces = text.split(":")
    if len(pieces) == 2:
        host, port = pieces
        bad = _private_host(host)
        return f"地址 {host} {bad}" if bad else _check_port(port)
    if len(pieces) >= 3:
        host, port = pieces[0], pieces[1]
        bad = _private_host(host)
        if bad:
            return f"地址 {host} {bad}"
        problem = _check_port(port)
        if problem:
            return problem
        if ":" in pieces[2] if len(pieces) == 4 else False:
            return "用户名不能含冒号"
        return None
    return f"无法识别的写法：{len(pieces)} 段"


def build_socks5(arg: str) -> str:
    """地址:端口[:用户名:密码] 原样返回。

    文档明确说这种写法里密码可以含冒号（按第一段切分即可），且 # ? @ 都算
    密码的一部分，所以**不要**重新拼接或做 URL 编码，原样交给服务端最安全。
    """
    if not arg:
        raise ValueError("--socks5 为空")
    if len(arg.split(":")) < 2:
        raise ValueError("--socks5 至少要给 地址:端口")
    return arg


# --------------------------------------------------------------------------- #
# 客户端
# --------------------------------------------------------------------------- #

class ApiError(Exception):
    def __init__(self, status: int, code: str | None, message: str, payload: dict | None = None):
        super().__init__(f"HTTP {status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message
        self.payload = payload or {}


class ChukouClient:
    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.http = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
            },
        )

    def close(self) -> None:
        self.http.close()

    def call(self, method: str, path: str, body: dict | None = None, attempts: int = 6) -> dict:
        """按文档的重试语义：可重试码幂等重发，不可重试码直接抛。"""
        last_error: ApiError | None = None
        for attempt in range(attempts):
            try:
                resp = self.http.request(method, path, json=body)
            except httpx.RequestError as exc:
                last_error = ApiError(0, "NETWORK", f"{type(exc).__name__}: {exc}")
                time.sleep(min(5 * (attempt + 1), 20))
                continue

            try:
                data = resp.json()
            except ValueError:
                # 404/405 可能是 HTML 页面，文档明确提醒过
                raise ApiError(resp.status_code, None,
                               f"响应不是 JSON：{resp.text[:120]!r}") from None
            if not isinstance(data, dict):
                raise ApiError(resp.status_code, None, "响应不是 JSON 对象")

            if resp.status_code < 400:
                return data

            code = data.get("code")
            message = data.get("error") or data.get("message") or ""
            if code == "rate_limited":
                wait = int(data.get("retry_after") or 60)
                print(f"  [限频] {message}，{wait} 秒后重试（剩余 {attempts - attempt - 1} 次）")
                time.sleep(min(wait, 300))
                continue
            if code in RETRYABLE or resp.status_code >= 500:
                wait = min(10 * (attempt + 1), 30)
                print(f"  [可重试] {code} {message}，{wait} 秒后重发同一 request_id")
                time.sleep(wait)
                continue
            raise ApiError(resp.status_code, code, message, data)
        raise last_error or ApiError(0, "TIMEOUT", "多次重试仍未成功")

    # --- 六个接口 ---

    def nodes(self) -> dict:
        return self.call("GET", "/nodes")

    def provision(self, *, request_id: str, business_id: str, outbound: str,
                  node_id: str = "auto", link_type: str | None = None) -> dict:
        body = {
            "request_id": request_id,
            "business_id": business_id,
            "node_id": node_id,
            "outbound": outbound,
        }
        if link_type:
            body["link_type"] = link_type
        return self.call("POST", "/provisions", body)

    def job(self, job_id: str) -> dict:
        return self.call("GET", f"/jobs/{job_id}")

    def binding(self, business_id: str) -> dict:
        return self.call("GET", f"/bindings/{business_id}")

    def change_outbound(self, business_id: str, *, request_id: str, outbound: str) -> dict:
        return self.call("PUT", f"/bindings/{business_id}/outbound",
                         {"request_id": request_id, "outbound": outbound})

    def link(self, business_id: str) -> dict:
        return self.call("GET", f"/bindings/{business_id}/link")

    # --- 轮询 ---

    def wait_job(self, job_id: str, timeout: float = 300.0, interval: float = 4.0) -> dict:
        deadline = time.time() + timeout
        last: dict = {}
        while time.time() < deadline:
            last = self.job(job_id)
            state = last.get("state")
            if state != "pending":
                return last
            remaining = int(deadline - time.time())
            print(f"  … {state}（{last.get('message') or '处理中'}，剩余 {remaining}s）")
            time.sleep(interval)
        print("  [!!] 已超过 5 分钟仍是 pending。文档允许停止轮询，稍后用同一个 job_id 再查。")
        return dict(last, timed_out=True)

    def fetch_subscription(self, url: str) -> httpx.Response:
        """订阅地址是供应商域名，不带我们的 Authorization（带了反而可能被拒）。"""
        with httpx.Client(timeout=30.0, follow_redirects=True) as plain:
            return plain.get(url)


# --------------------------------------------------------------------------- #
# 输出辅助
# --------------------------------------------------------------------------- #

def mask_link(link: str, keep_host: bool = True) -> str:
    """打印用：保留协议和主机，抹掉凭据和 token。"""
    if not link:
        return "(空)"
    if "://" not in link:
        return link[:40] + "…"
    scheme, rest = link.split("://", 1)
    if "@" in rest:
        _, tail = rest.split("@", 1)
        return f"{scheme}://***@{tail.split('?')[0]}" if keep_host else f"{scheme}://***"
    # https 订阅地址：只留 host 和 path 前缀
    host = rest.split("/", 1)[0]
    tail = rest.split("/", 1)[1][:12] if "/" in rest else ""
    return f"{scheme}://{host}/{tail}…"


def maybe_b64(text: str) -> str | None:
    """订阅内容可能是整段 base64 的节点列表。"""
    compact = (text or "").strip().replace("\n", "").replace("\r", "").replace(" ", "")
    if not compact or "://" in compact:
        return None
    try:
        padding = "=" * (-len(compact) % 4)
        decoded = base64.b64decode(compact + padding, validate=False).decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 - 解不出来就当不是 base64
        return None
    return decoded if "://" in decoded else None


def summarize_nodes(payload: dict) -> int:
    nodes = payload.get("nodes") or []
    accepting = payload.get("accepting_new")
    print(f"accepting_new = {accepting}")
    if accepting is False:
        print("  [!!] 我方暂停了新开通，nodes 为空；已有业务的管理接口仍可用。")
    if not nodes:
        print("  当前没有可分配节点。")
        return 0
    print(f"  可分配 {len(nodes)} 个：")
    print(f"  {'node_id':<14}{'panel':<10}{'remark':<10}{'port':<8}link_type")
    for item in nodes:
        print(f"  {str(item.get('node_id')):<14}{str(item.get('panel')):<10}"
              f"{str(item.get('remark')):<10}{str(item.get('port')):<8}{item.get('link_type')}")
    by_type: dict[str, int] = {}
    for item in nodes:
        by_type[item.get("link_type") or "?"] = by_type.get(item.get("link_type") or "?", 0) + 1
    print(f"  分布：{by_type}（subscription=香港，vless=美国）")
    return len(nodes)


def summarize_job(job: dict, *, show_link: bool = False) -> bool:
    state = job.get("state")
    print(f"  job_id   = {job.get('job_id')}")
    print(f"  kind     = {job.get('kind')}   state = {state}")
    print(f"  business = {job.get('business_id')}   node = {job.get('node_id')}")
    print(f"  message  = {job.get('message')}")
    if job.get("created") and job.get("updated"):
        print(f"  耗时     ≈ {float(job['updated']) - float(job['created']):.1f}s")
    if job.get("link_type"):
        print(f"  link_type= {job['link_type']}")
    if job.get("link"):
        print(f"  link     = {job['link'] if show_link else mask_link(job['link'])}")
    if job.get("link_error"):
        print(f"  [!!] 开通成功但链接没取到：{job['link_error']}（改用 link 子命令再取）")
    if job.get("timed_out"):
        print("  [..] 仍在处理中，不视为失败")
        return False
    if state == "needs_review":
        print("  [!!] 结果不确定，节点保持占用。不要重复提交，按文档联系上游人工核对。")
        return False
    return state == "succeeded"


# --------------------------------------------------------------------------- #
# 状态台账
# --------------------------------------------------------------------------- #

def load_state(path: Path) -> dict:
    if not path.exists():
        return {"requests": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {"requests": {}}
    return data if isinstance(data, dict) and "requests" in data else {"requests": {}}


def save_state(path: Path, state: dict) -> None:
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def new_request_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


# --------------------------------------------------------------------------- #
# 子命令
# --------------------------------------------------------------------------- #

def cmd_nodes(client: ChukouClient, args, state: dict, raw_sink: dict) -> int:
    payload = client.nodes()
    raw_sink["nodes"] = payload
    count = summarize_nodes(payload)
    return 0 if count or payload.get("accepting_new") is False else 1


def _do_provision(client: ChukouClient, args, state: dict, raw_sink: dict) -> int:
    outbound = args.outbound or build_socks5(args.socks5)
    problem = validate_outbound(outbound)
    if problem:
        print(f"[!!] 出口本地校验不通过：{problem}")
        print("     本地校验只挡常见手误，过了也不保证服务端接受。")
        return 2

    request_id = args.request_id or new_request_id("prov")
    print(f"business_id = {args.business}")
    print(f"request_id  = {request_id}")
    print(f"outbound    = {mask_link(outbound)}")
    print(f"node_id     = {args.node or 'auto'}   link_type = {args.link_type or '(不指定)'}")

    try:
        job = client.provision(
            request_id=request_id,
            business_id=args.business,
            outbound=outbound,
            node_id=args.node or "auto",
            link_type=args.link_type,
        )
    except ApiError as exc:
        print(f"[!!] 提交失败：{exc}")
        _explain_error(exc)
        return 1

    state["requests"][request_id] = {
        "business_id": args.business,
        "kind": "provision",
        "outbound": outbound,
        "job_id": job.get("job_id"),
        "created": time.time(),
    }
    save_state(args.state, state)
    raw_sink["provision"] = job

    print(f"\n[已提交] job_id = {job.get('job_id')}，node = {job.get('node_id')}")
    print("开始轮询（文档说通常 15-60 秒）：")
    final = client.wait_job(job["job_id"], timeout=args.wait, interval=args.interval)
    raw_sink["job"] = final
    ok = summarize_job(final, show_link=args.show_link)
    if ok and not final.get("link") and not final.get("timed_out"):
        print("\n链接没随任务返回，单独取一次：")
        try:
            got = client.link(args.business)
            raw_sink["link"] = got
            print(f"  link_type = {got.get('link_type')}")
            print(f"  link      = {got.get('link') if args.show_link else mask_link(got.get('link'))}")
        except ApiError as exc:
            print(f"  [!!] {exc}")
            _explain_error(exc)
            return 1
    return 0 if ok else 1


def cmd_provision(client: ChukouClient, args, state: dict, raw_sink: dict) -> int:
    return _do_provision(client, args, state, raw_sink)


def cmd_change(client: ChukouClient, args, state: dict, raw_sink: dict) -> int:
    outbound = args.outbound or build_socks5(args.socks5)
    problem = validate_outbound(outbound)
    if problem:
        print(f"[!!] 出口本地校验不通过：{problem}")
        return 2
    request_id = args.request_id or new_request_id("chg")
    print(f"business_id = {args.business}\nrequest_id  = {request_id}")
    try:
        job = client.change_outbound(args.business, request_id=request_id, outbound=outbound)
    except ApiError as exc:
        print(f"[!!] 提交失败：{exc}")
        _explain_error(exc)
        return 1
    state["requests"][request_id] = {
        "business_id": args.business, "kind": "change",
        "outbound": outbound, "job_id": job.get("job_id"), "created": time.time(),
    }
    save_state(args.state, state)
    raw_sink["change"] = job
    print(f"\n[已提交] job_id = {job.get('job_id')}")
    final = client.wait_job(job["job_id"], timeout=args.wait, interval=args.interval)
    raw_sink["job"] = final
    ok = summarize_job(final)
    print("\n换出口后节点和链接都不变，终端用户无需重新导入。")
    return 0 if ok else 1


def cmd_job(client: ChukouClient, args, state: dict, raw_sink: dict) -> int:
    try:
        job = client.job(args.job_id)
    except ApiError as exc:
        print(f"[!!] {exc}")
        _explain_error(exc)
        return 1
    raw_sink["job"] = job
    return 0 if summarize_job(job, show_link=args.show_link) else 1


def cmd_status(client: ChukouClient, args, state: dict, raw_sink: dict) -> int:
    try:
        got = client.binding(args.business)
    except ApiError as exc:
        print(f"[!!] {exc}")
        _explain_error(exc)
        return 1
    raw_sink["binding"] = got
    print(f"  business_id = {got.get('business_id')}")
    print(f"  node        = {got.get('node_id')}  ({got.get('panel')} / {got.get('remark')} / :{got.get('port')})")
    print(f"  link_type   = {got.get('link_type')}")
    print(f"  state       = {got.get('state')}   node_status = {got.get('node_status')}")
    expires = got.get("expires_at")
    if expires:
        # 文档：毫秒时间戳，且从「首次实际使用」才开始计时
        print(f"  expires_at  = {expires}  ({time.strftime('%Y-%m-%d %H:%M', time.localtime(expires / 1000))})")
    else:
        print("  expires_at  = null（节点还没被真正使用过，未开始计时）")
    last = got.get("last_job")
    if last:
        print(f"  last_job    = {last.get('kind')} / {last.get('state')} / {last.get('message')}")
    return 0


def cmd_link(client: ChukouClient, args, state: dict, raw_sink: dict) -> int:
    try:
        got = client.link(args.business)
    except ApiError as exc:
        print(f"[!!] {exc}")
        _explain_error(exc)
        return 1
    raw_sink["link"] = got
    print(f"  link_type = {got.get('link_type')}")
    print(f"  link      = {got.get('link') if args.show_link else mask_link(got.get('link'))}")
    return 0


def cmd_sub(client: ChukouClient, args, state: dict, raw_sink: dict) -> int:
    """最关键的一步：订阅里到底是什么，能不能解析成我们自己的节点。"""
    try:
        got = client.link(args.business)
    except ApiError as exc:
        print(f"[!!] 取链接失败：{exc}")
        _explain_error(exc)
        return 1
    link_type, link = got.get("link_type"), got.get("link") or ""
    print(f"  link_type = {link_type}")
    print(f"  link      = {link}")
    raw_sink["link"] = got
    if not link:
        return 1

    if link_type == "vless" or link.startswith("vless://"):
        print("\n[VLESS] 单条链接，字段拆解：")
        try:
            parts = urlsplit(link)
            print(f"  uuid   = {parts.username or '(空)'}")
            print(f"  server = {parts.hostname}:{parts.port}")
            print(f"  params = {dict(p.split('=', 1) for p in parts.query.split('&') if '=' in p)}")
            print(f"  name   = {unquote(parts.fragment or '')}")
        except ValueError as exc:
            print(f"  [!!] 解析失败：{exc}")
            return 1
        print("\n  → 这条可以直接写进 CustomerLine.node_link，走现有 Node/订阅逻辑。")
        return 0

    print("\n[订阅] 拉取内容：")
    try:
        resp = client.fetch_subscription(link)
    except httpx.HTTPError as exc:
        print(f"  [!!] 拉取失败：{type(exc).__name__}: {exc}")
        return 1
    body = resp.text or ""
    print(f"  HTTP {resp.status_code}  {len(body)} 字节  content-type={resp.headers.get('content-type')}")
    raw_sink["subscription"] = {"status": resp.status_code, "body": body[:20000]}
    if resp.status_code >= 400:
        print(f"  正文前 200 字：{body[:200]}")
        return 1

    decoded = maybe_b64(body)
    text = decoded if decoded else body
    print(f"  编码：{'base64（已解码）' if decoded else '明文'}")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    schemes: dict[str, int] = {}
    samples: list[str] = []
    for line in lines:
        if "://" not in line:
            continue
        scheme = line.split("://", 1)[0].lower()
        schemes[scheme] = schemes.get(scheme, 0) + 1
        if len(samples) < 5:
            samples.append(mask_link(line))
    print(f"  行数 {len(lines)}，协议分布 {schemes}")
    for item in samples:
        print(f"    {item}")
    if "proxies:" in text or "proxy-groups:" in text:
        print("\n  → 返回的是 Clash YAML 配置，不是节点列表。要复用得做一次 YAML 解析。")
    elif schemes:
        print("\n  → 是可解析的节点列表，可以逐条 parse_link 后重建 Node / 客户级订阅。")
    else:
        print("\n  [!!] 没识别出节点行，正文前 300 字：")
        print(f"  {text[:300]}")
        return 1
    return 0


def _explain_error(exc: ApiError) -> None:
    hints = {
        "invalid_key": "密钥缺失/错误/停用。确认 CHUKOU_API_KEY 以 ck_ 开头，且没有多余空格。",
        "invalid_outbound": "出口不合规。看错误说明里的参数名，别回显值的那条就是问题所在。",
        "invalid_request": "字段缺失、多余或格式不对。注意 request_id 8-64 位，business_id 1-64 位。",
        "paused": "上游暂停新开通，稍后再试；已有业务的查询/换出口/取链接不受影响。",
        "not_found": "业务没绑定节点，或任务不存在（也可能 30 天记录已清理）。",
        "business_bound": "这个 business_id 已经有节点了，改用 change 子命令。",
        "node_unavailable": "指定节点不可分配或已没有可用节点，先跑 nodes 重新看。",
        "request_reused": "同一 request_id 用了不同内容。新操作请换新编号。",
        "request_expired": "编号对应的任务早已结束且记录被清理，用 status 看业务当前状态。",
        "not_ready": "开通还没完成，等任务结束再操作。",
        "job_pending": "这个业务上一个操作还没结束，先等它。",
        "exit_unavailable": "节点出口状态变了，API 改不了，得找上游。",
        "link_syncing": "认证刚更新在同步，约 1 分钟后重试。",
        "link_unavailable": "链接暂时不可交付，稍后重试；一直这样就找上游。",
        "conflict": "节点所在服务器有任务待核对，稍后用同一 request_id 重试。",
        "rate_limited": "5 分钟内已保存过出口，按 retry_after 等。",
        "busy": "全站保存任务已满，10-30 秒后用同一 request_id 重试。",
        "panel_unavailable": "节点服务器暂时连不上，稍后重试。",
        "syncing": "服务正在同步数据，稍后重试。",
        "panel_timeout": "节点服务器响应超时，稍后用同一 request_id 重试。",
    }
    hint = hints.get(exc.code or "")
    if hint:
        print(f"     → {hint}")
    if exc.payload.get("retry_after"):
        print(f"     → retry_after = {exc.payload['retry_after']} 秒")


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #

def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--base", default=os.getenv("CHUKOU_BASE_URL", DEFAULT_BASE),
                        help=f"接口地址，默认 {DEFAULT_BASE}")
    parser.add_argument("--key", default=os.getenv("CHUKOU_API_KEY", ""),
                        help="API 密钥，默认读环境变量 CHUKOU_API_KEY")
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE,
                        help="request_id 台账（含出口凭据，别提交到仓库）")
    parser.add_argument("--show-link", action="store_true",
                        help="打印完整链接（含凭据，注意终端历史）")
    parser.add_argument("--save-raw", default="",
                        help="把本次原始响应存到该目录（含凭据，别提交）")


def _add_outbound(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--outbound", help="出口链接，如 socks5://u:p@1.2.3.4:1080 或 ss://...")
    group.add_argument("--socks5", help="无协议头写法 地址:端口:用户名:密码（密码可含冒号）")
    parser.add_argument("--request-id", default="",
                        help="手工指定 request_id；不填自动生成。重试用同一个")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="出口节点 API 探针（只调真实接口，不改本地库）")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("nodes", help="列可分配节点")
    _add_common(p)

    p = sub.add_parser("provision", help="开通：分配节点 + 配出口")
    _add_common(p)
    _add_outbound(p)
    p.add_argument("--business", required=True, help="业务编号，建议 order_no-lineindex")
    p.add_argument("--node", default="auto", help="指定 node_id；默认 auto")
    p.add_argument("--link-type", default=None, choices=["subscription", "vless"],
                   help="只在 auto 时可用：subscription=香港，vless=美国")
    p.add_argument("--wait", type=float, default=300, help="轮询上限秒数，默认 300")
    p.add_argument("--interval", type=float, default=4, help="轮询间隔，默认 4 秒")

    p = sub.add_parser("change", help="更换已开通业务的出口")
    _add_common(p)
    _add_outbound(p)
    p.add_argument("--business", required=True)
    p.add_argument("--wait", type=float, default=300)
    p.add_argument("--interval", type=float, default=4)

    p = sub.add_parser("job", help="查任务")
    _add_common(p)
    p.add_argument("job_id")

    p = sub.add_parser("status", help="查业务当前绑定")
    _add_common(p)
    p.add_argument("--business", required=True)

    p = sub.add_parser("link", help="重新取链接")
    _add_common(p)
    p.add_argument("--business", required=True)

    p = sub.add_parser("sub", help="拉订阅内容并解析（决定交付形态）")
    _add_common(p)
    p.add_argument("--business", required=True)

    args = parser.parse_args(argv)

    if not args.key:
        print("[!!] 没有 API 密钥。")
        print("     先 export CHUKOU_API_KEY=ck_xxx，或加 --key ck_xxx。")
        print("     密钥只能放服务端，别写进前端、App 或代码仓库。")
        return 2

    state = load_state(args.state)
    raw_sink: dict = {}
    client = ChukouClient(args.key, args.base)
    print(f"接口：{client.base_url}\n")

    handlers = {
        "nodes": cmd_nodes, "provision": cmd_provision, "change": cmd_change,
        "job": cmd_job, "status": cmd_status, "link": cmd_link, "sub": cmd_sub,
    }
    try:
        return handlers[args.command](client, args, state, raw_sink)
    except ApiError as exc:
        print(f"[!!] 请求失败：{exc}")
        _explain_error(exc)
        return 1
    except KeyboardInterrupt:
        print("\n[中断] 任务可能已经提交，用 status / job 查，别重新开通。")
        return 130
    finally:
        client.close()
        if getattr(args, "save_raw", ""):
            target = Path(args.save_raw)
            target.mkdir(parents=True, exist_ok=True)
            out = target / f"chukou-{args.command}-{int(time.time())}.json"
            out.write_text(json.dumps(raw_sink, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"\n原始响应已写入 {out}（含凭据，别提交到仓库）")


if __name__ == "__main__":
    sys.exit(main())
