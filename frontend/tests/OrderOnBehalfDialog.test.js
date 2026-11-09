// @vitest-environment jsdom
// 代客下单弹窗：销售看不到折扣输入、管理员能看到；下单带上幂等键与折扣。
// 规则来源 docs/销售代客下单设计.md（折扣只有管理员能设，默认上限 30%）。
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  getPublicProducts: vi.fn(),
  previewOrder: vi.fn(),
  createOnBehalfOrder: vi.fn(),
}))

vi.mock('../src/api', () => api)

import OrderOnBehalfDialog from '../src/components/OrderOnBehalfDialog.vue'

const customer = { id: 7, name: 'A 的客户', phone: '13800000000', email: 'a@test.com', has_account: true }
const product = {
  id: 3, name: '美国 独享静态住宅 30天', region: '美国', region_id: 1,
  price: 60, billing_days: 30, is_in_stock: true, is_active: true,
  periods: [{ billing_days: 30, unit_price: 60 }, { billing_days: 90, unit_price: 180 }],
}

function mountDialog(props = {}) {
  return mount(OrderOnBehalfDialog, {
    props: { open: true, customer, canDiscount: false, maxDiscountPercent: 30, ...props },
    global: { stubs: { Teleport: true } },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  api.getPublicProducts.mockImplementation(() => Promise.resolve({ data: [product] }))
  api.previewOrder.mockImplementation(() => Promise.resolve({
    data: { product_id: 3, quantity: 1, billing_days: 30, unit_price: 60, total_amount: 60, periods: product.periods },
  }))
  api.createOnBehalfOrder.mockImplementation((payload) => Promise.resolve({
    data: { id: 11, order_no: 'ORD-1', total_amount: payload.discount_percent ? 42 : 60 },
  }))
})

describe('OrderOnBehalfDialog', () => {
  it('销售看不到折扣输入，提交不带 discount_percent', async () => {
    const wrapper = mountDialog({ canDiscount: false })
    await flushPromises()
    expect(wrapper.text()).not.toContain('折扣（%）')

    await wrapper.find('form').trigger('submit')
    await flushPromises()

    const payload = api.createOnBehalfOrder.mock.calls[0][0]
    expect(payload.customer_id).toBe(7)
    expect(payload.product_id).toBe(3)
    expect(payload).not.toHaveProperty('discount_percent')
    expect(payload.idempotency_key.length).toBeGreaterThanOrEqual(36)
    expect(wrapper.emitted('created')).toBeTruthy()
  })

  it('管理员看到折扣输入，提交带上比例', async () => {
    const wrapper = mountDialog({ canDiscount: true })
    await flushPromises()
    expect(wrapper.text()).toContain('折扣（%）')

    const discount = wrapper.findAll('input[type="number"]').at(-1)
    await discount.setValue(30)
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(api.createOnBehalfOrder.mock.calls[0][0].discount_percent).toBe(30)
  })

  it('客户没有登录账号时只能选联系经理', async () => {
    const wrapper = mountDialog({ customer: { ...customer, has_account: false } })
    await flushPromises()
    const options = wrapper.findAll('select').at(-1).findAll('option')
    const disabled = options.filter((option) => option.attributes('disabled') !== undefined)
    expect(disabled.length).toBe(2)
    expect(wrapper.find('form').html()).toContain('value="contact_manager"')
  })

  it('同一弹窗重复提交复用同一个幂等键', async () => {
    const wrapper = mountDialog()
    await flushPromises()
    api.createOnBehalfOrder.mockImplementationOnce(() => Promise.reject(Object.assign(new Error('net'), { userMessage: '网络错误' })))
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    const [first, second] = api.createOnBehalfOrder.mock.calls
    expect(first[0].idempotency_key).toBe(second[0].idempotency_key)
  })
})
