// @vitest-environment jsdom
// 官网 /buy 的顺序：先选产品（4 个），再选国家。
// 之前是「先挑地区再看该地区有什么产品」，客户要卖的东西在第二屏，顺序反了。
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  getPublicCatalog: vi.fn(), previewOrder: vi.fn(), getCatalogCities: vi.fn(),
}))
const sessionModule = vi.hoisted(() => ({ session: { user: null } }))
const routerMock = vi.hoisted(() => ({ push: vi.fn() }))
const routeMock = vi.hoisted(() => ({ value: { query: {}, fullPath: '/buy' } }))

vi.mock('../src/api', () => api)
vi.mock('../src/api/session', () => sessionModule)
vi.mock('vue-router', () => ({ useRoute: () => routeMock.value, useRouter: () => routerMock }))

import BuyFlow from '../src/components/BuyFlow.vue'

const catalog = {
  price_visible: false,
  lines: [
    {
      code: 'residential', name: '静态住宅IP', min_price: null, periods: [30, 90, 180, 360], region_count: 1, in_stock: true,
      regions: [{ id: 2, name: '美国', country_code: 'US', min_price: null, in_stock: true,
        offers: [{ product_id: 7, billing_days: 30, unit_price: null, in_stock: true, spec: null }] }],
    },
    {
      code: 'live', name: '直播专线', min_price: null, periods: [30], region_count: 1, in_stock: true,
      regions: [{ id: 2, name: '美国', country_code: 'US', min_price: null, in_stock: true,
        offers: [
          { product_id: 20, billing_days: 30, unit_price: null, in_stock: true, spec: '手机' },
          { product_id: 21, billing_days: 30, unit_price: null, in_stock: true, spec: '电脑' },
        ] }],
    },
  ],
}

beforeEach(() => {
  vi.clearAllMocks()
  api.getPublicCatalog.mockResolvedValue({ data: catalog })
  api.getCatalogCities.mockResolvedValue({ data: { supported: false, items: [], auto_label: '自动分配' } })
  sessionModule.session.user = null
})

describe('官网选购顺序', () => {
  it('STEP 01 是产品，STEP 02 才是国家', async () => {
    const wrapper = mount(BuyFlow)
    await flushPromises()

    const text = wrapper.text()
    expect(text.indexOf('选择产品')).toBeGreaterThan(-1)
    expect(text.indexOf('选择产品')).toBeLessThan(text.indexOf('选择国家'))
    expect(wrapper.find('.step-rail').text()).toContain('01 产品')
  })

  it('产品固定四个，直播拆成手机与电脑两档', async () => {
    const wrapper = mount(BuyFlow)
    await flushPromises()

    const cards = wrapper.findAll('.type-picker button')
    expect(cards).toHaveLength(4)
    const labels = cards.map((item) => item.text())
    expect(labels[2]).toContain('手机直播专线')
    expect(labels[3]).toContain('电脑直播专线')
    // 社媒没有商品：卡片禁用，并说明原因
    expect(labels[1]).toContain('暂无可售国家')
    expect(cards[1].attributes('disabled')).toBeDefined()
  })

  it('未登录也能选产品和城市，价格统一显示登录可见', async () => {
    const wrapper = mount(BuyFlow)
    await flushPromises()

    expect(wrapper.text()).toContain('登录后查看价格')
    expect(wrapper.text()).toContain('登录后显示')
    // 未登录不调算价接口
    expect(api.previewOrder).not.toHaveBeenCalled()
  })

  it('换产品后国家列表跟着换，不残留上一个产品的选择', async () => {
    const wrapper = mount(BuyFlow)
    await flushPromises()

    const cards = wrapper.findAll('.type-picker button')
    await cards[2].trigger('click')
    await flushPromises()

    expect(wrapper.findAll('.region-picker button')).toHaveLength(1)
    expect(wrapper.find('.order-summary').text()).toContain('手机直播专线')
  })
})
