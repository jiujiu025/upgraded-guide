import axios from 'axios'
import { helpArticles, resources } from '../mocks/data'
import { clearSession, onSessionChange, session, setSession } from './session'

const client = axios.create({ baseURL: import.meta.env.VITE_API_BASE_URL || '/api', timeout: 10000, withCredentials: true })
let refreshPromise = null
onSessionChange((type) => {
  if (type !== 'session-cleared') return
  clearSession(false)
  if (typeof window !== 'undefined' && /^\/(client|sales|admin)(\/|$)/.test(window.location.pathname)) {
    window.location.replace('/login')
  }
})
client.interceptors.request.use((config) => {
  const token = session.accessToken
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})
async function restoreSession(force = false) {
  if (!force && session.accessToken) return session.user
  const refreshWork = async () => {
    const csrf = await client.get('/auth/csrf', { skipRefresh: true })
    const result = await client.post('/auth/refresh', {}, { skipRefresh: true, headers: { 'X-CSRF-Token': csrf.data.csrf_token } })
    setSession(result.data)
    return session.user
  }
  if (!refreshPromise) refreshPromise = (async () => {
    if (typeof navigator !== 'undefined' && navigator.locks?.request) return navigator.locks.request('ops-refresh', refreshWork)
    return refreshWork()
  })().finally(() => { refreshPromise = null })
  return refreshPromise
}
export const ensureSession = restoreSession
export const hasSession = () => Boolean(session.user)
export const currentSessionUser = () => session.user

client.interceptors.response.use((response) => response, async (error) => {
  if (error.response?.status === 401 && !error.config?.skipRefresh && !error.config?._retried && !error.config?.url?.startsWith('/auth/')) {
    try {
      if (session.accessToken === error.config.headers?.Authorization?.replace(/^Bearer /, '')) await restoreSession(true)
      return client({ ...error.config, _retried: true, headers: { ...error.config.headers, Authorization: `Bearer ${session.accessToken}` } })
    } catch { clearSession() }
  }
  if (error.response?.status === 401) {
    clearSession()
  }
  const detail = error.response?.data?.detail
  const status = error.response?.status
  const validationMessage = Array.isArray(detail) ? detail.map((item) => item.msg?.replace(/^Value error,\s*/, '')).filter(Boolean).join('；') : ''
  const objectMessage = detail && !Array.isArray(detail) && typeof detail.message === 'string' ? detail.message : ''
  error.userMessage = typeof detail === 'string' ? detail : validationMessage || objectMessage || (status === 403 ? '当前账号没有执行此操作的权限' : status >= 500 ? '服务暂时不可用，请稍后重试' : error.response ? '请求失败，请检查填写内容后重试' : '网络连接失败，请确认后端服务已启动')
  return Promise.reject(error)
})

// 仅教程和资源内容继续使用本地内容数据，业务接口全部请求后端。
const contentMock = (data) => Promise.resolve({ data })
export const getArticles = () => contentMock(helpArticles)
export const getResources = () => contentMock(resources)
export const getPublicProducts = () => client.get('/public/products')
export const getPublicProduct = (id) => client.get(`/public/products/${id}`)
export const getPublicRegions = () => client.get('/public/regions')
export const previewOrder = (payload) => client.post('/public/orders/preview', payload)
export const login = async (payload) => { const result = await client.post('/auth/login', payload); setSession(result.data); return result }
export const register = async (payload) => { const result = await client.post('/public/register', payload); setSession(result.data); return result }
export const logout = async () => {
  try {
    const csrf = await client.get('/auth/csrf', { skipRefresh: true })
    await client.post('/auth/logout', {}, { skipRefresh: true, headers: { 'X-CSRF-Token': csrf.data.csrf_token } })
  } finally { clearSession() }
}
export const changePassword = (payload) => client.post('/auth/change-password', payload)
export const getAuthMe = () => client.get('/auth/me')
export const getCustomerMe = () => client.get('/customer/me')
export const getCustomerOrders = () => client.get('/customer/orders')
export const getCustomerOrder = (id) => client.get(`/customer/orders/${id}`)
export const createCustomerOrder = (payload) => client.post('/customer/orders', payload)
export const cancelCustomerOrder = (id) => client.post(`/customer/orders/${id}/cancel`)
export const getCustomerLines = () => client.get('/customer/lines')
export const getCustomerLine = (id) => client.get(`/customer/lines/${id}`)
export const updateCustomerLineNote = (id, payload) => client.patch(`/customer/lines/${id}/note`, payload)
export const getCustomerRenewals = () => client.get('/customer/renewals')
export const renewCustomerLine = (id, payload) => client.post(`/customer/lines/${id}/renew`, payload)
export const getNotifications = () => client.get('/notifications')
export const markNotificationRead = (id) => client.post(`/notifications/${id}/read`)
export const markAllNotificationsRead = () => client.post('/notifications/read-all')
export const getInboxThreads = (params) => client.get('/inbox/threads', { params })
export const createInboxThread = (payload) => client.post('/inbox/threads', payload)
export const getInboxThread = (id) => client.get(`/inbox/threads/${id}`)
export const getInboxMessages = (id, params) => client.get(`/inbox/threads/${id}/messages`, { params })
export const sendInboxMessage = (id, payload) => client.post(`/inbox/threads/${id}/messages`, payload)
export const markInboxThreadRead = (id) => client.post(`/inbox/threads/${id}/read`)
export const updateInboxThread = (id, payload) => client.patch(`/inbox/threads/${id}`, payload)
export const getInboxUnreadCount = () => client.get('/inbox/unread-count')
export const getInboxSalesOptions = (id) => client.get(`/inbox/threads/${id}/sales-options`)
export const getSalesDashboard = () => client.get('/sales/dashboard')
export const getSalesCustomers = () => client.get('/sales/customers')
export const getSalesOrders = () => client.get('/sales/orders')
export const getSalesExpiringLines = () => client.get('/sales/expiring-lines')
export const getSalesHealthImpacts = () => client.get('/sales/health-impacts')
export const getAdminOrders = () => client.get('/admin/orders')
export const getManagedLine = (id) => client.get(`/lines/${id}`)
export const confirmAdminOrder = (id) => client.post(`/admin/orders/${id}/confirm`)
export const deliverAdminOrder = (id, payload) => client.post(`/admin/orders/${id}/deliver`, payload)
export const cancelAdminOrder = (id) => client.post(`/admin/orders/${id}/cancel`)
export const completeAdminRenewal = (id) => client.post(`/admin/orders/${id}/complete-renewal`, { manually_confirmed: true })
export const getAdminCatalogProducts = () => client.get('/products')
export const getAdminCatalogRegions = () => client.get('/regions')
export const getSupplierOverview = () => client.get('/admin/suppliers/overview')
export const getSupplierProducts = () => client.get('/admin/suppliers/products')
export const getSupplierRegions = () => client.get('/admin/suppliers/regions')
export const getSupplierInventory = () => client.get('/admin/suppliers/inventory')
export const getSupplierQuotes = () => client.get('/admin/suppliers/quotes')
export const getSupplierRules = () => client.get('/admin/suppliers/supply-rules')
export const getSupplierSyncRuns = () => client.get('/admin/suppliers/sync-runs')
export const getCatalogPublications = () => client.get('/admin/suppliers/publications')
export const publishCatalogSnapshot = (payload) => client.post('/admin/suppliers/publications', payload)
export const withdrawCatalogPublication = (id) => client.post(`/admin/suppliers/publications/${id}/withdraw`)
export const syncSupplierProducts = () => client.post('/admin/suppliers/sync/products')
export const syncSupplierCountries = () => client.post('/admin/suppliers/sync/countries?product_type_id=2')
export const syncSupplierCities = () => client.post('/admin/suppliers/sync/cities?product_type_id=2')
export const syncSupplierInventory = (countryCode) => client.post(`/admin/suppliers/sync/inventory?product_type_id=2&country_code=${encodeURIComponent(countryCode)}`)
export const syncSupplierQuotes = () => client.post('/admin/suppliers/sync/quotes', {})
export const mapSupplierProduct = (id, localProductId) => client.patch(`/admin/suppliers/products/${id}/mapping`, { local_product_id: localProductId || null })
export const mapSupplierRegion = (id, localRegionId) => client.patch(`/admin/suppliers/regions/${id}/mapping`, { local_region_id: localRegionId || null })
export const saveSupplierRule = (payload) => client.post('/admin/suppliers/supply-rules', payload)
export default client
