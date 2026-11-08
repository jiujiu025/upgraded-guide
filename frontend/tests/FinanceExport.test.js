// @vitest-environment jsdom
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  checkPaymentApi: vi.fn(),
  createOfflineRecharge: vi.fn(),
  downloadFinanceExport: vi.fn(),
  getFinanceExports: vi.fn(),
  getFinanceSummary: vi.fn(),
  getPaymentConfig: vi.fn(),
  logout: vi.fn(),
  recordOrderRefund: vi.fn(),
  searchFinanceAccounts: vi.fn(),
  updatePaymentConfig: vi.fn(),
}))

vi.mock('../src/api', () => api)
vi.mock('../src/api/session', () => ({
  session: { user: { role: 'super_admin', display_name: '老板' }, accessToken: 't' },
}))
vi.mock('vue-router', () => ({ useRoute: () => ({ path: '/admin/finance' }), useRouter: () => ({ replace: vi.fn() }) }))
vi.mock('../src/navigation/adminNavigation', () => ({ getAdminNavigation: () => [] }))

import FinanceView from '../src/views/FinanceView.vue'

const emptySummary = {
  date_from: '2026-11-01', date_to: '2026-11-30',
  totals: {}, today: {}, daily: [], sales_commissions: [], recent_payments: [],
}

const exportCatalog = [
  { kind: 'orders', title: '订单明细', basis: '按下单时间归属' },
  { kind: 'payments', title: '收款明细', basis: '按支付时间归属' },
  { kind: 'wallet', title: '钱包流水', basis: '按记账时间归属' },
  { kind: 'monthly', title: '月度汇总', basis: '按自然月汇总' },
]

function ok(data) { return Promise.resolve({ data }) }

function csvResponse() {
  return Promise.resolve({
    data: new Blob(['\ufeff订单号\r\n'], { type: 'text/csv' }),
    headers: { 'content-disposition': 'attachment; filename="finance-orders-20261101-20261130.csv"' },
  })
}

beforeEach(() => {
  api.getFinanceSummary.mockImplementation(() => ok(emptySummary))
  api.getFinanceExports.mockImplementation(() => ok(exportCatalog))
  api.downloadFinanceExport.mockImplementation(csvResponse)
  // jsdom 没有这两样，下载链路要靠它们。
  globalThis.URL.createObjectURL = vi.fn(() => 'blob:mock')
  globalThis.URL.revokeObjectURL = vi.fn()
  // jsdom 真的会尝试导航，拦掉它同时确认下载确实被触发。
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
})

describe('FinanceView 导出', () => {
  it('按接口返回渲染四个导出入口', async () => {
    const wrapper = mount(FinanceView)
    await flushPromises()

    expect(api.getFinanceExports).toHaveBeenCalled()
    const cards = wrapper.findAll('.export-card')
    expect(cards).toHaveLength(4)
    expect(cards[0].text()).toContain('订单明细')
    expect(cards[3].text()).toContain('按自然月汇总')
  })

  it('导出时带上当前日期区间并触发下载', async () => {
    const wrapper = mount(FinanceView)
    await flushPromises()

    const inputs = wrapper.findAll('.date-filters input')
    await inputs[0].setValue('2026-11-01')
    await inputs[1].setValue('2026-11-30')

    await wrapper.findAll('.export-card')[0].trigger('click')
    await flushPromises()

    expect(api.downloadFinanceExport).toHaveBeenCalledWith('orders', {
      date_from: '2026-11-01', date_to: '2026-11-30',
    })
    expect(globalThis.URL.createObjectURL).toHaveBeenCalled()
    expect(wrapper.text()).toContain('订单明细已导出')
  })

  it('后端拒绝时显示具体原因而不是笼统的失败', async () => {
    api.downloadFinanceExport.mockImplementation(() => Promise.reject({
      response: { data: { text: async () => JSON.stringify({ detail: '单次导出最多 366 天，请拆分区间' }) } },
    }))
    const wrapper = mount(FinanceView)
    await flushPromises()

    await wrapper.findAll('.export-card')[3].trigger('click')
    await flushPromises()

    // blob 响应下错误体也是 blob，不解出来用户只会看到「导出失败」。
    expect(wrapper.text()).toContain('单次导出最多 366 天')
  })

  it('报表清单接口失败时仍能导出', async () => {
    api.getFinanceExports.mockImplementation(() => Promise.reject(new Error('network down')))
    const wrapper = mount(FinanceView)
    await flushPromises()

    expect(wrapper.findAll('.export-card')).toHaveLength(4)

    await wrapper.findAll('.export-card')[1].trigger('click')
    await flushPromises()
    expect(api.downloadFinanceExport).toHaveBeenCalledWith('payments', {})
  })
})
