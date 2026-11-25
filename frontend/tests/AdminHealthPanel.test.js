// @vitest-environment jsdom
// 管理员线路健康：列表必须按「客户线路」而不是「节点」来展示。
// 同一台出口卖给多个客户时，从 Node 出发只会剩一行，看不出这条线是谁的、哪个销售在跟。
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  getHealthOverview: vi.fn(),
  getAdminCustomerLines: vi.fn(),
  checkAdminCustomerLines: vi.fn(),
  getTransitInbounds: vi.fn(),
  checkTransitInbounds: vi.fn(),
}))
vi.mock('../src/api', () => api)

import AdminHealthPanel from '../src/views/AdminHealthPanel.vue'

const lineRow = {
  line_id: 11, customer_id: 3, customer_name: '深圳乙公司', sales_id: 2, sales_name: '销售乙',
  order_no: 'ORD-B-1', product_name: '越南 社媒运营专线 30天', line_code: 'social',
  line_label: '社媒运营专线', region_name: '越南', country_code: 'VN',
  entry: '10.0.0.2:52000', exit_ip: '5.6.7.8', protocol: 'vless',
  vps_name: 'HK-01', inbound_port: 52000, outbound_tag: 'socks-ORD-B-1',
  health_status: 'offline', health_latency_ms: null, health_checked_at: null,
  line_status: 'active', expires_at: '2026-12-26T00:00:00',
}

const linesPayload = {
  items: [lineRow],
  total: 1,
  page: 1,
  page_size: 25,
  counts: { healthy: 4, offline: 1, unknown: 2 },
  options: {
    sales: [{ id: 2, name: '销售乙' }],
    lines: [{ code: 'residential', label: '静态住宅IP' }, { code: 'social', label: '社媒运营专线' }],
    countries: ['US', 'VN'],
  },
}

const overviewPayload = {
  lines: { total: 7, healthy: 4, offline: 1, unknown: 2 },
  average_latency_ms: 42, customers: 5, sales: 2,
  panels: { total: 2, enabled: 1 }, inbounds: { total: 6, configured: 5 },
}

beforeEach(() => {
  vi.clearAllMocks()
  api.getHealthOverview.mockResolvedValue({ data: overviewPayload })
  api.getAdminCustomerLines.mockResolvedValue({ data: linesPayload })
  api.checkAdminCustomerLines.mockResolvedValue({ data: { checked: 1, healthy: 1, offline: 0, nodes: [{ node_id: 9, server: '10.0.0.2', port: 52000, status: 'healthy', latency_ms: 18 }] } })
  api.getTransitInbounds.mockResolvedValue({
    data: {
      items: [
        {
          vps_id: 1, vps_name: 'HK-01', group_code: 'hk', connect_host: '9.9.9.9',
          inbound_id: 7, port: 52000, remark: '越南-5.6.7.8', protocol: 'vless', enable: true,
          outbound_tag: 'socks-ORD-B-1', exit_host: '5.6.7.8', exit_port: 1080,
          matched: { line_id: 11, customer_name: '深圳乙公司', sales_name: '销售乙', status: 'configured' },
        },
        {
          vps_id: 1, vps_name: 'HK-01', group_code: 'hk', connect_host: '9.9.9.9',
          inbound_id: 8, port: 52001, remark: '手工建的', protocol: 'vless', enable: true,
          outbound_tag: null, exit_host: null, exit_port: null, matched: null,
        },
      ],
      panels: [{ id: 1, name: 'HK-01', group_code: 'hk', connect_host: '9.9.9.9', is_enabled: true }],
      errors: [],
    },
  })
})

async function mountPanel() {
  const wrapper = mount(AdminHealthPanel)
  await flushPromises()
  return wrapper
}

describe('AdminHealthPanel', () => {
  it('按客户线路展示，带销售与入口→出口', async () => {
    const wrapper = await mountPanel()

    const text = wrapper.text()
    expect(text).toContain('深圳乙公司')
    expect(text).toContain('销售乙')
    expect(text).toContain('10.0.0.2:52000')
    expect(text).toContain('5.6.7.8')
    expect(text).toContain('HK-01 · 52000')
    expect(text).toContain('socks-ORD-B-1')
    expect(text).toContain('离线')
  })

  it('统计按客户线路口径，不按节点数', async () => {
    const wrapper = await mountPanel()
    const cards = wrapper.findAll('.hl-cards article')
    expect(cards[0].text()).toContain('7')
    expect(cards[0].text()).toContain('5 个客户')
    expect(cards[2].text()).toContain('1')
  })

  it('按销售筛选会带 sales_id 重新请求', async () => {
    const wrapper = await mountPanel()
    const select = wrapper.findAll('.hl-filters select')[0]
    await select.setValue('2')
    await flushPromises()

    const lastCall = api.getAdminCustomerLines.mock.calls.at(-1)[0]
    expect(lastCall.sales_id).toBe('2')
    expect(lastCall.page).toBe(1)
  })

  it('关键词搜索与状态筛选走服务端参数', async () => {
    const wrapper = await mountPanel()
    await wrapper.find('.hl-filters .wide input').setValue('ORD-B')
    await wrapper.find('.hl-filters .wide input').trigger('keyup.enter')
    await flushPromises()
    expect(api.getAdminCustomerLines.mock.calls.at(-1)[0].keyword).toBe('ORD-B')

    const chips = wrapper.findAll('.hl-chip')
    await chips[1].trigger('click')
    await flushPromises()
    expect(api.getAdminCustomerLines.mock.calls.at(-1)[0].health).toBe('offline')
  })

  it('单条检测只测这一条线路', async () => {
    const wrapper = await mountPanel()
    await wrapper.find('.hl-mini').trigger('click')
    await flushPromises()

    expect(api.checkAdminCustomerLines).toHaveBeenCalledWith({ line_ids: [11] })
    expect(wrapper.text()).toContain('连通')
  })

  it('面板入站列出未关联客户的入站', async () => {
    const wrapper = await mountPanel()
    const tabs = wrapper.findAll('.hl-tabs button')
    await tabs[1].trigger('click')
    await flushPromises()

    expect(api.getTransitInbounds).toHaveBeenCalled()
    const text = wrapper.text()
    expect(text).toContain('越南-5.6.7.8')
    expect(text).toContain('深圳乙公司')
    expect(text).toContain('手工建的')
    expect(text).toContain('未关联客户')
  })
})
