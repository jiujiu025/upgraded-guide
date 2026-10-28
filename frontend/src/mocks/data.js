export const products = [
  { id: 'live', slug: 'live', name: '直播线路', short: 'LIVE ROUTE', summary: '为多平台直播团队提供地区清晰、交付明确的专用线路。', scenes: '跨境直播 / 店播 / 连麦', platforms: 'TikTok · YouTube · Shopee', delivery: '节点链接 · 订阅地址', protocols: 'Clash · V2Ray', price: 39, accent: 'cyan' },
  { id: 'video', slug: 'video', name: '短视频线路', short: 'VIDEO ROUTE', summary: '支持内容团队按地区配置创作、发布与账号运营网络。', scenes: '短视频矩阵 / 内容发布', platforms: 'TikTok · Instagram · YouTube', delivery: '订阅地址 · 二维码', protocols: 'Clash · NekoBox', price: 29, accent: 'orange' },
  { id: 'social', slug: 'social', name: '社媒线路', short: 'SOCIAL ROUTE', summary: '面向社媒运营的地区线路，便于团队分工与节点管理。', scenes: '社媒运营 / 社群维护', platforms: 'Meta · X · LinkedIn', delivery: '节点链接 · 使用说明', protocols: 'V2Ray · Clash', price: 25, accent: 'blue' },
  { id: 'residential-ip', slug: 'residential-ip', name: '静态住宅 IP', short: 'STATIC RESIDENTIAL', summary: '为需要固定出口和长期地区识别的业务场景提供静态住宅 IP。', scenes: '店铺访问 / 风控验证 / 账号维护', platforms: '浏览器 · 客户端', delivery: '静态 IP · 端口信息', protocols: 'HTTP · SOCKS5', price: 59, accent: 'yellow' },
]
export const regions = ['美国', '英国', '日本', '新加坡', '韩国', '德国', '法国', '加拿大', '澳大利亚', '阿联酋']
export const helpArticles = [
  { slug: 'clash', category: '客户端', title: 'Clash 导入线路的完整步骤', desc: '从订阅地址到策略组，完成第一次连接。', time: '6 分钟阅读' },
  { slug: 'v2ray', category: '客户端', title: 'V2Ray 客户端配置指南', desc: '适合 Windows 与 macOS 的基础配置流程。', time: '5 分钟阅读' },
  { slug: 'nekobox', category: '客户端', title: 'NekoBox 移动端导入教程', desc: 'Android 用户如何使用二维码与订阅链接。', time: '4 分钟阅读' },
  { slug: 'import', category: '线路使用', title: '节点链接、订阅地址与二维码', desc: '三种交付形式的区别与使用场景。', time: '3 分钟阅读' },
]
export const resources = [
  { slug: 'go-global-network', tag: '出海网络指南', title: '跨境团队如何建立可交付的网络线路流程', date: '2026.06.18', featured: true },
  { slug: 'live-stability', tag: '直播网络指南', title: '直播前的线路检查清单：从节点到客户端', date: '2026.06.12' },
  { slug: 'ip-usage', tag: 'IP 使用指南', title: '静态住宅 IP 适合哪些业务场景？', date: '2026.06.05' },
]
