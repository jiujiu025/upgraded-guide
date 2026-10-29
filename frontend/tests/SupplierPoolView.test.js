// @vitest-environment jsdom
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  getAdminCatalogProducts: vi.fn(), getAdminCatalogRegions: vi.fn(), getSupplierInventory: vi.fn(),
  getSupplierOverview: vi.fn(), getSupplierProducts: vi.fn(), getSupplierQuotes: vi.fn(), getSupplierIpSegments: vi.fn(),
  getSupplierRegions: vi.fn(), getSupplierRules: vi.fn(), getSupplierSyncRuns: vi.fn(),
  getCatalogPublications: vi.fn(), publishCatalogSnapshot: vi.fn(), withdrawCatalogPublication: vi.fn(),
  mapSupplierProduct: vi.fn(), mapSupplierRegion: vi.fn(), saveSupplierRule: vi.fn(),
  syncSupplierCities: vi.fn(), syncSupplierCountries: vi.fn(), syncSupplierInventory: vi.fn(),
  syncSupplierProducts: vi.fn(), syncSupplierQuotes: vi.fn(),
}))
const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn() }))

vi.mock('../src/api', () => api)
vi.mock('vue-router', () => ({
  useRoute: () => ({ path: '/admin/resource-pool' }),
  useRouter: () => router,
}))

import SupplierPoolView from '../src/views/SupplierPoolView.vue'

const overview = {
  supplier: {
    id: 1, code: 'quanqiudaili', name: '全球代理', status: 'active',
    api_key_configured: false, auto_purchase_enabled: false, last_sync_at: null,
    last_sync_status: 'not_synced', last_error_code: null, last_error_message: null,
  },
  pool: { id: 1, code: 'global_proxy_pool', name: '资源池 1', status: 'active', strategy: 'priority_manual_fallback' },
  states: { products: 'not_synced', regions: 'not_synced', inventory: 'not_synced', quotes: 'not_synced' },
  counts: { products: 0, regions: 0, inventory_snapshots: 0, quote_snapshots: 0, enabled_manual_rules: 0 },
}

function response(data) { return Promise.resolve({ data }) }
function setLoadData(overrides = {}) {
  api.getSupplierOverview.mockImplementation(() => response(overrides.overview || overview))
  api.getSupplierProducts.mockImplementation(() => response(overrides.products || []))
  api.getSupplierRegions.mockImplementation(() => response(overrides.regions || []))
  api.getSupplierInventory.mockImplementation(() => response(overrides.inventory || []))
  api.getSupplierIpSegments.mockImplementation(() => response(overrides.ipSegments || []))
  api.getSupplierQuotes.mockImplementation(() => response(overrides.quotes || []))
  api.getSupplierRules.mockImplementation(() => response(overrides.rules || []))
  api.getSupplierSyncRuns.mockImplementation(() => response(overrides.runs || []))
  api.getAdminCatalogProducts.mockImplementation(() => response(overrides.localProducts || []))
  api.getAdminCatalogRegions.mockImplementation(() => response(overrides.localRegions || []))
  api.getCatalogPublications.mockImplementation(() => response(overrides.publications || []))
}

beforeEach(() => {
  vi.clearAllMocks()
  window.localStorage.clear()
  window.localStorage.setItem('ops_user', JSON.stringify({ role: 'admin', display_name: '管理员' }))
  setLoadData()
  for (const name of ['mapSupplierProduct', 'mapSupplierRegion', 'saveSupplierRule', 'syncSupplierCities', 'syncSupplierCountries', 'syncSupplierInventory', 'syncSupplierProducts', 'syncSupplierQuotes']) {
    api[name].mockResolvedValue({ data: { status: 'success' } })
  }
  api.publishCatalogSnapshot.mockResolvedValue({ data: { id: 1 } })
  api.withdrawCatalogPublication.mockResolvedValue({ data: { id: 1 } })
})

describe('管理员资源池工作台', () => {
  it('缺少 API Key 时明确提示且不生成演示数据', async () => {
    const wrapper = mount(SupplierPoolView)
    await flushPromises()

    expect(wrapper.text()).toContain('供应商 API Key 未配置')
    expect(wrapper.text()).toContain('页面不会生成演示数据')
    expect(wrapper.text()).toContain('未同步')
    expect(wrapper.findAll('button').find((button) => button.text().includes('同步目录与报价')).attributes('disabled')).toBeDefined()
  })

  it('展示真实成本快照和过期状态，不把它标成实时可售', async () => {
    setLoadData({
      overview: { ...overview, supplier: { ...overview.supplier, api_key_configured: true }, states: { products: 'fresh', regions: 'fresh', inventory: 'stale', quotes: 'stale' } },
      inventory: [{ id: 1, country_code: 'US', city_name: 'Los Angeles', availability: 'available', data_state: 'stale', is_realtime_available: false, checked_at: '2026-10-20T10:00:00' }],
      quotes: [{ id: 1, external_product_ref: '2', country_code: 'US', duration_days: 30, unit_cost: '15.0000', total_cost: null, currency: 'CNY', data_state: 'stale', quoted_at: '2026-10-20T10:00:00' }],
    })
    const wrapper = mount(SupplierPoolView)
    await flushPromises()
    await wrapper.findAll('.supplier-tabs button')[2].trigger('click')

    expect(wrapper.text()).toContain('¥15.0000 CNY')
    expect(wrapper.text()).toContain('数据过期')
    expect(wrapper.text()).toContain('不可用')
    expect(wrapper.text()).toContain('官网售价继续由玖玖IP商品目录控制')
  })

  it('目录同步存在单项失败时反馈部分失败并保留重试入口', async () => {
    setLoadData({ overview: { ...overview, supplier: { ...overview.supplier, api_key_configured: true } } })
    api.syncSupplierCities.mockRejectedValue({ userMessage: '城市查询失败' })
    const wrapper = mount(SupplierPoolView)
    await flushPromises()
    await wrapper.findAll('button').find((button) => button.text().includes('同步目录与报价')).trigger('click')
    await vi.waitFor(() => expect(api.syncSupplierQuotes).toHaveBeenCalled())
    await flushPromises()

    expect(wrapper.text()).toContain('部分同步完成')
    expect(api.syncSupplierProducts).toHaveBeenCalledTimes(1)
    expect(api.syncSupplierCountries).toHaveBeenCalledTimes(1)
    expect(api.syncSupplierCities).toHaveBeenCalledTimes(1)
  })

  it('保存商品映射时只提交内部商品 ID', async () => {
    setLoadData({
      overview: { ...overview, supplier: { ...overview.supplier, api_key_configured: true }, counts: { ...overview.counts, products: 1 } },
      products: [{ id: 8, external_product_ref: '2', name_snapshot: '静态住宅', currency: 'CNY', local_product_id: null, data_state: 'fresh', last_synced_at: '2026-10-27T10:00:00' }],
      localProducts: [{ id: 7, name: '玖玖IP美国线路', region_id: 3 }],
    })
    const wrapper = mount(SupplierPoolView)
    await flushPromises()
    await wrapper.findAll('.supplier-tabs button')[1].trigger('click')
    await wrapper.find('.product-mapping-table select').setValue('7')
    await flushPromises()

    expect(api.mapSupplierProduct).toHaveBeenCalledWith(8, 7, undefined)
  })

  it('供应商失败时告警并禁用发布，但仍可撤回已发布版本', async () => {
    setLoadData({
      overview: { ...overview, states: { products: 'fresh', regions: 'fresh', inventory: 'query_failed', quotes: 'fresh' } },
      publications: [{ id: 4, product_id: 7, region_id: 3, version: 1, inventory_snapshot_id: 9, quote_snapshot_id: 8, before_values: { price: 199 }, after_values: { price: 229 }, withdrawn_at: null }],
    })
    const wrapper = mount(SupplierPoolView)
    await flushPromises()
    expect(wrapper.text()).toContain('已发布商品保持现状')
    await wrapper.findAll('.supplier-tabs button')[3].trigger('click')
    expect(wrapper.find('.publish-fields button').attributes('disabled')).toBeDefined()
    await wrapper.findAll('button').find((button) => button.text().includes('撤回')).trigger('click')
    expect(api.withdrawCatalogPublication).toHaveBeenCalledWith(4)
  })
})
