// @vitest-environment jsdom
// 客户买完静态住宅 IP 之后，线路行尾要有「更多选项 → 升级线路」。
// 这里锁住三件事：入口存在、弹窗调到客户侧接口、视频专线（=社媒运营专线）的差价直接展示。
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

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
  // 升级相关：客户侧、销售侧、管理员侧
  getCustomerLineUpgradeOptions: vi.fn(), upgradeCustomerLine: vi.fn(),
  getSalesLineUpgradeOptions: vi.fn(), upgradeSalesLine: vi.fn(),
  getLineUpgradeOptions: vi.fn(), upgradeLine: vi.fn(),
}))
const sessionModule = vi.hoisted(() => ({
  session: { user: { username: 'test_customer', role: 'customer', display_name: '测试客户' } },
}))
const healthMock = vi.hoisted(() => ({ loadHealthFixture: vi.fn() }))
const routerMock = vi.hoisted(() => ({
  replace: vi.fn(), push: vi.fn(),
  currentRoute: {
    value: { meta: { workspace: 'client', page: 'lines' }, params: {}, path: '/client/lines', fullPath: '/client/lines' },
  },
}))

vi.mock('../src/api', () => api)
vi.mock('../src/api/session', () => sessionModule)
vi.mock('../src/mocks/health', () => healthMock)
vi.mock('vue-router', () => ({ useRoute: () => routerMock.currentRoute.value, useRouter: () => routerMock }))

import WorkspaceView from '../src/views/WorkspaceView.vue'

const line = {
  id: 101, line_name: '美国-LA01', ip: '23.94.5.10', region: '美国',
  product_type: '静态住宅IP', product_name: '美国 静态住宅IP 30天',
  started_at: '2026-11-01T00:00:00', expires_at: '2026-12-01T00:00:00',
  status: 'active', customer_note: null, billing_days: 30,
  periods: [{ billing_days: 30, unit_price: 60 }],
}

const upgradeOptions = {
  current: {
    category: 'static_residential', label: '静态住宅原生IP',
    product_name: '美国 静态住宅IP 30天', unit_price: 60,
    billing_days: 30, expires_at: '2026-12-01T00:00:00',
  },
  options: [
    { category: 'social_media', label: '社媒运营专线', product_id: 12,
      product_name: '美国 社媒运营专线 30天', unit_price: 95, diff: 35 },
    { category: 'live_stream_pc', label: '直播专线（电脑）', product_id: 13,
      product_name: '美国 直播专线（电脑） 30天', unit_price: 1100, diff: 1040 },
  ],
}

let wrapper = null

beforeEach(() => {
  vi.clearAllMocks()
  api.getCustomerOrders.mockResolvedValue({ data: { items: [], total: 0, status_counts: {} } })
  api.getCustomerLines.mockResolvedValue({ data: [line] })
  api.getCustomerRenewals.mockResolvedValue({ data: [] })
  api.getNotifications.mockResolvedValue({ data: [] })
  api.getCustomerMe.mockResolvedValue({ data: { id: 4, name: '测试客户' } })
  api.getCustomerLineUpgradeOptions.mockResolvedValue({ data: upgradeOptions })
  api.upgradeCustomerLine.mockResolvedValue({ data: { id: 501, status: 'pending_confirm', total_amount: 35 } })
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  document.body.innerHTML = ''
})

describe('线路更多选项 · 升级', () => {
  it('行尾有「更多选项」，点开能看到升级入口', async () => {
    wrapper = mount(WorkspaceView, { attachTo: document.body })
    await flushPromises()

    const more = wrapper.find('button[aria-label="更多选项"]')
    expect(more.exists()).toBe(true)
    await more.trigger('click')
    await flushPromises()

    const menu = document.querySelector('.line-menu')
    expect(menu).toBeTruthy()
    expect(menu.textContent).toContain('查看线路详情')
    expect(menu.textContent).toContain('续费这条线路')
    expect(menu.textContent).toContain('升级线路')
    expect(menu.textContent).toContain('视频专线 / 直播专线')
  })

  it('点升级后调到客户侧接口，视频专线与差价按后端返回展示', async () => {
    wrapper = mount(WorkspaceView, { attachTo: document.body })
    await flushPromises()

    await wrapper.find('button[aria-label="更多选项"]').trigger('click')
    await flushPromises()
    const upgradeItem = [...document.querySelectorAll('.line-menu button')]
      .find((button) => button.textContent.includes('升级线路'))
    upgradeItem.click()
    await flushPromises()

    expect(api.getCustomerLineUpgradeOptions).toHaveBeenCalledWith(101)
    const dialog = document.querySelector('.lup-card')
    expect(dialog).toBeTruthy()
    // 客户侧把 social_media 显示成「视频专线」，小字保留原名
    expect(dialog.textContent).toContain('视频专线')
    expect(dialog.textContent).toContain('社媒运营专线')
    expect(dialog.textContent).toContain('¥35.00')
    expect(dialog.textContent).toContain('到期时间不变')
  })

  it('提交升级单时带目标分类与幂等键', async () => {
    wrapper = mount(WorkspaceView, { attachTo: document.body })
    await flushPromises()

    await wrapper.find('button[aria-label="更多选项"]').trigger('click')
    await flushPromises()
    ;[...document.querySelectorAll('.line-menu button')]
      .find((button) => button.textContent.includes('升级线路'))
      .click()
    await flushPromises()

    document.querySelector('.lup-submit').click()
    await flushPromises()

    expect(api.upgradeCustomerLine).toHaveBeenCalledTimes(1)
    const [lineId, body] = api.upgradeCustomerLine.mock.calls[0]
    expect(lineId).toBe(101)
    expect(body.target_category).toBe('social_media')
    expect(body.payment_method).toBe('contact_manager')
    expect(body.idempotency_key).toBeTruthy()
  })
})
