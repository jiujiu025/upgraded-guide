// @vitest-environment jsdom
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  getTaskCatalog: vi.fn(),
  getTaskRuns: vi.fn(),
  runTaskNow: vi.fn(),
}))

vi.mock('../src/api', () => api)

import AdminTasksView from '../src/views/AdminTasksView.vue'

const catalog = [
  { name: 'payment_reconcile', title: '支付对账与挂单清理', interval_minutes: 15 },
  { name: 'close_expired_orders', title: '关闭超时未支付订单', interval_minutes: 30 },
]

const runs = [
  {
    id: 3, task_name: 'payment_reconcile', status: 'failed', triggered_by: 'scheduler',
    started_at: '2026-11-08T06:00:00', duration_ms: 1200,
    summary: '{}', error: 'PaymentGatewayError: 上游接口不可用',
  },
  {
    id: 2, task_name: 'close_expired_orders', status: 'success', triggered_by: 'scheduler',
    started_at: '2026-11-08T05:30:00', duration_ms: 23,
    summary: '{"closed": 2, "scan_note": "scanned=5"}', error: null,
  },
]

function response(data) { return Promise.resolve({ data }) }

beforeEach(() => {
  api.getTaskCatalog.mockImplementation(() => response(catalog))
  api.getTaskRuns.mockImplementation(() => response(runs))
  api.runTaskNow.mockImplementation(() => response({ status: 'success', summary: '{"closed":1}' }))
})

describe('AdminTasksView', () => {
  it('展示任务清单与最近执行流水', async () => {
    const wrapper = mount(AdminTasksView)
    await flushPromises()

    expect(api.getTaskRuns).toHaveBeenCalledWith({ limit: 100 })
    expect(wrapper.text()).toContain('支付对账与挂单清理')
    expect(wrapper.text()).toContain('每 15 分钟')
    expect(wrapper.text()).toContain('失败')
    expect(wrapper.text()).toContain('PaymentGatewayError')
    expect(wrapper.text()).toContain('closed=2')
    expect(wrapper.text()).toContain('23ms')
  })

  it('手动运行后提示结果并重新拉取流水', async () => {
    const wrapper = mount(AdminTasksView)
    await flushPromises()
    api.getTaskRuns.mockClear()

    const button = wrapper.findAll('.task-catalog article button')[1]
    await button.trigger('click')
    await flushPromises()

    expect(api.runTaskNow).toHaveBeenCalledWith('close_expired_orders')
    expect(api.getTaskRuns).toHaveBeenCalledTimes(1)
    expect(wrapper.text()).toContain('执行完成')
  })

  it('接口报错时展示可重试的错误态', async () => {
    api.getTaskCatalog.mockImplementation(() => Promise.reject(new Error('network down')))
    const wrapper = mount(AdminTasksView)
    await flushPromises()

    expect(wrapper.text()).toContain('流水暂时无法加载')
    expect(wrapper.text()).toContain('network down')
  })
})
