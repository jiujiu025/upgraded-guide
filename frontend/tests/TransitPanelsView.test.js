// @vitest-environment jsdom
// 中转面板页：添加机器、检测连通、查看待配中转并重试。
// 锁住三件事：
// 1) 检测结果分成功/失败两条文案，运营能一眼看出能不能接单；
// 2) 新增必须带走 reality 密钥与端口段，否则面板建不出入站；
// 3) 待配中转的重试打的是订单接口，参数是 order_id——不是中转记录 id。
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  getTransitSummary: vi.fn(),
  getTransitPanels: vi.fn(),
  createTransitPanel: vi.fn(),
  updateTransitPanel: vi.fn(),
  testTransitPanel: vi.fn(),
  getTransitRoutes: vi.fn(),
  saveTransitRoute: vi.fn(),
  getTransitProvisions: vi.fn(),
  retryTransitOrder: vi.fn(),
  retryAllTransit: vi.fn(),
}))
vi.mock('../src/api', () => api)

import TransitPanelsView from '../src/views/TransitPanelsView.vue'

const panels = [
  {
    id: 1, name: 'US-01', group_code: 'us', base_url: 'https://panel.example.com/abc',
    username: 'admin', connect_host: '5.6.7.8', sni: 'yahoo.com', dest: 'yahoo.com:443',
    fingerprint: 'random', port_start: 52000, port_end: 52999, max_inbounds: 200,
    is_enabled: true, note: null, has_password: true, has_api_token: false,
    reality_public_key: 'pub-key',
  },
]

beforeEach(() => {
  vi.clearAllMocks()
  // 停用前有确认弹窗；jsdom 里默认没有 window.confirm，需要显式给一个
  window.confirm = vi.fn(() => true)
  window.confirm = vi.fn(() => true)
  api.getTransitPanels.mockResolvedValue({ data: panels })
  api.getTransitSummary.mockResolvedValue({
    data: { auto_procurement_enabled: true, panels: 1, enabled_panels: 1, pending_transit_orders: 0 },
  })
  api.getTransitProvisions.mockResolvedValue({
    data: [{
      id: 5, order_id: 42, order_no: 'ORD-TR-1', line_index: 1, vps_name: 'US-01',
      status: 'inbound_created', inbound_port: 52001, exit_ip: '1.2.3.4',
      remark: '美国-1.2.3.4', error_code: 'XUI_SAVE_TEMPLATE', error_message: '面板返回 500',
    }],
  })
  api.retryTransitOrder.mockResolvedValue({ data: { status: 'delivered', line_ids: [9] } })
})

function button(wrapper, label) {
  return wrapper.findAll('button').find((item) => item.text().includes(label))
}

describe('TransitPanelsView', () => {
  it('列出面板并显示凭据类型', async () => {
    const wrapper = mount(TransitPanelsView)
    await flushPromises()

    expect(api.getTransitPanels).toHaveBeenCalled()
    expect(wrapper.text()).toContain('US-01')
    expect(wrapper.text()).toContain('us')
    expect(wrapper.text()).toContain('账号密码')
    expect(wrapper.text()).toContain('52000–52999')
  })

  it('自动开通关闭时提示订单不会走面板', async () => {
    api.getTransitSummary.mockResolvedValue({
      data: { auto_procurement_enabled: false, panels: 1, enabled_panels: 1, pending_transit_orders: 2 },
    })
    const wrapper = mount(TransitPanelsView)
    await flushPromises()

    expect(wrapper.text()).toContain('自动开通当前关闭')
    expect(wrapper.text()).toContain('不会走面板')
    expect(wrapper.text()).toContain('还有 2 张订单待配中转')
  })

  it('检测成功时提示入站与端口', async () => {
    api.testTransitPanel.mockResolvedValue({
      data: { ok: true, inbound_count: 3, socks_outbounds: 2, routing_rules: 2, next_free_port: 52003 },
    })
    const wrapper = mount(TransitPanelsView)
    await flushPromises()

    await button(wrapper, '检测').trigger('click')
    await flushPromises()

    expect(api.testTransitPanel).toHaveBeenCalledWith(1)
    expect(wrapper.text()).toContain('连接成功')
    expect(wrapper.text()).toContain('下一个可用端口 52003')
  })

  it('检测失败时显示后端原因', async () => {
    api.testTransitPanel.mockRejectedValue({ userMessage: 'XUI_AUTH_FAILED: 面板登录失败' })
    const wrapper = mount(TransitPanelsView)
    await flushPromises()

    await button(wrapper, '检测').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('连接失败')
    expect(wrapper.text()).toContain('面板登录失败')
  })

  it('添加面板时提交 reality 密钥与端口段', async () => {
    api.createTransitPanel.mockResolvedValue({ data: { id: 2 } })
    const wrapper = mount(TransitPanelsView)
    await flushPromises()

    await button(wrapper, '添加面板').trigger('click')
    const inputs = wrapper.findAll('.tp-form input')
    const set = async (index, value) => {
      await inputs[index].setValue(value)
    }
    // 顺序与模板一致：名称、登录地址、账号、密码、Token、连接地址、私钥、公钥、shortId…
    await set(0, 'HK-01')
    await set(1, 'https://panel.example.com/hk')
    await set(2, 'admin')
    await set(3, 'panel-pass')
    await set(5, '9.9.9.9')
    await set(6, 'priv-key-material')
    await set(7, 'pub-key-material')
    await set(8, 'd816')

    await button(wrapper, '保存').trigger('click')
    await flushPromises()

    expect(api.createTransitPanel).toHaveBeenCalledTimes(1)
    const payload = api.createTransitPanel.mock.calls[0][0]
    expect(payload.name).toBe('HK-01')
    expect(payload.password).toBe('panel-pass')
    expect(payload.reality_private_key).toBe('priv-key-material')
    expect(payload.reality_public_key).toBe('pub-key-material')
    expect(payload.reality_short_id).toBe('d816')
    expect(payload.port_start).toBe(52000)
    expect(payload.port_end).toBe(52999)
  })

  it('编辑时不能改名，提交里也不带 name', async () => {
    api.updateTransitPanel.mockResolvedValue({ data: {} })
    const wrapper = mount(TransitPanelsView)
    await flushPromises()

    await button(wrapper, '编辑').trigger('click')
    await flushPromises()

    // 名称框在编辑态是只读的，避免用户改了却不生效
    const nameInput = wrapper.findAll('.tp-form input')[0]
    expect(nameInput.attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('名称是唯一标识')

    await button(wrapper, '保存').trigger('click')
    await flushPromises()

    expect(api.updateTransitPanel).toHaveBeenCalledTimes(1)
    const [id, payload] = api.updateTransitPanel.mock.calls[0]
    expect(id).toBe(1)
    // 后端的编辑接口没有 name 字段，多传会被 extra="forbid" 拒掉
    expect('name' in payload).toBe(false)
    expect(payload.is_enabled).toBe(true)
  })

  it('停用前要确认，取消就不发请求', async () => {
    window.confirm = vi.fn(() => false)
    const wrapper = mount(TransitPanelsView)
    await flushPromises()

    // 启用/停用已经改成开关：文案是"已启用"，点一下变"已停用"
    await wrapper.find('.tp-toggle.on').trigger('click')
    await flushPromises()

    expect(window.confirm).toHaveBeenCalled()
    expect(api.updateTransitPanel).not.toHaveBeenCalled()
  })

  it('停用后提示同组已无可用面板', async () => {
    api.updateTransitPanel.mockResolvedValue({ data: {} })
    const wrapper = mount(TransitPanelsView)
    await flushPromises()

    // 启用/停用已经改成开关：文案是"已启用"，点一下变"已停用"
    await wrapper.find('.tp-toggle.on').trigger('click')
    await flushPromises()

    expect(api.updateTransitPanel).toHaveBeenCalledWith(1, { is_enabled: false })
    expect(wrapper.text()).toContain('us 组暂无可用面板')
  })

  it('没有启用中的面板时，直接说明「待中转」的原因', async () => {
    api.getTransitPanels.mockResolvedValue({ data: [{ ...panels[0], is_enabled: false }] })
    api.getTransitSummary.mockResolvedValue({
      data: { auto_procurement_enabled: true, panels: 1, enabled_panels: 0, pending_transit_orders: 3 },
    })
    const wrapper = mount(TransitPanelsView)
    await flushPromises()

    expect(wrapper.text()).toContain('所有中转面板都已停用')
    expect(wrapper.text()).toContain('停在「待配中转」')
  })

  it('一台面板都没录时提示先添加并检测', async () => {
    api.getTransitPanels.mockResolvedValue({ data: [] })
    api.getTransitSummary.mockResolvedValue({
      data: { auto_procurement_enabled: true, panels: 0, enabled_panels: 0, pending_transit_orders: 1 },
    })
    const wrapper = mount(TransitPanelsView)
    await flushPromises()

    expect(wrapper.text()).toContain('还没有录入任何中转面板')
  })

  it('待配中转可以批量重试', async () => {
    api.retryAllTransit.mockResolvedValue({
      data: { attempted: 2, succeeded: 2, failed: 0, results: [] },
    })
    const wrapper = mount(TransitPanelsView)
    await flushPromises()

    await button(wrapper, '待配中转').trigger('click')
    await flushPromises()
    await button(wrapper, '重试全部').trigger('click')
    await flushPromises()

    expect(api.retryAllTransit).toHaveBeenCalledWith(5)
    expect(wrapper.text()).toContain('批量重试：成功 2 单')
  })

  it('待配中转按订单 id 重试', async () => {
    const wrapper = mount(TransitPanelsView)
    await flushPromises()

    await button(wrapper, '待配中转').trigger('click')
    await flushPromises()

    expect(api.getTransitProvisions).toHaveBeenCalled()
    expect(wrapper.text()).toContain('ORD-TR-1')
    expect(wrapper.text()).toContain('入站已建，待出站')

    await button(wrapper, '重试配置').trigger('click')
    await flushPromises()

    expect(api.retryTransitOrder).toHaveBeenCalledWith(42)
  })
})
