// 下单入口的「产品」定义：官网和客户中心共用一套，避免两处顺序与文案不一致。
//
// 数据层仍然是三条产品线（residential / social / live），直播的手机与电脑是同一
// 条线上的两个分类。这里把它们拆成 4 个客户看得懂的产品，前端按 spec 过滤 offer：
// 客户先选产品，再选国家，不用先挑国家才发现「这个国家没有这个规格」。
export const BUY_PRODUCTS = [
  {
    code: 'residential',
    lineCode: 'residential',
    spec: null,
    name: '静态住宅IP',
    desc: '运营商原生 IP（ISP 原生），非机房伪装住宅',
  },
  {
    code: 'social',
    lineCode: 'social',
    spec: null,
    name: '社媒运营专线',
    desc: '社媒多账号运营用的固定出口，按国家选',
  },
  {
    code: 'live_mobile',
    lineCode: 'live',
    spec: '手机',
    name: '手机直播专线',
    desc: '手机端直播用的固定出口，按国家选',
  },
  {
    code: 'live_pc',
    lineCode: 'live',
    spec: '电脑',
    name: '电脑直播专线',
    desc: '电脑端直播用的固定出口，按国家选',
  },
]

const matchesSpec = (offer, spec) => (spec ? offer.spec === spec : !offer.spec)

/**
 * 把后端 catalog 的三条产品线派生成 4 个客户可选的产品。
 *
 * - 只保留该产品**真的有商品**的国家（脱敏时后端也会下发 offer 结构，只是没有金额）；
 * - 每个国家只保留属于该产品的 offer，直播的手机/电脑因此不会串台；
 * - 价格可能是 null（未登录），聚合时跳过，不要当成 0。
 */
export function buildProductViews(lines = []) {
  return BUY_PRODUCTS.map((product) => {
    const line = lines.find((item) => item.code === product.lineCode)
    const regions = (line?.regions || [])
      .map((region) => {
        const offers = (region.offers || []).filter((offer) => matchesSpec(offer, product.spec))
        const prices = offers.map((offer) => offer.unit_price).filter((price) => typeof price === 'number')
        return {
          ...region,
          offers,
          min_price: prices.length ? Math.min(...prices) : null,
        }
      })
      .filter((region) => region.offers.length)
    const prices = regions.flatMap((region) => region.offers.map((offer) => offer.unit_price))
      .filter((price) => typeof price === 'number')
    return {
      ...product,
      regions,
      regionCount: regions.length,
      periods: line?.periods || [],
      inStock: regions.some((region) => region.offers.some((offer) => offer.in_stock)),
      minPrice: prices.length ? Math.min(...prices) : null,
    }
  })
}

/** 默认产品：优先第一个「有国家且有货」的，其次第一个有国家的。 */
export function pickDefaultProduct(products = []) {
  return products.find((item) => item.regionCount > 0 && item.inStock) || products.find((item) => item.regionCount > 0) || null
}
