// @vitest-environment jsdom
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  getAdminOrderCenterOptions: vi.fn(),
  getAdminOrderCenterOrders: vi.fn(),
  getAdminSubscriptions: vi.fn(),
  createOnBehalfOrder: vi.fn(),
  createAdminResourceOrder: vi.fn(),
  logout: vi.fn(),
}))

vi.mock('../src/api', () => api)
vi.mock('../src/api/session', () => ({
  session: { user: { role: 'admin', display_name: '管理员', username: 'admin' } },
}))
vi.mock('vue-router', () => ({
  useRoute: () => ({ path: '/admin/orders' }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}))

import OrderCenterView from '../src/views/OrderCenterView.vue'

const centerOptions = {
  customers: [{ id: 1, name: '客户甲', email: 'a@test.com' }],
  products: [
    { id: 11, name: '美国 静态住宅IP 30天', product_type: '静态住宅IP', price: 60, billing_days: 30,
      region_id: 1, resource_category: 'static_residential', country_code: 'US', country_name: '美国' },
    { id: 12, name: '日本 静态住宅IP 30天', product_type: '静态住宅IP', price: 85, billing_days: 30,
      region_id: 2, resource_category: 'static_residential', country_code: 'JP', country_name: '日本' },
    { id: 21, name: '美国 社媒运营专线 30天', product_type: '社媒运营专线', price: 95, billing_days: 30,
      region_id: 1, resource_category: 'social_media', country_code: 'US', country_name: '美国' },
  ],
  categories: [
    { code: 'static_residential', label: '静态住宅IP' },
    { code: 'social_media', label: '社媒运营专线' },
    { code: 'live_stream_pc', label: '直播专线（电脑）' },
  ],
  city_options: [
    { resource_category: 'static_residential', country_code: 'US', city_code: 'LAX', city_name: '洛杉矶', segments: ['149.52', '23.94.5'] },
    { resource_category: 'static_residential', country_code: 'US', city_code: 'NYC', city_name: '纽约', segments: ['149.40'] },
    { resource_category: 'static_residential', country_code: 'JP', city_code: 'TYO', city_name: '东京', segments: ['163.227'] },
  ],
  retail_prices: [
    { resource_category: 'static_residential', country_code: 'US', currency: 'CNY', prices: { '30': 60, '90': 180, '180': 360, '360': 720 } },
    { resource_category: 'static_residential', country_code: 'JP', currency: 'CNY', prices: { '30': 85 } },
    { resource_category: 'social_media', country_code: 'US', currency: 'CNY', prices: { '30': 95 } },
  ],
  supplier_products: [],
  supplier_regions: [],
  segments: [],
  quotes: [],
  auto_procurement: { enabled: false, source: 'env' },
}

function response(data, headers = {}) {
  return Promise.resolve({ data, headers })
}

async function openAdminOrder(wrapper) {
  const tabs = wrapper.findAll('.order-tabs button')
  await tabs[1].trigger('click')
  await flushPromises()
}

beforeEach(() => {
  api.getAdminOrderCenterOptions.mockImplementation(() => response(centerOptions))
  api.getAdminOrderCenterOrders.mockImplementation(() => response([]))
  api.getAdminSubscriptions.mockImplementation(() => response([]))
  api.createOnBehalfOrder.mockImplementation(() => response({ id: 99, order_no: 'ORD-TEST' }))
})

describe('OrderCenterView 管理员下单四级结构', () => {
  it('渲染 类型 → 国家 → 城市 → 数量 四个步骤', async () => {
    const wrapper = mount(OrderCenterView)
    await flushPromises()
    await openAdminOrder(wrapper)

    const steps = wrapper.findAll('.form-step')
    expect(steps).toHaveLength(4)
    expect(steps[0].text()).toContain('产品类型')
    expect(steps[1].text()).toContain('国家')
    expect(steps[2].text()).toContain('城市')
    expect(steps[3].text()).toContain('数量')
  })

  it('类型卡片只列真的有 SKU 的类型', async () => {
    const wrapper = mount(OrderCenterView)
    await flushPromises()
    await openAdminOrder(wrapper)

    const cards = wrapper.findAll('.type-card')
    // live_stream_pc 没建商品，不该出现在卡片里
    expect(cards).toHaveLength(2)
    expect(cards.map((item) => item.text()).join(' ')).toContain('静态住宅IP')
    expect(cards.map((item) => item.text()).join(' ')).not.toContain('直播专线')
  })

  it('选类型后国家只剩该类型可售的，选国家后带出城市与号段', async () => {
    const wrapper = mount(OrderCenterView)
    await flushPromises()
    await openAdminOrder(wrapper)

    await wrapper.findAll('.type-card')[0].trigger('click')
    await flushPromises()
    const countrySelect = wrapper.findAll('.form-step')[1].find('select')
    const countryTexts = countrySelect.findAll('option').map((item) => item.text())
    expect(countryTexts.join(' ')).toContain('美国')
    expect(countryTexts.join(' ')).toContain('日本')

    await countrySelect.setValue('US')
    await flushPromises()
    const cityStep = wrapper.findAll('.form-step')[2]
    const citySelect = cityStep.find('select')
    const cityTexts = citySelect.findAll('option').map((item) => item.text())
    expect(cityTexts.join(' ')).toContain('洛杉矶')
    // 洛杉矶有 2 个段，副标题要标出来
    expect(cityTexts.join(' ')).toContain('2 个段')

    await citySelect.setValue('LAX')
    await flushPromises()
    const segmentSelects = cityStep.findAll('select')
    const segmentTexts = segmentSelects[1].findAll('option').map((item) => item.text())
    expect(segmentTexts.join(' ')).toContain('149.52')
  })

  it('提交时把城市与号段一起带给后端', async () => {
    const wrapper = mount(OrderCenterView)
    await flushPromises()
    await openAdminOrder(wrapper)

    await wrapper.findAll('.type-card')[0].trigger('click')
    await flushPromises()
    await wrapper.find('select').setValue('1')
    await wrapper.findAll('.form-step')[1].find('select').setValue('US')
    await flushPromises()
    const cityStep = wrapper.findAll('.form-step')[2]
    await cityStep.findAll('select')[0].setValue('LAX')
    await flushPromises()
    await cityStep.findAll('select')[1].setValue('149.52')
    await flushPromises()

    await wrapper.find('form.admin-order-form').trigger('submit')
    await flushPromises()

    expect(api.createOnBehalfOrder).toHaveBeenCalledTimes(1)
    const payload = api.createOnBehalfOrder.mock.calls[0][0]
    expect(payload.product_id).toBe(11)
    expect(payload.city_code).toBe('LAX')
    expect(payload.ip_segment).toBe('149.52')
    expect(payload.billing_days).toBe(30)
    expect(payload.idempotency_key).toBeTruthy()
  })
})
