// @vitest-environment jsdom
// 防复发：模拟登录结束后落到 /admin 下，绝不能再把模拟态恢复出来。
// 之前就是这样转圈的：登录页守卫恢复会话 → 401 → 跳回同一个地址 → 再恢复 → 无限刷新。
import { beforeEach, describe, expect, it, vi } from 'vitest'

function setPath(path) {
  Object.defineProperty(window, 'location', {
    value: { pathname: path, search: '', hash: '', replace: vi.fn() },
    writable: true,
  })
}

beforeEach(() => {
  vi.resetModules()
  sessionStorage.clear()
})

describe('模拟态与页面路径', () => {
  it('只有销售/客户工作台才算模拟态该在的地方', async () => {
    const { impersonationBelongsToPath } = await import('../src/api/session')
    expect(impersonationBelongsToPath('/sales')).toBe(true)
    expect(impersonationBelongsToPath('/sales/lines/12')).toBe(true)
    expect(impersonationBelongsToPath('/client')).toBe(true)
    expect(impersonationBelongsToPath('/client/wallet')).toBe(true)
    expect(impersonationBelongsToPath('/admin/login')).toBe(false)
    expect(impersonationBelongsToPath('/admin')).toBe(false)
    expect(impersonationBelongsToPath('/buy')).toBe(false)
  })

  it('后台页面启动时直接清掉 sessionStorage 里的模拟态', async () => {
    sessionStorage.setItem('ops-impersonation', JSON.stringify({
      token: 'imp_x', user: { role: 'customer' }, targetName: '客户乙',
      expiresAt: Date.now() + 300000,
    }))
    setPath('/admin/login')

    const mod = await import('../src/api/session')
    mod.initImpersonation()

    expect(sessionStorage.getItem('ops-impersonation')).toBeNull()
    expect(mod.impersonation.active).toBe(false)
  })

  it('工作台页面仍然会恢复模拟态（别把正常功能一起关掉）', async () => {
    const expiresAt = Date.now() + 300000
    sessionStorage.setItem('ops-impersonation', JSON.stringify({
      token: 'imp_x', user: { role: 'customer', display_name: '客户乙' },
      targetName: '客户乙', expiresAt,
    }))
    setPath('/client/lines')

    const mod = await import('../src/api/session')
    mod.initImpersonation()

    expect(mod.impersonation.active).toBe(true)
    expect(mod.impersonation.token).toBe('imp_x')
  })
})
