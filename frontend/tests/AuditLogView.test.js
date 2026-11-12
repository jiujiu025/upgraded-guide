// @vitest-environment jsdom
// 操作审计页：只读列表 + 过滤 + 分页。锁住两件事：
// 1) 系统动作（没有操作人）显示为「系统」，不能被吞掉；
// 2) 改筛选条件会带着参数重新请求，而不是只在本地过滤。
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ getAuditLogs: vi.fn(), getAuditSummary: vi.fn() }))
vi.mock('../src/api', () => api)

import AuditLogView from '../src/views/AuditLogView.vue'

const rows = [
  {
    id: 3, created_at: '2026-11-12T00:30:00', action: 'deliver', resource_type: 'order',
    resource_id: 7, detail: '线路=12; 进度=1/1', actor: '管理员', actor_username: 'root',
  },
  {
    id: 2, created_at: '2026-11-12T00:20:00', action: 'wallet_order_payment', resource_type: 'order',
    resource_id: 7, detail: '金额分=10900', actor: '系统', actor_username: null,
  },
]

beforeEach(() => {
  vi.clearAllMocks()
  api.getAuditLogs.mockResolvedValue({
    data: {
      items: rows, total: 2, page: 1, page_size: 50, pages: 1,
      facets: { actions: ['deliver', 'wallet_order_payment'], resource_types: ['order'] },
    },
  })
  api.getAuditSummary.mockResolvedValue({ data: { days: 7, items: [{ action: 'deliver', count: 1 }] } })
})

describe('操作审计页', () => {
  it('渲染记录，系统动作显示为「系统」', async () => {
    const wrapper = mount(AuditLogView)
    await flushPromises()

    expect(wrapper.text()).toContain('谁在什么时候改了什么')
    expect(wrapper.text()).toContain('交付线路')
    expect(wrapper.text()).toContain('管理员')
    expect(wrapper.text()).toContain('系统')
    expect(wrapper.text()).toContain('余额支付订单')
  })

  it('按动作过滤时带上参数重新请求，页码回到第一页', async () => {
    const wrapper = mount(AuditLogView)
    await flushPromises()

    const actionSelect = wrapper.findAll('select')[1]
    await actionSelect.setValue('deliver')
    await flushPromises()

    const lastParams = api.getAuditLogs.mock.calls.at(-1)[0]
    expect(lastParams.action).toBe('deliver')
    expect(lastParams.page).toBe(1)
  })

  it('关键词搜索走服务端，不在前端假过滤', async () => {
    const wrapper = mount(AuditLogView)
    await flushPromises()

    const keywordInput = wrapper.find('input[placeholder^="在详情里搜"]')
    await keywordInput.setValue('10900')
    await wrapper.find('button.audit-primary').trigger('click')
    await flushPromises()

    const lastParams = api.getAuditLogs.mock.calls.at(-1)[0]
    expect(lastParams.keyword).toBe('10900')
  })
})
