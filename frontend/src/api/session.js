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
  get user() { return sessionFor().user },
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
