import { describe, expect, it, beforeEach } from 'vitest'
import { clearSession, portalForPath, portalForRole, sessionFor, setSession } from '../src/api/session'

const payload = (role) => ({
  access_token: `token-${role}`,
  csrf_token: `csrf-${role}`,
  user: { username: role, role, display_name: role },
})

const reset = () => ['customer', 'sales', 'admin'].forEach(portal => clearSession(false, portal))

describe('三端会话互不覆盖', () => {
  beforeEach(reset)

  it('管理员登录后客户端会话仍然在', () => {
    setSession(payload('customer'), 'customer')
    setSession(payload('super_admin'), 'admin')
    expect(sessionFor('customer').accessToken).toBe('token-customer')
    expect(sessionFor('customer').user.role).toBe('customer')
    expect(sessionFor('admin').accessToken).toBe('token-super_admin')
  })

  it('登出只清本入口，不动其他入口', () => {
    setSession(payload('customer'), 'customer')
    setSession(payload('sales'), 'sales')
    clearSession(false, 'customer')
    expect(sessionFor('customer').user).toBeNull()
    expect(sessionFor('sales').user.role).toBe('sales')
  })

  it('角色和入口不匹配时拒绝写入，避免串号', () => {
    expect(() => setSession(payload('customer'), 'admin')).toThrow()
    expect(() => setSession(payload('sales'), 'admin')).toThrow()
    expect(sessionFor('admin').user).toBeNull()
  })

  it('路由归属与角色归属一一对应', () => {
    expect(portalForPath('/admin/orders')).toBe('admin')
    expect(portalForPath('/sales/customers')).toBe('sales')
    expect(portalForPath('/client/orders')).toBe('customer')
    expect(portalForPath('/products')).toBe('customer')
    expect(portalForRole('super_admin')).toBe('admin')
    expect(portalForRole('sales')).toBe('sales')
    expect(portalForRole('customer')).toBe('customer')
  })
})
