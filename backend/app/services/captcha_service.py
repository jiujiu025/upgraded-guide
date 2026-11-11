"""注册图形验证码：九宫格点选，服务端出题、服务端判题。

为什么自建，不接 reCAPTCHA / hCaptcha / 极验：

- 站内客户主要在国内，Google 系域名多数网络环境打不通；
- 第三方验证码等于把「谁在注册」这件事交给外部服务，还要多一次跨域请求；
- 这里只需要挡住脚本批量注册，不需要跟专业打码平台对抗。

三个关键设计：

1. **答案不出现在响应里**。token 用 AES-GCM 加密（密钥由 ``JWT_SECRET_KEY`` 派生），
   前端拿到的是一串密文；扒 DOM、翻 token 都读不出该选哪几张。
2. **图形由服务端用 SVG 现画**，不读图片文件，也不用 emoji。emoji 会以字符形式落进
   页面，脚本直接抄答案；几何图形是路径数据，看不出语义。
3. **一次性**。验证成功即作废；同一道题错 3 次也作废，避免拿一道题把 2^9 种组合
   试完。多实例靠 Redis 共享，Redis 不可用时退回进程内——验证码是防刷手段，
   不该在缓存抖动时把注册整体打死。
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import math
import random
import secrets
import time
from collections.abc import Sequence
from dataclasses import dataclass
from threading import Lock

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from redis import Redis
from redis.exceptions import RedisError

from app.core.config import get_settings

logger = logging.getLogger(__name__)

TILE_COUNT = 9
TOKEN_TTL_SECONDS = 180
MAX_WRONG_ATTEMPTS = 3

# token 的 AAD：换用途（比如以后给找回密码也发 token）必须换 AAD，
# 免得两处通行证互相顶用。
_AAD = b"99ipxin:captcha:v1"
_TOKEN_VERSION = "v1:"

_SHAPE_COLORS = (
    "#d64545", "#e07b39", "#b58b00", "#2f8f5b", "#2f7d8f",
    "#3b6fd4", "#6d57c8", "#b8478f", "#4b5563",
)
_TILE_BACKGROUNDS = ("#f7f2e8", "#eef4f0", "#f0eef9", "#fdf1e6", "#edf2fa", "#f6eef2")


def _star_points(cx: float = 50, cy: float = 52, outer: float = 36, inner: float = 15, points: int = 5) -> str:
    coords = []
    for index in range(points * 2):
        radius = outer if index % 2 == 0 else inner
        angle = math.radians(-90 + index * 180 / points)
        coords.append(f"{cx + radius * math.cos(angle):.1f},{cy + radius * math.sin(angle):.1f}")
    return " ".join(coords)


# key -> (对客户展示的名字, 形状模板)。模板里 {fill} 由出题时随机上色替换。
_SHAPES: dict[str, tuple[str, str]] = {
    "star": ("星星", f'<polygon points="{_star_points()}" fill="{{fill}}"/>'),
    "heart": ("爱心", '<path d="M50,84 C18,62 12,44 22,32 C31,21 45,24 50,34 C55,24 69,21 78,32 C88,44 82,62 50,84 Z" fill="{fill}"/>'),
    "lightning": ("闪电", '<polygon points="56,10 26,56 46,56 42,90 74,42 54,42" fill="{fill}"/>'),
    "moon": ("月亮", '<circle cx="50" cy="52" r="34" fill="{fill}"/><circle cx="70" cy="40" r="30" fill="{bg}"/>'),
    "house": ("房子", '<path d="M50,14 L88,48 H78 V86 H58 V64 H42 V86 H22 V48 H12 Z" fill="{fill}"/>'),
    "drop": ("水滴", '<path d="M50,12 C50,12 78,46 78,64 A28,28 0 0 1 22,64 C22,46 50,12 50,12 Z" fill="{fill}"/>'),
    "plus": ("十字", '<path d="M42,12 H58 V42 H88 V58 H58 V88 H42 V58 H12 V42 H42 Z" fill="{fill}"/>'),
    "triangle": ("三角", '<polygon points="50,16 86,80 14,80" fill="{fill}"/>'),
    "square": ("方块", '<rect x="18" y="18" width="64" height="64" rx="6" fill="{fill}"/>'),
    "circle": ("圆形", '<circle cx="50" cy="50" r="34" fill="{fill}"/>'),
    "diamond": ("菱形", '<polygon points="50,12 88,50 50,88 12,50" fill="{fill}"/>'),
    "hexagon": ("六边形", '<polygon points="50,14 81,32 81,68 50,86 19,68 19,32" fill="{fill}"/>'),
    "ring": ("圆环", '<circle cx="50" cy="50" r="30" fill="none" stroke="{fill}" stroke-width="16"/>'),
}


class CaptchaError(Exception):
    """验证码不可用（过期、用过了、选错了）。消息直接展示给客户。"""


def _cipher() -> AESGCM:
    material = get_settings().jwt_secret_key
    if hasattr(material, "get_secret_value"):
        material = material.get_secret_value()
    return AESGCM(hashlib.sha256(f"99ipxin:captcha-key:{material}".encode()).digest())


def _seal(payload: dict) -> str:
    nonce = secrets.token_bytes(12)
    body = _cipher().encrypt(nonce, json.dumps(payload, separators=(",", ":")).encode("utf-8"), _AAD)
    return _TOKEN_VERSION + base64.urlsafe_b64encode(nonce + body).decode("ascii").rstrip("=")


def _unseal(token: str) -> dict:
    if not token.startswith(_TOKEN_VERSION):
        raise CaptchaError("图形验证码不正确，请重新选择")
    raw = token[len(_TOKEN_VERSION):]
    try:
        blob = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
        if len(blob) < 29:
            raise ValueError("ciphertext too short")
        plain = _cipher().decrypt(blob[:12], blob[12:], _AAD)
        payload = json.loads(plain.decode("utf-8"))
    except (InvalidTag, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CaptchaError("图形验证码不正确，请重新选择") from exc
    if not isinstance(payload, dict) or "s" not in payload or "a" not in payload:
        raise CaptchaError("图形验证码不正确，请重新选择")
    return payload


def _noise(rng: random.Random) -> str:
    parts = []
    for _ in range(rng.randint(4, 7)):
        parts.append(
            f'<circle cx="{rng.randint(3, 97)}" cy="{rng.randint(3, 97)}" '
            f'r="{rng.uniform(0.7, 1.8):.1f}" fill="#0f172a" opacity="{rng.uniform(0.06, 0.16):.2f}"/>'
        )
    for _ in range(rng.randint(1, 2)):
        parts.append(
            f'<line x1="{rng.randint(2, 40)}" y1="{rng.randint(2, 98)}" '
            f'x2="{rng.randint(60, 98)}" y2="{rng.randint(2, 98)}" '
            f'stroke="#0f172a" stroke-width="{rng.uniform(0.5, 1.2):.1f}" '
            f'opacity="{rng.uniform(0.05, 0.12):.2f}"/>'
        )
    return "".join(parts)


def _tile_svg(shape_key: str, rng: random.Random) -> str:
    _label, template = _SHAPES[shape_key]
    fill = rng.choice(_SHAPE_COLORS)
    background = rng.choice(_TILE_BACKGROUNDS)
    # 方块只给小幅旋转：转到 30° 以上就成菱形了，客户会把干扰项当成答案，
    # 或者反过来。菱形随便转都是菱形，不用管。
    rotation = rng.randint(-10, 10) if shape_key == "square" else rng.randint(-18, 18)
    scale = rng.uniform(0.82, 1.0)
    offset_x = rng.randint(-3, 3)
    offset_y = rng.randint(-3, 3)
    body = template.replace("{fill}", fill).replace("{bg}", background)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="100" height="100">'
        f'<rect width="100" height="100" rx="14" fill="{background}"/>'
        f"{_noise(rng)}"
        f'<g transform="translate({50 + offset_x} {50 + offset_y}) rotate({rotation}) scale({scale:.2f}) '
        f'translate(-50 -50)">{body}</g>'
        "</svg>"
    )


@dataclass(frozen=True)
class Challenge:
    """一道题。``answers`` 只给服务端（和测试）用，不下发。"""

    token: str
    target_label: str
    target_count: int
    tiles: tuple[str, ...]
    answers: frozenset[int]
    expires_in: int

    def public_view(self) -> dict:
        return {
            "token": self.token,
            "target": self.target_label,
            "count": self.target_count,
            "expires_in": self.expires_in,
            "tiles": [{"index": index, "svg": svg} for index, svg in enumerate(self.tiles)],
        }


class _ChallengeStore:
    """记录 token 是否用过、错了几次。

    生产是多实例，两份状态必须共享：同一道题在 A 实例用掉后，B 实例不能再认。
    生产强制 RATE_LIMIT_BACKEND=redis（见 config 校验），所以这里跟着它走。
    """

    def __init__(self) -> None:
        self._memory: dict[str, list] = {}  # sid -> [过期时刻, 错次, 是否用过]
        self._lock = Lock()
        self._redis: Redis | None = None

    def _client(self) -> Redis | None:
        settings = get_settings()
        if settings.rate_limit_backend != "redis":
            return None
        if self._redis is None:
            self._redis = Redis.from_url(
                settings.redis_url, decode_responses=True, socket_connect_timeout=2, socket_timeout=2
            )
        return self._redis

    @staticmethod
    def _key(kind: str, sid: str) -> str:
        return f"ops:captcha:{kind}:{sid}"

    def _memory_record(self, sid: str, ttl: int) -> list:
        now = time.monotonic()
        for key in [key for key, value in self._memory.items() if value[0] <= now]:
            self._memory.pop(key, None)
        return self._memory.setdefault(sid, [now + ttl, 0, False])

    def consume(self, sid: str, ttl: int) -> bool:
        """把 token 标记为已用；返回 False 表示之前已经用过。"""
        client = self._client()
        if client is not None:
            try:
                return bool(client.set(self._key("used", sid), "1", nx=True, ex=max(ttl, 1)))
            except RedisError:
                logger.warning("验证码去重降级到进程内存储：Redis 不可用")
        with self._lock:
            record = self._memory_record(sid, ttl)
            if record[2]:
                return False
            record[2] = True
            return True

    def note_failure(self, sid: str, ttl: int) -> int:
        """记一次选错，返回这道题累计错了几次。"""
        client = self._client()
        if client is not None:
            try:
                key = self._key("wrong", sid)
                count = int(client.incr(key))
                if count == 1:
                    client.expire(key, max(ttl, 1))
                return count
            except RedisError:
                logger.warning("验证码错误计数降级到进程内存储：Redis 不可用")
        with self._lock:
            record = self._memory_record(sid, ttl)
            record[1] += 1
            return int(record[1])

    def clear(self) -> None:
        with self._lock:
            self._memory.clear()


store = _ChallengeStore()


def issue_challenge(*, ttl_seconds: int = TOKEN_TTL_SECONDS, rng: random.Random | None = None) -> Challenge:
    """出一道 3×3 点选题：3~4 个目标图形混在干扰图形里。"""
    rng = rng or random.SystemRandom()
    target_key = rng.choice(list(_SHAPES))
    target_label = _SHAPES[target_key][0]
    target_count = rng.randint(3, 4)
    answer_indexes = frozenset(rng.sample(range(TILE_COUNT), target_count))
    distractors = [key for key in _SHAPES if key != target_key]
    tiles = tuple(
        _tile_svg(target_key if index in answer_indexes else rng.choice(distractors), rng)
        for index in range(TILE_COUNT)
    )
    sid = secrets.token_urlsafe(12)
    token = _seal({"s": sid, "a": sorted(answer_indexes), "e": int(time.time()) + ttl_seconds})
    return Challenge(
        token=token,
        target_label=target_label,
        target_count=target_count,
        tiles=tiles,
        answers=answer_indexes,
        expires_in=ttl_seconds,
    )


def _normalize_selection(selection: Sequence[int] | None) -> set[int]:
    if not selection:
        raise CaptchaError("请先完成图形验证码")
    picked: set[int] = set()
    for item in selection:
        try:
            index = int(item)
        except (TypeError, ValueError) as exc:
            raise CaptchaError("图形验证码不正确，请重新选择") from exc
        if not 0 <= index < TILE_COUNT:
            raise CaptchaError("图形验证码不正确，请重新选择")
        picked.add(index)
    return picked


def verify_challenge(token: str | None, selection: Sequence[int] | None) -> None:
    """校验通过则静默返回，否则抛 :class:`CaptchaError`。"""
    if not token:
        raise CaptchaError("请先完成图形验证码")
    payload = _unseal(token)
    expire_at = int(payload.get("e") or 0)
    remaining = expire_at - int(time.time())
    if remaining <= 0:
        raise CaptchaError("图形验证码已过期，请刷新后重试")
    sid = str(payload["s"])
    try:
        expected = {int(item) for item in payload["a"]}
    except (TypeError, ValueError) as exc:
        raise CaptchaError("图形验证码不正确，请重新选择") from exc
    picked = _normalize_selection(selection)
    if picked != expected:
        failures = store.note_failure(sid, remaining)
        if failures >= MAX_WRONG_ATTEMPTS:
            # 作废这道题：再让同一道题继续试下去，等于把 512 种组合白送。
            store.consume(sid, remaining)
            raise CaptchaError("验证失败次数过多，请刷新验证码后重试")
        raise CaptchaError("图形验证码不正确，请重新选择")
    if not store.consume(sid, remaining):
        raise CaptchaError("图形验证码已使用，请刷新后重试")
