// @vitest-environment jsdom
// 模拟态横幅：必须一直可见、剩余时间在走、退出会真的通知后端。
// 这条横幅是"管理员别忘了自己在别人账号里"的最后一道提示，不能只在控制台里有状态。
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ logoutImpersonation: vi.fn() }))
const state = vi.hoisted(() => ({
  impersonation: {
    active: true, token: 'imp_test', actorName: '超级管理员', targetName: '销售甲',
    user: { id: 2, role: 'sales', display_name: '销售甲' }, expiresAt: 0,
  },
  impersonationActive: vi.fn(() => true),
  clearImpersonation: vi.fn(),
}))
vi.mock('../src/api', () => api)
vi.mock('../src/api/session', () => state)

import ImpersonationBanner from '../src/components/ImpersonationBanner.vue'

beforeEach(() => {
  vi.clearAllMocks()
  api.logoutImpersonation.mockResolvedValue({ data: null })
  state.impersonation.expiresAt = Date.now() + 9 * 60 * 1000
  Object.defineProperty(window, 'location', {
    value: { replace: vi.fn() }, writable: true,
  })
})

describe('ImpersonationBanner', () => {
  it('显示被模拟身份、审计归属与剩余时间', () => {
    const wrapper = mount(ImpersonationBanner)
    const text = wrapper.text()
    expect(text).toContain('销售甲')
    expect(text).toContain('超级管理员')
    expect(text).toContain('剩余')
    expect(text).toMatch(/08:5\d|09:00/)
  })

  it('退出时通知后端并清理本地状态', async () => {
    const wrapper = mount(ImpersonationBanner)
    await wrapper.find('.imp-exit').trigger('click')
    await flushPromises()

    expect(api.logoutImpersonation).toHaveBeenCalled()
    expect(state.clearImpersonation).toHaveBeenCalled()
    expect(window.location.replace).toHaveBeenCalledWith('/admin/login?imp=ended')
  })

  it('剩余时间进入最后一分钟时变红', () => {
    state.impersonation.expiresAt = Date.now() + 30 * 1000
    const wrapper = mount(ImpersonationBanner)
    expect(wrapper.find('.imp-clock').classes()).toContain('urgent')
  })
})
