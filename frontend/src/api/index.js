import axios from 'axios'
import { activePortal, clearSession, loginPathForPortal, onSessionChange, passwordPathForPortal, sessionFor, setSession } from './session'

const client = axios.create({ baseURL: import.meta.env.VITE_API_BASE_URL || '/api', timeout: 10000, withCredentials: true })
const refreshPromises = new Map()
const workspaceRoot = portal => portal === 'customer' ? '/client' : `/${portal}`
onSessionChange((type, portal) => {
  if (type !== 'session-cleared') return
  clearSession(false, portal)
  if (portal === activePortal.value && typeof window !== 'undefined') {
    const path = window.location.pathname
    const root = workspaceRoot(portal)
    const loginPath = loginPathForPortal(portal)
    if ((path === root || path.startsWith(`${root}/`) || path === passwordPathForPortal(portal)) && path !== loginPath) {
      window.location.replace(loginPath)
    }
  }
})
client.interceptors.request.use((config) => {
  config.authPortal ??= activePortal.value
  const token = sessionFor(config.authPortal).accessToken
  if (token && !config.skipBearer) config.headers.Authorization = `Bearer ${token}`
  return config
}, undefined, { synchronous: true })

const portalConfig = portal => ({ authPortal: portal, params: { portal } })
const cookieConfig = portal => ({ ...portalConfig(portal), skipBearer: true, skipRefresh: true })
const withPortalLock = (portal, work) => globalThis.navigator?.locks?.request
  ? navigator.locks.request(`ops-session-${portal}`, work)
  : work()

async function restoreSession(portal = activePortal.value, force = false) {
  const state = sessionFor(portal)
  if (!force && state.accessToken) return state.user
  if (!refreshPromises.has(portal)) {
    const refresh = withPortalLock(portal, async () => {
      const config = cookieConfig(portal)
      const csrf = await client.get('/auth/csrf', config)
      const { data } = await client.post('/auth/refresh', {}, {
        ...config, headers: { 'X-CSRF-Token': csrf.data.csrf_token },
      })
      setSession(data, portal)
      return state.user
    })
    refreshPromises.set(portal, refresh.finally(() => refreshPromises.delete(portal)))
  }
  return refreshPromises.get(portal)
}
export const ensureSession = restoreSession
export const hasSession = () => Boolean(sessionFor().user)
export const currentSessionUser = portal => sessionFor(portal).user

client.interceptors.response.use((response) => response, async (error) => {
  const config = error.config
  const portal = config?.authPortal ?? activePortal.value
  const state = sessionFor(portal)
  const requestToken = config?.headers?.Authorization?.replace(/^Bearer /, '') || ''
  if (error.response?.status === 401 && config && !config.skipRefresh && !config._retried && !config.url?.startsWith('/auth/')) {
    try {
      if (state.accessToken === requestToken) await restoreSession(portal, true)
      if (state.accessToken) return client({ ...config, _retried: true })
    } catch { /* 仅清理此次请求使用的失效会话 */ }
  }
  if (error.response?.status === 401 && requestToken && requestToken === state.accessToken
    && (!config.url?.startsWith('/auth/') || config.url === '/auth/me')) {
    clearSession(false, portal)
  }
  const detail = error.response?.data?.detail
  const status = error.response?.status
  // 428 = 后端要求先改密（管理员重置过密码）。直接把人送到改密页，
  // 否则每个业务请求都会失败，用户只会看到一堆"请求失败"。
  if (status === 428 && portal === activePortal.value && typeof window !== 'undefined') {
    const path = passwordPathForPortal(portal)
    if (window.location.pathname !== path) window.location.replace(path)
  }
  const validationMessage = Array.isArray(detail) ? detail.map((item) => item.msg?.replace(/^Value error,\s*/, '')).filter(Boolean).join('；') : ''
  const objectMessage = detail && !Array.isArray(detail) && typeof detail.message === 'string' ? detail.message : ''
  error.userMessage = typeof detail === 'string' ? detail : validationMessage || objectMessage || (status === 403 ? '当前账号没有执行此操作的权限' : status >= 500 ? '服务暂时不可用，请稍后重试' : error.response ? '请求失败，请检查填写内容后重试' : '网络连接失败，请确认后端服务已启动')
  return Promise.reject(error)
})

export const getPublicProducts = () => client.get('/public/products')
export const getPublicCatalog = () => client.get('/public/catalog')
export const getPublicCatalogDetail = (code) => client.get(`/public/catalog/${encodeURIComponent(code)}`)
// 登录后才带价格：未登录调 /public/* 一律拿不到金额，前端不做"藏起来"这种假保护。
export const getCatalog = () => client.get('/catalog')
export const getCatalogDetail = (code) => client.get(`/catalog/${encodeURIComponent(code)}`)
// 可选精细化选项：某商品可指定的城市码与 IP 段（未配置时 supported=false）
export const getCatalogCities = (productId) => client.get('/catalog/cities', { params: { product_id: productId } })
export const getPublicProduct = (id) => client.get(`/public/products/${id}`)
export const getPublicRegions = () => client.get('/public/regions')
// 注册图形验证码：拿九宫格题目（图形是 SVG，答案在 token 里，前端读不出来）
export const getCaptcha = () => client.get('/public/captcha')
export const previewOrder = (payload) => client.post('/public/orders/preview', payload)
export const login = (payload, portal = activePortal.value) => withPortalLock(portal, async () => {
  const result = await client.post('/auth/login', payload, cookieConfig(portal))
  setSession(result.data, portal)
  return result
})
export const register = payload => withPortalLock('customer', async () => {
  const result = await client.post('/public/register', payload, cookieConfig('customer'))
  setSession(result.data, 'customer')
  return result
})
export const logout = (portal = activePortal.value) => withPortalLock(portal, async () => {
  try {
    const config = cookieConfig(portal)
    const csrf = await client.get('/auth/csrf', config)
    await client.post('/auth/logout', {}, { ...config, headers: { 'X-CSRF-Token': csrf.data.csrf_token } })
  } finally { clearSession(true, portal) }
})
export const changePassword = (payload, portal = activePortal.value) =>
  withPortalLock(portal, () => client.post('/auth/change-password', payload, portalConfig(portal)))
export const getSecurityQuestions = () => client.get('/auth/security-questions')
export const updateSecurityQuestions = (payload) => client.put('/auth/security-questions', payload)
export const recoverPassword = (payload) => client.post('/auth/password-recovery', payload)
export const getAuthMe = () => client.get('/auth/me')
export const getCustomerMe = () => client.get('/customer/me')
export const getCustomerOrders = (params = {}) => client.get('/customer/orders', { params })
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
export const createOnBehalfOrder = (payload) => client.post('/orders/on-behalf', payload)
// 线路升级：静态住宅 → 视频/社媒、直播，只补差价，到期时间不变。
// 三个入口共用后端同一套口径，前端按当前工作台选对应路径，不自己算差价。
export const getLineUpgradeOptions = (lineId) => client.get(`/admin/lines/${lineId}/upgrade-options`)
export const upgradeLine = (lineId, payload) => client.post(`/admin/lines/${lineId}/upgrade`, payload)
export const getCustomerLineUpgradeOptions = (lineId) => client.get(`/customer/lines/${lineId}/upgrade-options`)
export const upgradeCustomerLine = (lineId, payload) => client.post(`/customer/lines/${lineId}/upgrade`, payload)
export const getSalesLineUpgradeOptions = (lineId) => client.get(`/sales/lines/${lineId}/upgrade-options`)
export const upgradeSalesLine = (lineId, payload) => client.post(`/sales/lines/${lineId}/upgrade`, payload)
export const getSalesCustomers = () => client.get('/sales/customers')
export const getSalesOrders = (params = {}) => client.get('/sales/orders', { params })
export const getSalesOrder = (id) => client.get(`/sales/orders/${id}`)
export const getSalesLines = (params = {}) => client.get('/sales/lines', { params })
export const getSalesRenewals = () => client.get('/sales/renewals')
export const renewSalesLine = (id, payload) => client.post(`/sales/lines/${id}/renew`, payload)
export const getSalesExpiringLines = () => client.get('/sales/expiring-lines')
export const getSalesHealthImpacts = () => client.get('/sales/health-impacts')
export const getInviteCodeCheck = (code) => client.get(`/public/invite-codes/${encodeURIComponent(code)}`)
export const getSalesInviteCodes = () => client.get('/sales/invite-codes')
export const createSalesInviteCode = (payload) => client.post('/sales/invite-codes', payload)
export const disableSalesInviteCode = (id) => client.post(`/sales/invite-codes/${id}/disable`)
export const getAdminInviteCodes = (params = {}) => client.get('/admin/invite-codes', { params })
export const disableAdminInviteCode = (id) => client.post(`/admin/invite-codes/${id}/disable`)
export const getAdminNotificationChannels = () => client.get('/admin/notification-channels')
export const createAdminNotificationChannel = (payload) => client.post('/admin/notification-channels', payload)
export const updateAdminNotificationChannel = (id, payload) => client.patch(`/admin/notification-channels/${id}`, payload)
export const testAdminNotificationChannel = (id, payload) => client.post(`/admin/notification-channels/${id}/test`, payload)
export const getAdminChannelBindings = (channelId) => client.get(`/admin/notification-channels/${channelId}/bindings`)
export const createAdminChannelBinding = (channelId, payload) => client.post(`/admin/notification-channels/${channelId}/bindings`, payload)
export const deleteAdminChannelBinding = (channelId, bindingId) => client.delete(`/admin/notification-channels/${channelId}/bindings/${bindingId}`)
export const getAdminNotificationDeliveries = (params = {}) => client.get('/admin/notification-deliveries', { params })
export const getMyNotificationChannels = () => client.get('/notifications/channels/me')
export const toggleMyNotificationChannel = (bindingId) => client.post(`/notifications/channels/me/${bindingId}/toggle`)
export const getSalesOutreach = (params = {}) => client.get('/sales/renewal-outreach', { params })
export const recordRenewalTouch = (lineId, payload) => client.post(`/sales/renewal-outreach/${lineId}`, payload)
export const getRenewalTouchHistory = (params = {}) => client.get('/sales/renewal-outreach/history', { params })
export const getAdminOrders = (params = {}) => client.get('/admin/orders', { params })
export const getAdminOrderCenterOptions = (params = {}) => client.get('/admin/order-center/options', { params })
export const getAdminSubscriptions = (params = {}) => client.get('/admin/order-center/subscriptions', { params })
export const getAdminOrderCenterOrders = () => client.get('/admin/order-center/orders')
export const createAdminResourceOrder = (payload) => client.post('/admin/order-center/orders', payload)
export const getFinanceSummary = (params = {}) => client.get('/admin/finance/summary', { params })
export const getFinanceExports = () => client.get('/admin/finance/exports')
// 导出是流式响应，区间大时要扫几千行，默认 10 秒超时不够用；
// responseType 必须是 blob，否则中文会被当文本按错误的编码解一遍。
export const downloadFinanceExport = (kind, params = {}) =>
  client.get(`/admin/finance/exports/${kind}`, { params, responseType: 'blob', timeout: 120000 })
export const getTaskRuns = (params = {}) => client.get('/admin/task-runs', { params })
export const getTaskCatalog = () => client.get('/admin/task-runs/catalog')
export const runTaskNow = (name) => client.post(`/admin/task-runs/${encodeURIComponent(name)}/run`)
export const getAdminCustomers = (params = {}) => client.get('/admin/customers', { params })
export const createAdminCustomer = (payload) => client.post('/admin/customers', payload)
export const getAdminSalesOptions = () => client.get('/admin/customers/sales-options')
export const getAdminCustomer = (id) => client.get(`/admin/customers/${id}`)
export const getAdminCustomerRecords = (id, kind, offset = 0, filters = {}) => client.get(`/admin/customers/${id}/records`, { params: { kind, offset, limit: 50, ...filters } })
export const updateAdminCustomer = (id, payload) => client.patch(`/admin/customers/${id}`, payload)
export const getManagedCatalogProducts = (params = {}) => client.get('/admin/catalog-management/products', { params })
export const updateManagedCatalogProduct = (id, payload) => client.patch(`/admin/catalog-management/products/${id}`, payload)
export const bulkUpdateManagedCatalogProducts = (payload) => client.post('/admin/catalog-management/products/bulk', payload)
export const getManagedRetailPrices = (params = {}) => client.get('/admin/catalog-management/retail-prices', { params })
export const updateManagedRetailPrice = (id, payload) => client.patch(`/admin/catalog-management/retail-prices/${id}`, payload)
export const bulkUpdateManagedRetailPrices = (payload) => client.post('/admin/catalog-management/retail-prices/bulk', payload)
// 官网可见性：产品线 × 国家的上架/下架（缺价国家没有商品行，只能在这一层控制）
export const getCatalogListings = (params = {}) => client.get('/admin/catalog-management/listings', { params })
export const updateCatalogListing = (payload) => client.patch('/admin/catalog-management/listings', payload)
export const bulkUpdateCatalogListings = (payload) => client.post('/admin/catalog-management/listings/bulk', payload)
export const setManagedProductActive = (id, isActive) =>
  client.patch(`/admin/catalog-management/products/${id}/active`, { is_active: isActive })
// 城市与号段：客户可选的城市码清单
export const getManagedCities = (params = {}) => client.get('/admin/catalog-management/cities', { params })
export const createManagedCity = (payload) => client.post('/admin/catalog-management/cities', payload)
export const updateManagedCity = (id, payload) => client.patch(`/admin/catalog-management/cities/${id}`, payload)
export const deleteManagedCity = (id) => client.delete(`/admin/catalog-management/cities/${id}`)
export const searchFinanceAccounts = (q) => client.get('/admin/finance/accounts', { params: { q } })
export const createOfflineRecharge = (payload) => client.post('/admin/finance/offline-recharges', payload)
export const getPaymentConfig = () => client.get('/admin/finance/payment-config')
export const updatePaymentConfig = (payload) => client.put('/admin/finance/payment-config', payload)
export const checkPaymentApi = () => client.post('/admin/finance/payment-config/health')
export const getWallet = () => client.get('/wallet')
export const createOnlineRecharge = (payload) => client.post('/wallet/recharges', payload)
export const payWalletOrderWithBalance = (id, payload) => client.post(`/wallet/orders/${id}/pay-balance`, payload)
export const payWalletOrderOnline = (id, payload) => client.post(`/wallet/orders/${id}/pay-online`, payload)
export const recordOfflineOrderPayment = (id, payload) => client.post(`/admin/finance/orders/${id}/offline-payment`, payload)
export const recordOrderRefund = (id, payload) => client.post(`/admin/finance/orders/${id}/refund`, payload)
export const createSalesUser = (payload) => client.post('/admin/sales-users', payload)
export const getAdminSalesUsers = (params = {}) => client.get('/admin/sales-users', { params })
export const updateAdminSalesUser = (id, payload) => client.patch(`/admin/sales-users/${id}`, payload)
export const getAdminSalesLoginLogs = (id, limit = 50) => client.get(`/admin/sales-users/${id}/login-logs`, { params: { limit } })
export const getAdminSalesCustomers = (id, params = {}) => client.get(`/admin/sales-users/${id}/customers`, { params })
export const resetAdminUserPassword = (id, payload) => client.post(`/admin/users/${id}/reset-password`, payload)
export const updateAdminUserAccess = (id, payload) => client.patch(`/admin/users/${id}/access`, payload)
export const getManagedLine = (id) => client.get(`/lines/${id}`)
export const confirmAdminOrder = (id) => client.post(`/admin/orders/${id}/confirm`)
export const deliverAdminOrder = (id, payload) => client.post(`/admin/orders/${id}/deliver`, payload)
// 人工重新发起自动开通：已收款、没有成功采购记录时才会真的去上游下单
export const retryAdminProcurement = (id) => client.post(`/admin/orders/${id}/retry-procurement`)
// 采购记录（只读）：不含上游账号密码，重试仍走上面的订单接口
export const getProcurements = (params = {}) => client.get('/admin/procurements', { params })
export const getProcurement = (id) => client.get(`/admin/procurements/${id}`)
// 全局操作审计（只读）：按人/动作/资源/时间回溯
export const getAuditLogs = (params = {}) => client.get('/admin/audit-logs', { params })
export const getAuditSummary = (days = 7) => client.get('/admin/audit-logs/summary', { params: { days } })
export const cancelAdminOrder = (id) => client.post(`/admin/orders/${id}/cancel`)
export const completeAdminRenewal = (id) => client.post(`/admin/orders/${id}/complete-renewal`, { manually_confirmed: true })
export const getAdminCatalogProducts = () => client.get('/products')
export const getAdminCatalogRegions = () => client.get('/regions')
export const getSupplierOverview = () => client.get('/admin/suppliers/overview')
export const getSupplierPools = () => client.get('/admin/suppliers/pools')
// 自动采购总开关：后台点一下立即生效，不需要重启 api
export const setAutoProcurement = (enabled) => client.put('/admin/suppliers/auto-procurement', { enabled })
// 资源池 2（985Proxy）：拉库存与号段写进客户可选城市
export const syncSupplier985 = (supplierId, params = {}) =>
  client.post(`/admin/suppliers/${supplierId}/sync-985`, null, { params })
export const getSupplierProducts = () => client.get('/admin/suppliers/products')
export const getSupplierRegions = () => client.get('/admin/suppliers/regions')
export const getSupplierInventory = () => client.get('/admin/suppliers/inventory')
export const getSupplierIpSegments = (params = {}) => client.get('/admin/suppliers/ip-segments', { params })
export const getSupplierQuotes = () => client.get('/admin/suppliers/quotes')
export const getSupplierRules = () => client.get('/admin/suppliers/supply-rules')
export const getSupplierSyncRuns = () => client.get('/admin/suppliers/sync-runs')
export const saveSupplierCredential = (apiKey) => client.put('/admin/suppliers/credential', { api_key: apiKey })
// 按供应商录入 Key：每个资源池各自一把，互不覆盖
export const saveSupplierCredentialFor = (supplierId, apiKey) =>
  client.put(`/admin/suppliers/${supplierId}/credential`, { api_key: apiKey })
export const deleteSupplierCredentialFor = (supplierId) =>
  client.delete(`/admin/suppliers/${supplierId}/credential`)
export const deleteSupplierCredential = () => client.delete('/admin/suppliers/credential')
export const getCatalogPublications = () => client.get('/admin/suppliers/publications')
export const publishCatalogSnapshot = (payload) => client.post('/admin/suppliers/publications', payload)
export const withdrawCatalogPublication = (id) => client.post(`/admin/suppliers/publications/${id}/withdraw`)
export const syncSupplierProducts = () => client.post('/admin/suppliers/sync/products')
export const syncSupplierCatalog = () => client.post('/admin/suppliers/sync/catalog')
export const importSellableCatalog = (payload) => client.post('/admin/suppliers/catalog/import-sellable', payload)
export const syncSupplierCountries = (productTypeId = 2) => client.post(`/admin/suppliers/sync/countries?product_type_id=${encodeURIComponent(productTypeId)}`)
export const syncSupplierCities = (productTypeId = 2) => client.post(`/admin/suppliers/sync/cities?product_type_id=${encodeURIComponent(productTypeId)}`)
// 资源池1：按城市拉真实 IP 段，合并进客户可选城市清单（原生=3）
export const syncSupplierCitySegments = (params = {}) => client.post('/admin/suppliers/sync/city-segments', null, { params })
export const syncSupplierInventory = (countryCode, productTypeId = 2) => client.post(`/admin/suppliers/sync/inventory?product_type_id=${encodeURIComponent(productTypeId)}&country_code=${encodeURIComponent(countryCode)}`)
export const checkSupplierInventory = (payload) => client.post('/admin/suppliers/check-inventory', payload)
export const syncSupplierQuotes = () => client.post('/admin/suppliers/sync/quotes', {})
export const mapSupplierProduct = (id, localProductId, resourceCategory = null) => client.patch(`/admin/suppliers/products/${id}/mapping`, { local_product_id: localProductId || null, resource_category: resourceCategory || null })
export const mapSupplierRegion = (id, localRegionId) => client.patch(`/admin/suppliers/regions/${id}/mapping`, { local_region_id: localRegionId || null })
export const saveSupplierRule = (payload) => client.post('/admin/suppliers/supply-rules', payload)
// 管理员线路健康：所有客户线路 + 面板入站
export const getHealthOverview = () => client.get('/admin/health/overview')
export const getAdminCustomerLines = (params = {}) => client.get('/admin/health/customer-lines', { params })
export const checkAdminCustomerLines = (payload) => client.post('/admin/health/customer-lines/check', payload)
export const getTransitInbounds = (params = {}) => client.get('/admin/health/transit-inbounds', { params })
export const checkTransitInbounds = (payload) => client.post('/admin/health/transit-inbounds/check', payload)
// 中转面板（3x-ui）：面板池、国家映射、待配中转重试
export const getTransitSummary = () => client.get('/admin/transit/summary')
export const getTransitPanels = () => client.get('/admin/transit/vps')
export const createTransitPanel = (payload) => client.post('/admin/transit/vps', payload)
export const updateTransitPanel = (id, payload) => client.patch(`/admin/transit/vps/${id}`, payload)
// 只读检测：登录 + 列入站 + 读 Xray 模板，不改配置
export const testTransitPanel = (id) => client.post(`/admin/transit/vps/${id}/test`)
export const getTransitRoutes = () => client.get('/admin/transit/routes')
export const saveTransitRoute = (countryCode, payload) =>
  client.put(`/admin/transit/routes/${encodeURIComponent(countryCode)}`, payload)
export const getTransitProvisions = (params = {}) => client.get('/admin/transit/provisions', { params })
export const retryTransitOrder = (orderId) => client.post(`/admin/orders/${orderId}/retry-transit`)
export default client
