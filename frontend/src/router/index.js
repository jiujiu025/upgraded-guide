import { createRouter, createWebHistory } from 'vue-router'
import AppView from '../views/AppView.vue'
import WorkspaceView from '../views/WorkspaceView.vue'
import HealthView from '../views/HealthView.vue'
import InboxView from '../views/InboxView.vue'
import SupplierPoolView from '../views/SupplierPoolView.vue'
import OrderCenterView from '../views/OrderCenterView.vue'
import FinanceView from '../views/FinanceView.vue'
import WalletView from '../views/WalletView.vue'
import CustomerManagerView from '../views/CustomerManagerView.vue'
import SalesManagementView from '../views/SalesManagementView.vue'
import CatalogManagementView from '../views/CatalogManagementView.vue'
import AdminTasksView from '../views/AdminTasksView.vue'
import AuditLogView from '../views/AuditLogView.vue'
import ProcurementRecordsView from '../views/ProcurementRecordsView.vue'
import TransitPanelsView from '../views/TransitPanelsView.vue'
import SystemLogsView from '../views/SystemLogsView.vue'
import NotificationChannelsView from '../views/NotificationChannelsView.vue'
import AdminLayout from '../layouts/AdminLayout.vue'
import ChangePasswordView from '../views/ChangePasswordView.vue'
import PasswordRecoveryView from '../views/PasswordRecoveryView.vue'
import { ensureSession, currentSessionUser } from '../api'
import { activePortal, loginPathForPortal, passwordPathForPortal, portalForPath, portalForRole } from '../api/session'

const marketingRoutes = [
  ['/', 'home'], ['/products', 'products'], ['/products/:slug', 'product-detail'], ['/regions', 'regions'], ['/buy', 'buy'], ['/checkout', 'checkout'], ['/help', 'help'], ['/help/:slug', 'article'], ['/resources', 'resources'], ['/guide', 'resources'], ['/guide/:slug', 'article'], ['/resources/:slug', 'article'], ['/about', 'about'], ['/contact', 'contact'], ['/login', 'login'], ['/register', 'register'],
].map(([path, section]) => ({ path, component: AppView, meta: { section } }))

const workspaceRoute = (path, workspace, page) => ({ path, component: WorkspaceView, meta: { auth: true, workspace, page } })
const routes = [
  ...marketingRoutes,
  { path: '/admin/login', component: AppView, meta: { section: 'admin-login', standalone: true, loginPortal: 'admin' } },
  { path: '/sales/login', component: AppView, meta: { section: 'sales-login', standalone: true, loginPortal: 'sales' } },
  { path: '/change-password', component: ChangePasswordView, meta: { auth: true, workspace: 'client' } },
  { path: '/recover-password', component: PasswordRecoveryView },
  workspaceRoute('/client', 'client', 'dashboard'),
  { path: '/client/wallet', component: WalletView, meta: { auth: true, workspace: 'client', page: 'client-wallet' } },
  workspaceRoute('/client/dashboard', 'client', 'dashboard'),
  workspaceRoute('/client/orders', 'client', 'orders'),
  workspaceRoute('/client/orders/:id', 'client', 'order'),
  workspaceRoute('/client/lines', 'client', 'lines'),
  workspaceRoute('/client/lines/:id', 'client', 'line'),
  workspaceRoute('/client/buy', 'client', 'client-buy'),
  workspaceRoute('/client/renewals', 'client', 'renewals'),
  workspaceRoute('/client/notifications', 'client', 'notifications'),
  { path: '/client/inbox', component: InboxView, meta: { auth: true, workspace: 'client', page: 'inbox' } },
  workspaceRoute('/client/profile', 'client', 'profile'),
  workspaceRoute('/client/security', 'client', 'security-center'),
  workspaceRoute('/sales', 'sales', 'sales'),
  // 销售端的业务页：与客户端同一套模板，数据走 /api/sales/*（后端按名下客户限权）
  workspaceRoute('/sales/customers', 'sales', 'sales-customers'),
  workspaceRoute('/sales/orders', 'sales', 'orders'),
  workspaceRoute('/sales/orders/:id', 'sales', 'order'),
  workspaceRoute('/sales/lines', 'sales', 'lines'),
  workspaceRoute('/sales/lines/:id', 'sales', 'line'),
  workspaceRoute('/sales/renewals', 'sales', 'renewals'),
  workspaceRoute('/sales/notifications', 'sales', 'notifications'),
  workspaceRoute('/sales/profile', 'sales', 'profile'),
  { path: '/sales/wallet', component: WalletView, meta: { auth: true, workspace: 'sales', page: 'sales-wallet' } },
  { path: '/sales/health', component: HealthView, meta: { auth: true, workspace: 'sales', page: 'sales-health' } },
  { path: '/sales/inbox', component: InboxView, meta: { auth: true, workspace: 'sales', page: 'sales-inbox' } },
  workspaceRoute('/sales/security', 'sales', 'security-center'),
  {
    path: '/admin',
    component: AdminLayout,
    meta: { auth: true, workspace: 'admin' },
    children: [
      { path: '', component: WorkspaceView, meta: { page: 'admin' } },
      { path: 'change-password', component: ChangePasswordView },
      { path: 'sales-management', component: SalesManagementView, meta: { page: 'admin-sales-management' } },
      { path: 'sales-users', redirect: '/admin/sales-management' },
      { path: 'security', component: WorkspaceView, meta: { page: 'security-center' } },
      { path: 'health', component: HealthView, meta: { page: 'admin-health' } },
      { path: 'tasks', component: AdminTasksView, meta: { page: 'admin-tasks' } },
      { path: 'audit', component: AuditLogView, meta: { page: 'admin-audit' } },
      { path: 'notifications', component: NotificationChannelsView, meta: { page: 'admin-notification-channels' } },
      { path: 'inbox', component: InboxView, meta: { page: 'admin-inbox' } },
      { path: 'resource-pool', component: SupplierPoolView, meta: { page: 'admin-resource-pool' } },
      { path: 'transit-panels', component: TransitPanelsView, meta: { page: 'admin-transit-panels' } },
      { path: 'procurements', component: ProcurementRecordsView, meta: { page: 'admin-procurements' } },
      { path: 'logs', component: SystemLogsView, meta: { page: 'admin-logs' } },
      { path: 'orders', component: OrderCenterView, meta: { page: 'admin-orders' } },
      { path: 'finance', component: FinanceView, meta: { page: 'admin-finance' } },
      { path: 'customers', component: CustomerManagerView, meta: { page: 'admin-customers' } },
      { path: 'customers/:id', component: CustomerManagerView, meta: { page: 'admin-customers' } },
      { path: 'catalog', component: CatalogManagementView, meta: { page: 'admin-catalog-management' } },
    ],
  },
  { path: '/sales/change-password', component: ChangePasswordView, meta: { auth: true, workspace: 'sales' } },
  { path: '/:pathMatch(.*)*', component: AppView, meta: { section: '404' } },
]

const router = createRouter({ history: createWebHistory(), routes, scrollBehavior: () => ({ top: 0 }) })

router.beforeEach(async (to) => {
  const portal = portalForPath(to.path)
  const isLogin = to.meta.loginPortal || ['/login', '/register'].includes(to.path)
  if (!to.meta.auth && !isLogin) return true

  let user = currentSessionUser(portal)
  if (!user) {
    try { user = await ensureSession(portal) }
    catch { /* 此入口没有可恢复的会话 */ }
  }
  if (!user || portalForRole(user.role) !== portal) {
    return isLogin ? true : { path: loginPathForPortal(portal), query: { redirect: to.fullPath } }
  }
  const passwordPath = passwordPathForPortal(portal)
  if (user.must_change_password && to.path !== passwordPath) return passwordPath
  if (isLogin) return portal === 'customer' ? '/client' : `/${portal}`
  if (to.path.startsWith('/admin/sales-management') && user.role !== 'super_admin') return '/admin'
  return true
})

router.afterEach((to, _from, failure) => {
  if (!failure) activePortal.value = portalForPath(to.path)
})

export default router
