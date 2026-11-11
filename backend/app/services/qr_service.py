"""二维码：把分享链接/订阅地址渲染成 SVG。

用 segno（纯 Python，不依赖 Pillow）。输出 SVG 而不是 PNG 是因为：

- 客户中心与后台都要展示，SVG 任意缩放都不糊；
- 不需要在容器里装图像库，镜像不会因为二维码功能变大。
"""
from __future__ import annotations

import io

import segno


def qr_svg(data: str, *, scale: int = 4, border: int = 2) -> str:
    """生成内联 SVG 字符串（不带 XML 声明，可直接塞进页面）。

    segno 写 SVG 时按二进制模式落到 buffer，所以这里统一走 BytesIO 再解码，
    用 StringIO 会报 "string argument expected, got 'bytes'"。
    """
    buffer = io.BytesIO()
    segno.make(data, error="m").save(
        buffer, kind="svg", scale=scale, border=border, xmldecl=False,
        svgns=True, dark="#102124", light="#ffffff",
    )
    return buffer.getvalue().decode("utf-8")


def qr_svg_bytes(data: str, *, scale: int = 4, border: int = 2) -> bytes:
    return qr_svg(data, scale=scale, border=border).encode("utf-8")
