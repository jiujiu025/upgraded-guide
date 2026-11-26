// @vitest-environment jsdom
// 系统日志页：排障入口。锁住三件事：
// 1) 级别与分类显示中文，不把 transit/procurement 这种内部代号丢给运营；
// 2) 级别筛选走服务端参数（日志量会到几十万条，前端筛不现实）；
// 3) 详情默认折叠，点开才显示堆栈/上游单号。
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ getSystemLogs: vi.fn() }))
vi.mock('../src/api', () => api)

import SystemLogsView from '../src/views/SystemLogsView.vue'

const payload = {
  items: [
    {
      id: 3, level: 'error', category: 'transit', action: 'transit_provision_failed',
      message: '订单 ORD-1 中转未完成：TransitProvisionError',
      detail: '上游单号=UP-1\n面板超时', order_id: 7, line_id: null, vps_id: 2, actor_id: null,
      created_at: '2026-11-26T10:00:00',
    },
    {
      id: 2, level: 'warning', category: 'impersonation', action: 'impersonation_start',
      message: '超级管理员 开始模拟 客户乙', detail: '目标角色=customer',
      order_id: null, line_id: null, vps_id: null, actor_id: 1,
      created_at: '2026-11-26T09:50:00',
    },
    {
      id: 1, level: 'info', category: 'procurement', action: 'procurement_succeeded',
      message: '订单 ORD-2 已采购并交付 1 条线路', detail: '上游单号=UP-2',
      order_id: 8, line_id: null, vps_id: null, actor_id: null,
      created_at: '2026-11-26T09:40:00',
    },
  ],
  total: 3, page: 1, page_size: 25,
  counts: { error: 4, warning: 2, info: 9 },
  options: { levels: ['info', 'warning', 'error'], categories: ['transit', 'impersonation', 'procurement'] },
}

beforeEach(() => {
  vi.clearAllMocks()
  api.getSystemLogs.mockResolvedValue({ data: payload })
})

describe('SystemLogsView', () => {
  it('级别与分类显示中文，不暴露内部代号', async () => {
    const wrapper = mount(SystemLogsView)
    await flushPromises()

    const rows = wrapper.findAll('.sl-row')
    const cells = (row) => row.find('summary').findAll('span')
    // 第二列是级别、第三列是分类，都必须是人话
    expect(cells(rows[0])[1].text()).toBe('错误')
    expect(cells(rows[0])[2].text()).toBe('中转')
    expect(cells(rows[1])[2].text()).toBe('模拟登录')
    expect(cells(rows[2])[2].text()).toBe('采购')
  })

  it('级别 chip 带服务端筛选参数', async () => {
    const wrapper = mount(SystemLogsView)
    await flushPromises()

    const chips = wrapper.findAll('.sl-chip')
    await chips[0].trigger('click')
    await flushPromises()

    const lastParams = api.getSystemLogs.mock.calls.at(-1)[0]
    expect(lastParams.level).toBe('error')
    expect(lastParams.page).toBe(1)
  })

  it('分类与时间筛选也走服务端', async () => {
    const wrapper = mount(SystemLogsView)
    await flushPromises()

    const selects = wrapper.findAll('.sl-filters select')
    await selects[0].setValue('transit')
    await flushPromises()
    expect(api.getSystemLogs.mock.calls.at(-1)[0].category).toBe('transit')

    await selects[1].setValue(30)
    await flushPromises()
    expect(api.getSystemLogs.mock.calls.at(-1)[0].days).toBe(30)
  })

  it('详情默认折叠，展开后显示上游单号与上下文', async () => {
    const wrapper = mount(SystemLogsView)
    await flushPromises()

    const row = wrapper.findAll('.sl-row')[0]
    expect(row.element.open).toBe(false)
    expect(wrapper.text()).toContain('上游单号=UP-1')   // 内容在 DOM 里，但默认不展开

    row.element.open = true
    await flushPromises()
    expect(row.element.open).toBe(true)
    expect(wrapper.text()).toContain('面板超时')
  })

  it('空态给出可操作提示', async () => {
    api.getSystemLogs.mockResolvedValue({
      data: { items: [], total: 0, page: 1, page_size: 25, counts: { error: 0, warning: 0, info: 0 }, options: payload.options },
    })
    const wrapper = mount(SystemLogsView)
    await flushPromises()
    expect(wrapper.text()).toContain('没有符合条件的日志')
  })
})
