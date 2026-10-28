import { reactive } from 'vue'

export const session = reactive({ accessToken: '', csrfToken: '', user: null })
const channel = typeof BroadcastChannel !== 'undefined' ? new BroadcastChannel('ops-session') : null

export function setSession(data) {
  session.accessToken = data.access_token
  session.csrfToken = data.csrf_token
  session.user = data.user
  channel?.postMessage({ type: 'session-changed' })
}

export function clearSession(notify = true) {
  session.accessToken = ''
  session.csrfToken = ''
  session.user = null
  if (notify) channel?.postMessage({ type: 'session-cleared' })
}

export function onSessionChange(handler) {
  if (!channel) return () => {}
  const listener = (event) => handler(event.data?.type)
  channel.addEventListener('message', listener)
  return () => channel.removeEventListener('message', listener)
}

try {
  localStorage.removeItem('ops_token')
  localStorage.removeItem('ops_user')
} catch { /* 浏览器禁用存储时仍使用内存会话 */ }
