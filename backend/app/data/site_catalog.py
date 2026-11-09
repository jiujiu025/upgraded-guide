"""官网三类产品线的国家与价格底表（2026-11-09 玖夜提供）。

口径说明：
- 三类产品线：静态住宅 IP、社媒运营专线、直播专线；三类共用同一份国家清单。
- 价格单位：元 / 30 天。其它周期由定价服务线性派生（90 天 = 3×、180 天 = 6×、360 天 = 12×）。
- 社媒专线是「静态住宅 IP + 社媒中转」的打包价，报价表与住宅价不构成固定加价关系，
  所以单独维护，不做公式换算。
- 直播专线不分国家：手机统一 600、电脑统一 1100。为了满足「每类下面国家一样」，
  每个国家各写一条同价规则。
- 原始报价表里日本出现两行（115 / 123）。同一分类 + 国家 + 周期的唯一约束只允许一条，
  这里取较高的 123（宁可不少收），后台可随时改。
"""

# 国家清单：三类共用。顺序即官网展示顺序（按大洲分组的感觉排列）。
COUNTRIES = [
    ("US", "美国"),
    ("CA", "加拿大"),
    ("MX", "墨西哥"),
    ("BR", "巴西"),
    ("GB", "英国"),
    ("FR", "法国"),
    ("DE", "德国"),
    ("NL", "荷兰"),
    ("ES", "西班牙"),
    ("IT", "意大利"),
    ("PL", "波兰"),
    ("HU", "匈牙利"),
    ("BG", "保加利亚"),
    ("DK", "丹麦"),
    ("RU", "俄罗斯"),
    ("TR", "土耳其"),
    ("IL", "以色列"),
    ("AE", "阿联酋"),
    ("SA", "沙特阿拉伯"),
    ("JP", "日本"),
    ("KR", "韩国"),
    ("HK", "中国香港"),
    ("TW", "中国台湾"),
    ("SG", "新加坡"),
    ("MY", "马来西亚"),
    ("TH", "泰国"),
    ("VN", "越南"),
    ("ID", "印度尼西亚"),
    ("PH", "菲律宾"),
    ("IN", "印度"),
    ("BD", "孟加拉"),
    ("PK", "巴基斯坦"),
    ("AU", "澳大利亚"),
    ("ZA", "南非"),
]

# 静态住宅 IP：30 天价（元）
RESIDENTIAL_PRICES = {
    "US": 60, "GB": 79, "CA": 80, "KR": 89, "JP": 85, "IN": 89, "AE": 85,
    "ID": 99, "VN": 85, "TH": 99, "HK": 79, "ES": 79, "NL": 60, "TW": 109,
    "MY": 99, "FR": 79, "SG": 79, "BD": 79, "MX": 99, "BG": 69, "DK": 69,
    "IL": 60, "HU": 69, "BR": 85, "PL": 89, "TR": 75, "AU": 69, "IT": 75,
    "DE": 85, "PK": 120, "ZA": 75, "SA": 89, "PH": 99,
}

# 社媒运营专线：30 天价（元），原生标签
SOCIAL_PRICES = {
    "US": 95, "MX": 131, "CA": 113, "HK": 113, "MY": 127, "KR": 126,
    "JP": 123,   # 原表日本有两行（115 / 123），取高值
    "SA": 130, "IN": 116, "TW": 124, "ID": 110, "VN": 115, "TH": 132,
    "SG": 121, "PH": 129, "GB": 115, "FR": 108, "DE": 102, "RU": 116,
    "ES": 118, "IT": 113, "AU": 107, "BR": 129,
}

# 直播专线：不分国家，按规格统一价（元 / 30 天）
LIVE_PRICES = {"mobile": 600, "pc": 1100}

# 分类编码与展示名。直播拆成两个分类，是因为唯一约束只认 (分类, 国家, 周期)，
# 同一分类里没法给"手机/电脑"两个价；官网把它们合并成一个「直播专线」页签。
CATEGORY_LABELS = {
    "static_residential": "静态住宅IP",
    "social_media": "社媒运营专线",
    "live_stream_mobile": "直播专线（手机）",
    "live_stream_pc": "直播专线（电脑）",
}

# 分类 → 官网产品线
CATEGORY_TO_LINE = {
    "static_residential": "residential",
    "static_residential_native": "residential",
    "static_residential_isp": "residential",
    "static_datacenter": "residential",
    "social_media": "social",
    "live_stream_mobile": "live",
    "live_stream_pc": "live",
}

# 当前**在售**的分类：官网只对这些分类报价与下单。
# 其余映射到产品线的分类（本土原生 / 运营商原生 / 机房）是并入住宅线之前的
# 历史分类，保留映射是为了让旧商品仍能在后台被找到，但它们不再对外售卖——
# 判断"官网缺不缺价"要看 ACTIVE_CATEGORIES，否则会把一堆历史价则算成缺口。
ACTIVE_CATEGORIES = frozenset({
    "static_residential",
    "social_media",
    "live_stream_mobile",
    "live_stream_pc",
})

LEGACY_CATEGORIES = frozenset(CATEGORY_TO_LINE) - ACTIVE_CATEGORIES

LINE_LABELS = {
    "residential": "静态住宅IP",
    "social": "社媒运营专线",
    "live": "直播专线",
}

LINE_ORDER = {"residential": 1, "social": 2, "live": 3}

# 直播规格标签（官网展示用）
LIVE_SPEC_LABELS = {"live_stream_mobile": "手机", "live_stream_pc": "电脑"}


def price_rows() -> list[tuple[str, str, int]]:
    """(分类, 国家代码, 30 天价)。直播按国家展开成同价。"""
    rows: list[tuple[str, str, int]] = []
    for code, price in RESIDENTIAL_PRICES.items():
        rows.append(("static_residential", code, price))
    for code, price in SOCIAL_PRICES.items():
        rows.append(("social_media", code, price))
    for code, _name in COUNTRIES:
        rows.append(("live_stream_mobile", code, LIVE_PRICES["mobile"]))
        rows.append(("live_stream_pc", code, LIVE_PRICES["pc"]))
    return rows


def missing_prices() -> dict[str, list[str]]:
    """按产品线列出缺报价的国家，供后台/验收时核对。"""
    all_codes = [code for code, _ in COUNTRIES]
    have = {
        "residential": set(RESIDENTIAL_PRICES),
        "social": set(SOCIAL_PRICES),
        "live": set(all_codes),
    }
    return {line: [code for code in all_codes if code not in have[line]] for line in have}
