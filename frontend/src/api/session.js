import { reactive, ref } from 'vue'

const portals = ['customer', 'sales', 'admin']
const sessions = reactive(Object.fromEntries(portals.map(portal => [portal, {
  accessToken: '', csrfToken: '', user: null,
}])))
export const portalForRole = role => role === 'super_admin' ? 'admin' : role
export const portalForPath = path => /^\/admin(?:\/|$)/.test(path) ? 'admin' : /^\/sales(?:\/|$)/.test(path) ? 'sales' : 'customer'
export const loginPathForPortal = portal => portal === 'customer' ? '/login' : `/${portal}/login`
export const passwordPathForPortal = portal => portal === 'customer' ? '/change-password' : `/${portal}/change-password`
export const activePortal = ref(portalForPath(globalThis.location?.pathname || '/'))
export const sessionFor = (portal = activePortal.value) => sessions[portal]
export const session = {
  // 模拟态下这里返回的是被模拟的人，页面上所有"当前用户"的判断才会跟着变
  get user() { return currentUserForPortal() },
  set user(user) { sessionFor().user = user },
}
const channel = typeof BroadcastChannel !== 'undefined' ? new BroadcastChannel('ops-session') : null

export function setSession(data, portal = activePortal.value) {
  if (portalForRole(data.user?.role) !== portal) throw new Error('会话角色与登录入口不匹配')
  Object.assign(sessionFor(portal), { accessToken: data.access_token, csrfToken: data.csrf_token, user: data.user })
}

export function clearSession(notify = true, portal = activePortal.value) {
  Object.assign(sessionFor(portal), { accessToken: '', csrfToken: '', user: null })
  if (notify) channel?.postMessage({ type: 'session-cleared', portal })
}

export function onSessionChange(handler) {
  if (!channel) return () => {}
  const listener = (event) => {
    if (portals.includes(event.data?.portal)) handler(event.data.type, event.data.portal)
  }
  channel.addEventListener('message', listener)
  return () => channel.removeEventListener('message', listener)
}

try {
  localStorage.removeItem('ops_token')
  localStorage.removeItem('ops_user')
} catch { /* 浏览器禁用存储时仍使用内存会话 */ }

// ---- 模拟登录状态（超级管理员以销售/客户身份查看）----
// 存在 sessionStorage：按标签隔离，两个标签可以同时模拟不同的人，互不覆盖。
// 不写 cookie、不广播——否则一个标签退出会把另一个也踢掉。
const IMP_KEY = 'ops-impersonation'

export const impersonation = reactive({
  active: false, token: '', user: null, actorName: '', targetName: '', expiresAt: 0,
})

function persistImpersonation() {
  try {
    if (!impersonation.active) sessionStorage.removeItem(IMP_KEY)
    else {
      sessionStorage.setItem(IMP_KEY, JSON.stringify({
        token: impersonation.token,
        user: impersonation.user,
        actorName: impersonation.actorName,
        targetName: impersonation.targetName,
        expiresAt: impersonation.expiresAt,
      }))
    }
  } catch { /* 隐私模式下存储不可用时只保留内存状态 */ }
}

export function setImpersonation(payload) {
  Object.assign(impersonation, {
    active: true,
    token: payload.token,
    user: payload.user || null,
    actorName: payload.actorName || '',
    targetName: payload.targetName || '',
    expiresAt: payload.expiresAt || 0,
  })
  persistImpersonation()
}

export function updateImpersonationUser(user, expiresAt) {
  if (!impersonation.active) return
  if (user) impersonation.user = user
  if (expiresAt) impersonation.expiresAt = expiresAt
  persistImpersonation()
}

export function clearImpersonation() {
  Object.assign(impersonation, { active: false, token: '', user: null, actorName: '', targetName: '', expiresAt: 0 })
  persistImpersonation()
}

// expiresAt = 0 表示不预设时长（由管理员在后台手动结束）。
// 不能再用"有没有 expiresAt"判断是否有效，否则无时限的模拟会被当成已过期。
export const impersonationActive = () =>
  impersonation.active && (impersonation.expiresAt === 0 || impersonation.expiresAt > Date.now())

/** 模拟只在销售/客户工作台有意义；/admin 下出现模拟态说明它已经结束。 */
export function impersonationBelongsToPath(path = '') {
  return path === '/sales' || path.startsWith('/sales/') || path === '/client' || path.startsWith('/client/')
}

const LOGIN_PATHS = ['/login', '/register', '/admin/login', '/sales/login']

/** 登录/注册页永远不携带模拟态：在这里恢复它，只会换来 401 与跳转循环。 */
export function isAuthEntryPath(path = '') {
  return LOGIN_PATHS.includes(path) || path.startsWith('/recover-password')
}

/**
 * 页面启动时调用一次：
 * - 新标签从 URL hash 拿 token（放 hash 不进服务器日志），拿到后立刻从地址栏抹掉；
 * - 刷新时从本标签的 sessionStorage 恢复；
 * - 已过期的一律清掉，不让它复活。
 */
export function initImpersonation() {
  try {
    // 登录页/注册页/后台页面都不该带模拟态。留着它，守卫会去恢复会话、
    // 拿到 401、再跳回同一个地址——这就是之前那个"登录页无限刷新"。
    const here = globalThis.location?.pathname || ''
    if (here && (!impersonationBelongsToPath(here) || isAuthEntryPath(here))) {
      clearImpersonation()
      return
    }
    const match = /[#&]imp=([^&]+)/.exec(globalThis.location?.hash || '')
    if (match) {
      const token = decodeURIComponent(match[1])
      // 不预设时长：服务端只认记录里有没有结束，不再有 10 分钟硬过期
      setImpersonation({ token, expiresAt: 0 })
      const url = `${globalThis.location.pathname}${globalThis.location.search}`
      globalThis.history?.replaceState(null, '', url)
    } else {
      const raw = sessionStorage.getItem(IMP_KEY)
      if (raw) {
        const saved = JSON.parse(raw)
        const alive = saved?.token && (saved.expiresAt === 0 || saved.expiresAt > Date.now())
        if (alive) Object.assign(impersonation, saved, { active: true })
        else sessionStorage.removeItem(IMP_KEY)
      }
    }
  } catch { /* 解析失败就当没有模拟态 */ }
  if (!impersonationActive()) clearImpersonation()
}

/** 模拟态下按标签匹配返回被模拟用户；其余情况回落到正常会话。 */
export function currentUserForPortal(portal = activePortal.value) {
  if (!impersonationActive() || !impersonation.user) return sessionFor(portal).user
  return portalForRole(impersonation.user.role) === portal ? impersonation.user : sessionFor(portal).user
}
