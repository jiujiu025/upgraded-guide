// @vitest-environment jsdom
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  cancelAdminOrder: vi.fn(), cancelCustomerOrder: vi.fn(), createSalesUser: vi.fn(),
  completeAdminRenewal: vi.fn(), confirmAdminOrder: vi.fn(), deliverAdminOrder: vi.fn(),
  getAdminOrders: vi.fn(), getCustomerLine: vi.fn(), getCustomerLines: vi.fn(),
  getCustomerMe: vi.fn(), getCustomerOrder: vi.fn(), getCustomerOrders: vi.fn(),
  getCustomerRenewals: vi.fn(), getManagedLine: vi.fn(), getNotifications: vi.fn(),
  getSalesCustomers: vi.fn(), getSalesDashboard: vi.fn(), getSalesExpiringLines: vi.fn(),
  getSalesOrders: vi.fn(), getSalesOutreach: vi.fn(), getSecurityQuestions: vi.fn(),
  markAllNotificationsRead: vi.fn(), markNotificationRead: vi.fn(), recordRenewalTouch: vi.fn(),
  renewCustomerLine: vi.fn(), updateSecurityQuestions: vi.fn(), updateCustomerLineNote: vi.fn(),
  logout: vi.fn(),
}))
const sessionModule = vi.hoisted(() => ({ session: { user: { username: 'sales_a', role: 'sales', display_name: '销售甲' } } }))
const healthMock = vi.hoisted(() => ({ loadHealthFixture: vi.fn() }))
const routerMock = vi.hoisted(() => ({
  replace: vi.fn(), push: vi.fn(),
  currentRoute: { value: { meta: { workspace: 'sales', page: 'sales' }, params: {}, path: '/sales', fullPath: '/sales' } },
}))

vi.mock('../src/api', () => api)
vi.mock('../src/api/session', () => sessionModule)
vi.mock('../src/mocks/health', () => healthMock)
vi.mock('vue-router', () => ({ useRoute: () => routerMock.currentRoute.value, useRouter: () => routerMock }))

import WorkspaceView from '../src/views/WorkspaceView.vue'

const dashboard = {
  customers: 2, active_lines: 3, expiring_in_7_days: 2, pending_confirm_orders: 0,
  pending_delivery_orders: 0, unread_notifications: 0, monthly_sales: 1200, monthly_renewals: 600,
}

const outreach = {
  items: [
    {
      line_id: 11, customer_id: 1, customer_name: '客户甲', contact: '13800000001',
      line_name: '美国-LA01', product_name: '美国原生独享', product_type: '原生独享',
      region: '美国', ip: '1.2.3.4', expires_at: '2026-11-05T12:00:00', days_left: -3,
      bucket: 'overdue', renewal_days: 30, renewal_price: 100, price_missing: false,
      last_touch: null,
      suggested_message: '客户甲，您好。美国-LA01（美国原生独享）已于 2026-11-05 到期（已过期 3 天）。续费 30 天为 ¥100.00，需要我这边直接安排吗？',
    },
    {
      line_id: 12, customer_id: 2, customer_name: '客户乙', contact: '',
      line_name: '英国-LA02', product_name: '英国原生独享', product_type: '原生独享',
      region: '英国', ip: null, expires_at: '2026-11-15T12:00:00', days_left: 7,
      bucket: 'urgent', renewal_days: 30, renewal_price: null, price_missing: true,
      last_touch: {
        id: 1, channel: 'wechat', channel_label: '微信', outcome: 'promised',
        outcome_label: '已答应续费', message: '旧文案', note: null,
        sales_name: '销售甲', created_at: '2026-11-07T10:00:00', days_ago: 1,
      },
      suggested_message: '客户乙，您好。英国-LA02（英国原生独享）将于 2026-11-15 到期（还剩 7 天）。需要我这边安排续费吗？具体金额我确认后发您。',
    },
  ],
  summary: { total: 2, overdue: 1, urgent: 1, soon: 0, touched: 1, untouched: 1, promised: 1 },
  channels: [{ value: 'wechat', label: '微信' }, { value: 'phone', label: '电话' }],
  outcomes: [{ value: 'sent', label: '已发送，等回复' }, { value: 'promised', label: '已答应续费' }],
  window_days: 30,
  overdue_grace_days: 15,
}

const ok = (data) => Promise.resolve({ data })

beforeEach(() => {
  Object.values(api).forEach((fn) => fn.mockReset())
  api.getSalesDashboard.mockImplementation(() => ok(dashboard))
  api.getSalesCustomers.mockImplementation(() => ok([{ id: 1, name: '客户甲', email: 'a@example.com', phone: null, status: '正常' }]))
  api.getSalesOrders.mockImplementation(() => ok([]))
  api.getSalesExpiringLines.mockImplementation(() => ok([]))
  api.getSalesOutreach.mockImplementation(() => ok(outreach))
  api.recordRenewalTouch.mockImplementation(() => ok({ id: 9 }))
  healthMock.loadHealthFixture.mockImplementation(() => Promise.resolve({ incidents: [] }))
})

async function mountSales() {
  const wrapper = mount(WorkspaceView)
  await flushPromises()
  return wrapper
}

describe('销售工作台 · 到期触达', () => {
  it('按紧急度展示线路，续费价与上次触达都在行内', async () => {
    const wrapper = await mountSales()
    const rows = wrapper.findAll('.outreach-row')
    expect(rows).toHaveLength(2)
    expect(rows[0].text()).toContain('客户甲')
    expect(rows[0].text()).toContain('已过期 3 天')
    expect(rows[0].text()).toContain('¥100.00')
    expect(rows[0].text()).toContain('未联系')
    // 缺价时给"价格待确认"，不能显示 0 元
    expect(rows[1].text()).toContain('价格待确认')
    expect(rows[1].text()).toContain('已答应续费')
    expect(wrapper.find('.outreach-stats').text()).toContain('已过期 1')
  })

  it('记一笔会带上当时的文案与渠道结果', async () => {
    const wrapper = await mountSales()
    await wrapper.findAll('.outreach-actions button')[1].trigger('click')
    expect(wrapper.find('.outreach-form').exists()).toBe(true)

    await wrapper.find('.outreach-form select').setValue('phone')
    await wrapper.findAll('.outreach-form select')[1].setValue('promised')
    await wrapper.find('.outreach-form input').setValue('说周五打款')
    await wrapper.find('.outreach-form .outline-action').trigger('click')
    await flushPromises()

    expect(api.recordRenewalTouch).toHaveBeenCalledTimes(1)
    const [lineId, payload] = api.recordRenewalTouch.mock.calls[0]
    expect(lineId).toBe(11)
    expect(payload.channel).toBe('phone')
    expect(payload.outcome).toBe('promised')
    expect(payload.note).toBe('说周五打款')
    expect(payload.message).toBe(outreach.items[0].suggested_message)
    // 保存后重新拉取清单，界面上的"未联系"才会更新
    expect(api.getSalesOutreach).toHaveBeenCalledTimes(2)
  })
})
