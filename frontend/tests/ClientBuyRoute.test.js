// @vitest-environment jsdom
// 客户中心内完成选购：之前「购买线路」指向官网 /buy，点进去就离开客户中心且没有返回入口。
// 这里锁住两件事：导航指向 /client/buy，且该 page 在客户工作台里渲染选购面板。
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  cancelAdminOrder: vi.fn(), cancelCustomerOrder: vi.fn(), createSalesUser: vi.fn(),
  completeAdminRenewal: vi.fn(), confirmAdminOrder: vi.fn(), deliverAdminOrder: vi.fn(),
  getAdminOrders: vi.fn(), getCustomerLine: vi.fn(), getCustomerLines: vi.fn(),
  getCustomerMe: vi.fn(), getCustomerOrder: vi.fn(), getCustomerOrders: vi.fn(),
  getCustomerRenewals: vi.fn(), getManagedLine: vi.fn(), getNotifications: vi.fn(),
  getSalesCustomers: vi.fn(), getSalesDashboard: vi.fn(), getSalesExpiringLines: vi.fn(),
  getSalesInviteCodes: vi.fn(), getSalesOrders: vi.fn(), getSalesOutreach: vi.fn(),
  getSecurityQuestions: vi.fn(), markAllNotificationsRead: vi.fn(), markNotificationRead: vi.fn(),
  recordRenewalTouch: vi.fn(), renewCustomerLine: vi.fn(), updateSecurityQuestions: vi.fn(),
  updateCustomerLineNote: vi.fn(), logout: vi.fn(),
  // 客户中心选购面板用到的
  getCatalog: vi.fn(), previewOrder: vi.fn(), createCustomerOrder: vi.fn(), getCatalogCities: vi.fn(),
}))
const sessionModule = vi.hoisted(() => ({ session: { user: { username: 'test_customer', role: 'customer', display_name: '测试客户' } } }))
const healthMock = vi.hoisted(() => ({ loadHealthFixture: vi.fn() }))
const routerMock = vi.hoisted(() => ({
  replace: vi.fn(), push: vi.fn(),
  currentRoute: { value: { meta: { workspace: 'client', page: 'client-buy' }, params: {}, path: '/client/buy', fullPath: '/client/buy' } },
}))

vi.mock('../src/api', () => api)
vi.mock('../src/api/session', () => sessionModule)
vi.mock('../src/mocks/health', () => healthMock)
vi.mock('vue-router', () => ({ useRoute: () => routerMock.currentRoute.value, useRouter: () => routerMock }))

import WorkspaceView from '../src/views/WorkspaceView.vue'

const catalog = {
  price_visible: true,
  lines: [
    {
      code: 'residential', name: '静态住宅IP', min_price: 60, periods: [30, 90], region_count: 1, in_stock: true,
      regions: [{ id: 2, name: '美国', country_code: 'US', min_price: 60, in_stock: true,
        offers: [{ product_id: 7, billing_days: 30, unit_price: 60, in_stock: true, spec: null }] }],
    },
    {
      code: 'live', name: '直播专线', min_price: 600, periods: [30], region_count: 1, in_stock: true,
      regions: [{ id: 2, name: '美国', country_code: 'US', min_price: 600, in_stock: true,
        offers: [
          { product_id: 20, billing_days: 30, unit_price: 600, in_stock: true, spec: '手机' },
          { product_id: 21, billing_days: 30, unit_price: 1100, in_stock: true, spec: '电脑' },
        ] }],
    },
  ],
}

beforeEach(() => {
  vi.clearAllMocks()
  api.getCatalog.mockResolvedValue({ data: catalog })
  api.previewOrder.mockResolvedValue({ data: { unit_price: 60, total_amount: 60, billing_days: 30, quantity: 1 } })
  api.createCustomerOrder.mockResolvedValue({ data: { id: 99 } })
  api.getCustomerMe.mockResolvedValue({ data: { id: 4, name: '测试客户', balance: 0 } })
  api.getCustomerOrders.mockResolvedValue({ data: { items: [], total: 0, status_counts: {} } })
  api.getCustomerLines.mockResolvedValue({ data: [] })
  api.getCustomerRenewals.mockResolvedValue({ data: [] })
  api.getNotifications.mockResolvedValue({ data: [] })
  api.getCatalogCities.mockResolvedValue({ data: { supported: false, items: [], auto_label: '自动分配' } })
})

describe('客户中心内选购', () => {
  it('在客户工作台里渲染选购面板，不跳官网', async () => {
    const wrapper = mount(WorkspaceView)
    await flushPromises()

    expect(api.getCatalog).toHaveBeenCalled()
    expect(wrapper.text()).toContain('在本页完成选购')
    expect(wrapper.text()).toContain('静态住宅IP')
    expect(wrapper.text()).toContain('美国')
  })

  it('选城市与号段后，下单请求里带上城市码和号段', async () => {
    api.getCatalogCities.mockResolvedValue({
      data: {
        supported: true, country_name: '美国', auto_label: '自动分配',
        items: [{ city_code: 'LAX', city_name: '洛杉矶', segments: ['23.94.5', '23.94.6'] }],
      },
    })
    const wrapper = mount(WorkspaceView)
    await flushPromises()
    // 算价是 250ms 防抖，等它跑完 submit 才是可点的
    await new Promise((resolve) => setTimeout(resolve, 320))
    await flushPromises()

    const cityCard = wrapper.findAll('.city-card').find((item) => item.text().includes('美国 — 洛杉矶'))
    expect(cityCard).toBeTruthy()
    await cityCard.trigger('click')
    await flushPromises()

    const segment = wrapper.findAll('.segment-list button').find((item) => item.text() === '23.94.6')
    await segment.trigger('click')
    await flushPromises()

    await wrapper.find('.buy-submit').trigger('click')
    await flushPromises()

    expect(api.createCustomerOrder).toHaveBeenCalledWith(expect.objectContaining({
      product_id: 7, city_code: 'LAX', ip_segment: '23.94.6',
    }))
  })

  it('产品按 4 个列出，先选产品再选国家', async () => {
    const wrapper = mount(WorkspaceView)
    await flushPromises()

    const cards = wrapper.findAll('.product-grid button')
    expect(cards).toHaveLength(4)
    const labels = cards.map((item) => item.text())
    expect(labels[0]).toContain('静态住宅IP')
    expect(labels[1]).toContain('社媒运营专线')
    expect(labels[2]).toContain('手机直播专线')
    expect(labels[3]).toContain('电脑直播专线')

    // 社媒在这个 fixture 里没有商品，卡片要标出来而不是给一个空列表
    expect(labels[1]).toContain('0 个国家/地区')

    // 选手机直播后，国家列表来自该产品，规格固定为手机
    const mobile = cards.find((item) => item.text().includes('手机直播专线'))
    await mobile.trigger('click')
    await flushPromises()
    await new Promise((resolve) => setTimeout(resolve, 320))
    await flushPromises()

    await wrapper.find('.buy-submit').trigger('click')
    await flushPromises()
    expect(api.createCustomerOrder).toHaveBeenCalledWith(expect.objectContaining({ product_id: 20 }))
  })

  it('侧边栏「购买线路」指向 /client/buy', async () => {
    const wrapper = mount(WorkspaceView)
    await flushPromises()

    const navButton = wrapper.findAll('nav button').find((item) => item.text().includes('购买线路'))
    expect(navButton).toBeTruthy()
    await navButton.trigger('click')
    expect(routerMock.push).toHaveBeenCalledWith('/client/buy')
  })
})
