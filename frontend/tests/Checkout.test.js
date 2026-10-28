// @vitest-environment jsdom
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  previewOrder: vi.fn(),
  createCustomerOrder: vi.fn(),
}))
const router = vi.hoisted(() => ({ push: vi.fn() }))

vi.mock('../src/api', () => api)
vi.mock('vue-router', () => ({ useRouter: () => router }))

import Checkout from '../src/components/Checkout.vue'
import { clearSession, session } from '../src/api/session'

const selection = { product_id: 7, region_id: 3, quantity: 2, billing_days: 30 }
const preview = {
  ...selection,
  product_name: '隔离测试产品',
  region: '隔离测试地区',
  unit_price: 120,
  discount: 0.95,
  total_amount: 228,
}
const originalLocalStorageDescriptor = Object.getOwnPropertyDescriptor(window, 'localStorage')

function mountCheckout() {
  return mount(Checkout, { global: { stubs: { PageIntro: true } } })
}

beforeEach(() => {
  vi.clearAllMocks()
  window.sessionStorage.clear()
  window.localStorage.clear()
  clearSession()
  window.sessionStorage.setItem('checkout_selection', JSON.stringify(selection))
  api.previewOrder.mockResolvedValue({ data: preview })
  api.createCustomerOrder.mockResolvedValue({ data: { id: 91 } })
})

afterEach(() => {
  if (originalLocalStorageDescriptor) Object.defineProperty(window, 'localStorage', originalLocalStorageDescriptor)
})

describe('结算页', () => {
  it('加载后端价格预览并展示权威金额', async () => {
    const wrapper = mountCheckout()
    await flushPromises()

    expect(api.previewOrder).toHaveBeenCalledWith(selection)
    expect(wrapper.text()).toContain('隔离测试产品')
    expect(wrapper.text()).toContain('¥228.00')
  })

  it('登录后仅提交选购字段、支付方式和备注，并跳转真实订单', async () => {
    session.user = { role: 'customer' }
    const wrapper = mountCheckout()
    await flushPromises()

    await wrapper.find('textarea').setValue('回归测试备注')
    await wrapper.findAll('button').find((button) => button.text().includes('创建待处理订单')).trigger('click')
    await flushPromises()

    expect(api.createCustomerOrder).toHaveBeenCalledWith({
      ...selection,
      payment_method: 'contact_manager',
      remark: '回归测试备注',
    })
    expect(router.push).toHaveBeenCalledWith('/client/orders/91?created=1')
  })

  it('下单失败时保留页面并显示后端错误', async () => {
    session.user = { role: 'customer' }
    api.createCustomerOrder.mockRejectedValue({ userMessage: '产品库存不足' })
    const wrapper = mountCheckout()
    await flushPromises()

    await wrapper.findAll('button').find((button) => button.text().includes('创建待处理订单')).trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('产品库存不足')
    expect(wrapper.text()).toContain('创建待处理订单')
    expect(router.push).not.toHaveBeenCalled()
  })

  it('localStorage 不可用时仍能渲染价格和登录入口', async () => {
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      get() { throw new DOMException('Storage disabled', 'SecurityError') },
    })

    const wrapper = mountCheckout()
    await flushPromises()

    expect(wrapper.text()).toContain('隔离测试产品')
    expect(wrapper.text()).toContain('登录后创建订单')
    expect(wrapper.text()).toContain('¥228.00')
  })
})
