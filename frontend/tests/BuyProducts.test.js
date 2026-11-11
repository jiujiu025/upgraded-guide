// 下单入口把「三条产品线」派生成 4 个客户可选的产品：静态住宅 / 社媒 / 手机直播 / 电脑直播。
// 这里锁住派生规则，避免以后改目录结构时把直播的手机和电脑串到一起。
import { describe, expect, it } from 'vitest'
import { BUY_PRODUCTS, buildProductViews, pickDefaultProduct } from '../src/utils/buyProducts'

const lines = [
  {
    code: 'residential', periods: [30, 90, 180, 360],
    regions: [
      { id: 2, name: '美国', country_code: 'US', offers: [
        { product_id: 7, billing_days: 30, unit_price: 79, in_stock: true, spec: null },
      ] },
      { id: 3, name: '德国', country_code: 'DE', offers: [
        { product_id: 8, billing_days: 30, unit_price: null, in_stock: true, spec: null },
      ] },
    ],
  },
  {
    code: 'live', periods: [30],
    regions: [
      { id: 2, name: '美国', country_code: 'US', offers: [
        { product_id: 20, billing_days: 30, unit_price: 600, in_stock: true, spec: '手机' },
        { product_id: 21, billing_days: 30, unit_price: 1100, in_stock: true, spec: '电脑' },
      ] },
      { id: 4, name: '日本', country_code: 'JP', offers: [
        { product_id: 22, billing_days: 30, unit_price: 600, in_stock: false, spec: '手机' },
      ] },
    ],
  },
]

describe('下单产品派生', () => {
  it('固定拆成 4 个产品，顺序是住宅 → 社媒 → 手机直播 → 电脑直播', () => {
    const views = buildProductViews(lines)
    expect(views.map((item) => item.code)).toEqual(['residential', 'social', 'live_mobile', 'live_pc'])
    expect(views.map((item) => item.name)).toEqual(BUY_PRODUCTS.map((item) => item.name))
  })

  it('直播的手机与电脑各自只保留自己那一档 offer', () => {
    const views = buildProductViews(lines)
    const mobile = views.find((item) => item.code === 'live_mobile')
    const pc = views.find((item) => item.code === 'live_pc')

    expect(mobile.regions.map((region) => region.name)).toEqual(['美国', '日本'])
    expect(mobile.regions[0].offers.map((offer) => offer.product_id)).toEqual([20])
    // 日本只有手机没有电脑，电脑直播不该出现这个国家
    expect(pc.regions.map((region) => region.name)).toEqual(['美国'])
    expect(pc.regions[0].offers.map((offer) => offer.product_id)).toEqual([21])
  })

  it('没有商品的产品一个国家都不给，避免客户点进去是空的', () => {
    const social = buildProductViews(lines).find((item) => item.code === 'social')
    expect(social.regions).toEqual([])
    expect(social.regionCount).toBe(0)
    expect(social.minPrice).toBeNull()
  })

  it('价格聚合跳过 null（未登录脱敏），不当成 0', () => {
    const residential = buildProductViews(lines).find((item) => item.code === 'residential')
    // 德国 unit_price 是 null：国家仍然可选，但不能拉低起价
    expect(residential.regions.map((region) => region.name)).toEqual(['美国', '德国'])
    expect(residential.minPrice).toBe(79)
    expect(residential.regions[1].min_price).toBeNull()
  })

  it('产品周期跟着所属产品线走', () => {
    const views = buildProductViews(lines)
    expect(views.find((item) => item.code === 'residential').periods).toEqual([30, 90, 180, 360])
    expect(views.find((item) => item.code === 'live_pc').periods).toEqual([30])
  })

  it('默认产品选第一个有国家且有货的', () => {
    expect(pickDefaultProduct(buildProductViews(lines)).code).toBe('residential')
    // 只剩一个无国家的产品时不能崩，返回 null
    expect(pickDefaultProduct(buildProductViews([]))).toBeNull()
  })
})
