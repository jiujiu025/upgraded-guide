// @vitest-environment jsdom
// 采购记录页：只读列表 + 详情 + 失败重试入口。锁住四件事：
// 1) 状态计数点一下就带参数重新请求（服务端过滤，不在前端假筛）；
// 2) 「转人工」不给重试按钮——上游没这个档位，重试也买不到；
// 3) 重试调用的是订单侧接口（唯一扣款实现），参数是 order_id；
// 4) 详情只显示下单参数与账号数量，不出现任何账号密码。
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  getProcurements: vi.fn(),
  getProcurement: vi.fn(),
  retryAdminProcurement: vi.fn(),
}))
vi.mock('../src/api', () => api)

import ProcurementRecordsView from '../src/views/ProcurementRecordsView.vue'

const rows = [
  {
    id: 11, created_at: '2026-11-12T00:30:00', status: 'failed', order_id: 7, order_no: 'ORD-2',
    customer_name: '上海某跨境公司', product_name: '静态住宅-美国', supplier_name: '全球代理',
    supplier_code: 'quanqiudaili', external_order_no: null, error_code: 'BALANCE_LOW',
    error_message: '上游余额不足', line_id: null, delivered_accounts: 0, has_response: false,
  },
  {
    id: 10, created_at: '2026-11-12T00:20:00', status: 'succeeded', order_id: 6, order_no: 'ORD-1',
    customer_name: '上海某跨境公司', product_name: '静态住宅-美国', supplier_name: '全球代理',
    supplier_code: 'quanqiudaili', external_order_no: 'UP-9001', error_code: null,
    error_message: null, line_id: 321, has_response: true,
  },
  {
    id: 9, created_at: '2026-11-12T00:10:00', status: 'manual', order_id: 6, order_no: 'ORD-1',
    customer_name: '上海某跨境公司', product_name: '静态住宅-美国', supplier_name: '全球代理',
    supplier_code: 'quanqiudaili', external_order_no: null, error_code: 'UNSUPPORTED_PERIOD',
    error_message: '上游没有 45 天档位', line_id: null, has_response: false,
  },
]

const listPayload = {
  items: rows,
  total: 3,
  page: 1,
  page_size: 25,
  pages: 1,
  counts: { pending: 0, succeeded: 1, failed: 1, manual: 1 },
  suppliers: [{ id: 1, code: 'quanqiudaili', name: '全球代理' }],
}

beforeEach(() => {
  vi.clearAllMocks()
  window.confirm = vi.fn(() => true)
  api.getProcurements.mockResolvedValue({ data: listPayload })
  api.getProcurement.mockResolvedValue({
    data: {
      ...rows[0],
      request_summary: {
        order_no: 'ORD-2', quantity: 2, billing_days: 30, country: 'US',
        city: 'New York', segment: '1.1.1', trigger: 'payment',
      },
      delivered_accounts: 1,
    },
  })
  api.retryAdminProcurement.mockResolvedValue({ data: { status: 'queued' } })
})

describe('采购记录页', () => {
  it('渲染记录、状态与失败原因', async () => {
    const wrapper = mount(ProcurementRecordsView)
    await flushPromises()

    expect(wrapper.text()).toContain('每笔自动开通，都能对上单')
    expect(wrapper.text()).toContain('ORD-2')
    expect(wrapper.text()).toContain('UP-9001')
    expect(wrapper.text()).toContain('上游余额不足')
    expect(wrapper.text()).toContain('BALANCE_LOW')
    expect(wrapper.text()).toContain('已开通')
    expect(wrapper.text()).toContain('转人工')
  })

  it('点状态计数走服务端过滤并回到第一页', async () => {
    const wrapper = mount(ProcurementRecordsView)
    await flushPromises()

    const succeededChip = wrapper.findAll('.proc-chip').find((chip) => chip.text().includes('已开通'))
    await succeededChip.trigger('click')
    await flushPromises()

    const params = api.getProcurements.mock.calls.at(-1)[0]
    expect(params.status).toBe('succeeded')
    expect(params.page).toBe(1)
  })

  it('只有失败记录给重试按钮，重试走订单接口', async () => {
    const wrapper = mount(ProcurementRecordsView)
    await flushPromises()

    const retryButtons = wrapper.findAll('.proc-link').filter((button) => button.text() === '重试')
    expect(retryButtons).toHaveLength(1)

    await retryButtons[0].trigger('click')
    await flushPromises()

    expect(api.retryAdminProcurement).toHaveBeenCalledWith(7)
    expect(wrapper.text()).toContain('已加入后台任务')
  })

  it('详情显示下单参数与账号数量，不出现账号密码', async () => {
    const wrapper = mount(ProcurementRecordsView)
    await flushPromises()

    await wrapper.findAll('.proc-link').find((button) => button.text() === '详情').trigger('click')
    await flushPromises()

    expect(api.getProcurement).toHaveBeenCalledWith(11)
    const text = wrapper.text()
    expect(text).toContain('下单参数')
    expect(text).toContain('New York')
    expect(text).toContain('30 天')
    expect(text).toContain('交付账号数')
    expect(text).not.toContain('password')
    expect(text).not.toContain('username')
  })
})
