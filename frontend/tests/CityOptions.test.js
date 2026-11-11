// @vitest-environment jsdom
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  getCatalogCities: vi.fn(),
  getManagedCities: vi.fn(),
  createManagedCity: vi.fn(),
  updateManagedCity: vi.fn(),
  deleteManagedCity: vi.fn(),
  getManagedCatalogProducts: vi.fn(),
  getManagedRetailPrices: vi.fn(),
  getCatalogListings: vi.fn(),
  updateManagedCatalogProduct: vi.fn(),
  updateManagedRetailPrice: vi.fn(),
  bulkUpdateManagedCatalogProducts: vi.fn(),
  bulkUpdateManagedRetailPrices: vi.fn(),
  updateCatalogListing: vi.fn(),
  bulkUpdateCatalogListings: vi.fn(),
  setManagedProductActive: vi.fn(),
  syncSupplierCitySegments: vi.fn(),
}))

vi.mock('../src/api', () => api)

import CitySegmentPicker from '../src/components/CitySegmentPicker.vue'
import CatalogManagementView from '../src/views/CatalogManagementView.vue'

const optionsPayload = {
  supported: true,
  auto_label: '自动分配（不指定城市）',
  country_name: '美国',
  items: [
    { city_code: 'LAX', city_name: '洛杉矶', segments: ['23.94.5', '23.94.6'] },
    { city_code: 'NYC', city_name: '纽约', segments: [] },
  ],
}

describe('CitySegmentPicker', () => {
  beforeEach(() => {
    document.body.innerHTML = ''
    Object.values(api).forEach((fn) => fn.mockReset())
  })

  it('后台没配城市时整个组件不出现，也不打扰客户', async () => {
    api.getCatalogCities.mockResolvedValue({ data: { supported: false, items: [], auto_label: '自动分配' } })
    const wrapper = mount(CitySegmentPicker, { props: { productId: 7 } })
    await flushPromises()

    expect(api.getCatalogCities).toHaveBeenCalledWith(7)
    expect(wrapper.find('.city-picker').exists()).toBe(false)
    expect(wrapper.emitted('ready')[0][0].supported).toBe(false)
    wrapper.unmount()
  })

  it('城市按「国家 — 城市」卡片列出，选中后再出现号段', async () => {
    api.getCatalogCities.mockResolvedValue({ data: optionsPayload })
    const wrapper = mount(CitySegmentPicker, { props: { productId: 7, modelValue: { cityCode: 'LAX', ipSegment: '' } } })
    await flushPromises()

    const cards = wrapper.findAll('.city-card')
    // 自动分配 + 两个城市
    expect(cards).toHaveLength(3)
    expect(cards[1].text()).toContain('美国 — 洛杉矶')
    expect(cards[1].text()).toContain('LAX')

    const chips = wrapper.findAll('.segment-list button')
    expect(chips).toHaveLength(3) // 不限 + 两个号段

    await chips[2].trigger('click')
    expect(wrapper.emitted('update:modelValue').at(-1)).toEqual([{ cityCode: 'LAX', ipSegment: '23.94.6' }])
    wrapper.unmount()
  })

  it('号段可以按前缀筛选，计数跟着变', async () => {
    api.getCatalogCities.mockResolvedValue({
      data: { ...optionsPayload, items: [{ city_code: 'LAX', city_name: '洛杉矶', segments: ['23.94.5', '23.94.6', '104.28.1'] }] },
    })
    const wrapper = mount(CitySegmentPicker, { props: { productId: 7, modelValue: { cityCode: 'LAX', ipSegment: '' } } })
    await flushPromises()

    expect(wrapper.findAll('.segment-list button')).toHaveLength(4)
    await wrapper.find('.segment-search input').setValue('23.94')
    expect(wrapper.findAll('.segment-list button')).toHaveLength(3) // 不限 + 两个匹配
    expect(wrapper.find('.segment-count').text()).toContain('2 / 3')

    await wrapper.find('.segment-search input').setValue('不存在的段')
    expect(wrapper.find('.segment-empty').exists()).toBe(true)
    wrapper.unmount()
  })

  it('筛选把已选号段过滤掉时仍然显示，避免用户以为选择丢了', async () => {
    api.getCatalogCities.mockResolvedValue({
      data: { ...optionsPayload, items: [{ city_code: 'LAX', city_name: '洛杉矶', segments: ['23.94.5', '104.28.1'] }] },
    })
    const wrapper = mount(CitySegmentPicker, { props: { productId: 7, modelValue: { cityCode: 'LAX', ipSegment: '23.94.5' } } })
    await flushPromises()

    await wrapper.find('.segment-search input').setValue('104')
    expect(wrapper.find('.segment-list button.pinned').text()).toContain('23.94.5')
    wrapper.unmount()
  })

  it('换城市会清掉旧号段，避免提交不属于新城市的段', async () => {
    api.getCatalogCities.mockResolvedValue({ data: optionsPayload })
    const wrapper = mount(CitySegmentPicker, { props: { productId: 7, modelValue: { cityCode: 'LAX', ipSegment: '23.94.5' } } })
    await flushPromises()

    const nyc = wrapper.findAll('.city-card').find((item) => item.text().includes('纽约'))
    await nyc.trigger('click')
    expect(wrapper.emitted('update:modelValue').at(-1)).toEqual([{ cityCode: 'NYC', ipSegment: '' }])
    wrapper.unmount()
  })

  it('商品切换后旧城市不再可售就自动清空', async () => {
    api.getCatalogCities.mockResolvedValue({ data: { ...optionsPayload, items: [optionsPayload.items[1]] } })
    const wrapper = mount(CitySegmentPicker, { props: { productId: 9, modelValue: { cityCode: 'LAX', ipSegment: '1.1.1' } } })
    await flushPromises()

    expect(wrapper.emitted('update:modelValue').at(-1)).toEqual([{ cityCode: '', ipSegment: '' }])
    wrapper.unmount()
  })
})

describe('城市与号段管理页签', () => {
  beforeEach(() => {
    document.body.innerHTML = ''
    Object.values(api).forEach((fn) => fn.mockReset())
    api.getManagedCatalogProducts.mockResolvedValue({ data: { items: [], total: 0 } })
    api.getManagedCities.mockResolvedValue({
      data: {
        items: [
          { id: 3, country_code: 'US', country_name: '美国', category_label: '静态住宅原生IP', resource_category: 'static_residential', city_code: 'LAX', city_name: '洛杉矶', segments: ['1.1.1'], is_enabled: true, sort_order: 10 },
        ],
        total: 1,
        categories: [{ code: 'static_residential', label: '静态住宅原生IP', active: true }],
        countries: [{ code: 'US', name: '美国' }],
      },
    })
    api.createManagedCity.mockResolvedValue({ data: { id: 4 } })
    api.updateManagedCity.mockResolvedValue({ data: {} })
    api.deleteManagedCity.mockResolvedValue({ data: {} })
    api.syncSupplierCitySegments.mockResolvedValue({ data: { cities_seen: 2, segments_saved: 3, errors: [] } })
  })

  async function openCityTab() {
    const wrapper = mount(CatalogManagementView, { attachTo: document.body })
    await flushPromises()
    const tabButton = wrapper.findAll('.catalog-tabs button').find((item) => item.text().includes('城市与号段'))
    await tabButton.trigger('click')
    await flushPromises()
    return wrapper
  }

  it('切到城市页签会拉取清单并渲染城市码', async () => {
    const wrapper = await openCityTab()
    expect(api.getManagedCities).toHaveBeenCalled()
    expect(wrapper.find('.city-table').text()).toContain('LAX')
    wrapper.unmount()
  })

  it('停用城市走行内开关，不需要二次确认', async () => {
    const wrapper = await openCityTab()
    const toggle = wrapper.find('.city-table .product-active')
    await toggle.trigger('click')
    await flushPromises()
    expect(api.updateManagedCity).toHaveBeenCalledWith(3, { is_enabled: false })
    wrapper.unmount()
  })

  it('从资源池1同步城市号段：调接口并刷新清单', async () => {
    const wrapper = await openCityTab()
    const syncButton = wrapper.findAll('.catalog-filters button').find((item) => item.text().includes('同步城市'))
    expect(syncButton).toBeTruthy()
    await syncButton.trigger('click')
    await flushPromises()

    expect(api.syncSupplierCitySegments).toHaveBeenCalledWith({ product_type_id: 3, country_code: undefined })
    expect(api.getManagedCities).toHaveBeenCalledTimes(2) // 初次加载 + 同步后刷新
    wrapper.unmount()
  })

  it('新增城市把表单原样提交给后端', async () => {
    const wrapper = await openCityTab()
    const inputs = wrapper.findAll('.city-create-grid input')
    // 城市码、城市名、号段、排序
    await inputs[0].setValue('SJC')
    await inputs[1].setValue('圣何塞')
    await inputs[2].setValue('3.3.3, 3.3.4')
    const addButton = wrapper.findAll('.city-create-grid button').find((item) => item.text().includes('新增城市'))
    await addButton.trigger('click')
    await flushPromises()

    expect(api.createManagedCity).toHaveBeenCalledWith({
      resource_category: 'static_residential',
      country_code: 'US',
      city_code: 'SJC',
      city_name: '圣何塞',
      ip_segments: '3.3.3, 3.3.4',
      sort_order: 100,
    })
    wrapper.unmount()
  })
})
