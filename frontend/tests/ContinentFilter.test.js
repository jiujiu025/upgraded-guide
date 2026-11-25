// @vitest-environment jsdom
// 购买页大洲筛选：30+ 国家平铺太长，按大洲分栏并显示数量。
// 锁住三件事：
// 1) 默认"全部"，数量徽标等于该洲在当前产品下的可选国家数；
// 2) 点某个洲只显示该洲国家；
// 3) 一输入关键词就跨全部大洲搜索——客户记得"越南"，但未必知道它在亚洲。
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

const offer = (id) => [{
  product_id: id, billing_days: 30, unit_price: null, in_stock: true, spec: null,
}]

const regions = [
  { id: 1, name: '美国', country_code: 'US', continent: 'north_america', offers: offer(101), min_price: null, in_stock: true },
  { id: 2, name: '加拿大', country_code: 'CA', continent: 'north_america', offers: offer(102), min_price: null, in_stock: true },
  { id: 3, name: '墨西哥', country_code: 'MX', continent: 'north_america', offers: offer(103), min_price: null, in_stock: true },
  { id: 4, name: '日本', country_code: 'JP', continent: 'asia', offers: offer(104), min_price: null, in_stock: true },
  { id: 5, name: '越南', country_code: 'VN', continent: 'asia', offers: offer(105), min_price: null, in_stock: true },
  { id: 6, name: '新加坡', country_code: 'SG', continent: 'asia', offers: offer(106), min_price: null, in_stock: true },
  { id: 7, name: '英国', country_code: 'GB', continent: 'europe', offers: offer(107), min_price: null, in_stock: true },
  { id: 8, name: '德国', country_code: 'DE', continent: 'europe', offers: offer(108), min_price: null, in_stock: true },
  { id: 9, name: '沙特阿拉伯', country_code: 'SA', continent: 'middle_east', offers: offer(109), min_price: null, in_stock: true },
  { id: 10, name: '巴西', country_code: 'BR', continent: 'south_america', offers: offer(110), min_price: null, in_stock: true },
  { id: 11, name: '澳大利亚', country_code: 'AU', continent: 'oceania', offers: offer(111), min_price: null, in_stock: true },
  { id: 12, name: '南非', country_code: 'ZA', continent: 'africa', offers: offer(112), min_price: null, in_stock: true },
]

const catalog = {
  price_visible: false,
  lines: [
    {
      code: 'residential', name: '静态住宅IP', min_price: null, periods: [30], region_count: regions.length,
      in_stock: true, regions,
    },
    {
      code: 'live', name: '直播专线', min_price: null, periods: [30], region_count: 1, in_stock: true,
      regions: [{ id: 1, name: '美国', country_code: 'US', continent: 'north_america',
        offers: [{ product_id: 201, billing_days: 30, unit_price: null, in_stock: true, spec: '手机' }],
        min_price: null, in_stock: true }],
    },
  ],
}

beforeEach(() => {
  vi.clearAllMocks()
  api.getPublicCatalog.mockResolvedValue({ data: catalog })
  api.getCatalogCities.mockResolvedValue({ data: { supported: false, items: [], auto_label: '自动分配' } })
  sessionModule.session.user = null
})

async function mountFlow() {
  const wrapper = mount(BuyFlow)
  await flushPromises()
  return wrapper
}

function tab(wrapper, label) {
  return wrapper.findAll('.continent-tabs button').find((item) => item.text().startsWith(label))
}

function regionNames(wrapper) {
  return wrapper.findAll('.region-picker button').map((item) => item.text())
}

describe('购买页大洲筛选', () => {
  it('默认全部，徽标数量按洲统计', async () => {
    const wrapper = await mountFlow()

    expect(tab(wrapper, '全部').text()).toContain('12')
    expect(tab(wrapper, '北美洲').text()).toContain('3')
    expect(tab(wrapper, '亚洲').text()).toContain('3')
    expect(tab(wrapper, '中东').text()).toContain('1')
    expect(tab(wrapper, '全部').attributes('aria-selected')).toBe('true')
    expect(regionNames(wrapper)).toHaveLength(12)
  })

  it('点某个洲只显示该洲国家', async () => {
    const wrapper = await mountFlow()

    await tab(wrapper, '亚洲').trigger('click')
    await flushPromises()

    const names = regionNames(wrapper).join(' ')
    expect(names).toContain('日本')
    expect(names).toContain('越南')
    expect(names).toContain('新加坡')
    expect(names).not.toContain('美国')
    expect(tab(wrapper, '亚洲').attributes('aria-selected')).toBe('true')
  })

  it('输入关键词自动跳回全部并跨洲搜索', async () => {
    const wrapper = await mountFlow()

    await tab(wrapper, '欧洲').trigger('click')
    await flushPromises()
    expect(regionNames(wrapper).join(' ')).toContain('英国')

    await wrapper.find('.search-box input').setValue('越南')
    await flushPromises()

    expect(tab(wrapper, '全部').attributes('aria-selected')).toBe('true')
    expect(regionNames(wrapper).join(' ')).toContain('越南')
  })

  it('切换产品后回到全部，不停在上一个产品的洲上', async () => {
    const wrapper = await mountFlow()

    await tab(wrapper, '非洲').trigger('click')
    await flushPromises()

    // 手机直播只有 1 个国家：筛选条本身应当消失，不留一条只有一个选项的栏
    await wrapper.findAll('.type-picker button')[2].trigger('click')
    await flushPromises()
    expect(wrapper.find('.continent-tabs').exists()).toBe(false)
    expect(regionNames(wrapper).join(' ')).toContain('美国')

    // 切回住宅：国家多了，栏回来，且选中态是「全部」而不是刚才的非洲
    await wrapper.findAll('.type-picker button')[0].trigger('click')
    await flushPromises()
    expect(tab(wrapper, '全部').attributes('aria-selected')).toBe('true')
  })

  it('切换大洲后已选国家不在该洲时，自动落到该洲第一个', async () => {
    const wrapper = await mountFlow()
    expect(wrapper.find('.order-summary').text()).toContain('美国')

    await tab(wrapper, '亚洲').trigger('click')
    await flushPromises()

    const summary = wrapper.find('.order-summary').text()
    expect(summary).toContain('日本')
    expect(summary).not.toContain('美国')
  })

  it('搜索无结果时给出可操作的提示', async () => {
    const wrapper = await mountFlow()

    await wrapper.find('.search-box input').setValue('火星')
    await flushPromises()

    expect(wrapper.text()).toContain('没有找到「火星」')
  })
})
