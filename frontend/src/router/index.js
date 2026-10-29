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
import AdminLayout from '../layouts/AdminLayout.vue'
import ChangePasswordView from '../views/ChangePasswordView.vue'
import PasswordRecoveryView from '../views/PasswordRecoveryView.vue'
import { ensureSession, currentSessionUser } from '../api'

const marketingRoutes = [
  ['/', 'home'], ['/products', 'products'], ['/products/:slug', 'product-detail'], ['/regions', 'regions'], ['/buy', 'buy'], ['/checkout', 'checkout'], ['/help', 'help'], ['/help/:slug', 'article'], ['/resources', 'resources'], ['/guide', 'resources'], ['/guide/:slug', 'article'], ['/resources/:slug', 'article'], ['/about', 'about'], ['/contact', 'contact'], ['/login', 'login'], ['/register', 'register'],
].map(([path, section]) => ({ path, component: AppView, meta: { section } }))

const workspaceRoute = (path, workspace, page) => ({ path, component: WorkspaceView, meta: { auth: true, workspace, page } })
const routes = [
  ...marketingRoutes,
  { path: '/admin/login', component: AppView, meta: { section: 'admin-login', standalone: true, loginPortal: 'admin' } },
  { path: '/sales/login', component: AppView, meta: { section: 'sales-login', standalone: true, loginPortal: 'sales' } },
  { path: '/change-password', component: ChangePasswordView, meta: { auth: true } },
  { path: '/recover-password', component: PasswordRecoveryView },
  workspaceRoute('/client', 'client', 'dashboard'),
  { path: '/client/wallet', component: WalletView, meta: { auth: true, workspace: 'client', page: 'client-wallet' } },
  workspaceRoute('/client/dashboard', 'client', 'dashboard'),
  workspaceRoute('/client/orders', 'client', 'orders'),
  workspaceRoute('/client/orders/:id', 'client', 'order'),
  workspaceRoute('/client/lines', 'client', 'lines'),
  workspaceRoute('/client/lines/:id', 'client', 'line'),
  workspaceRoute('/client/renewals', 'client', 'renewals'),
  workspaceRoute('/client/notifications', 'client', 'notifications'),
  { path: '/client/inbox', component: InboxView, meta: { auth: true, workspace: 'client', page: 'inbox' } },
  workspaceRoute('/client/profile', 'client', 'profile'),
  workspaceRoute('/client/security', 'client', 'security-center'),
  workspaceRoute('/sales', 'sales', 'sales'),
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
      { path: 'sales-users', component: WorkspaceView, meta: { page: 'admin-sales-users' } },
      { path: 'security', component: WorkspaceView, meta: { page: 'security-center' } },
      { path: 'health', component: HealthView, meta: { page: 'admin-health' } },
      { path: 'inbox', component: InboxView, meta: { page: 'admin-inbox' } },
      { path: 'resource-pool', component: SupplierPoolView, meta: { page: 'admin-resource-pool' } },
      { path: 'orders', component: OrderCenterView, meta: { page: 'admin-orders' } },
      { path: 'finance', component: FinanceView, meta: { page: 'admin-finance' } },
      { path: 'customers', component: CustomerManagerView, meta: { page: 'admin-customers' } },
      { path: 'customers/:id', component: CustomerManagerView, meta: { page: 'admin-customers' } },
    ],
  },
  { path: '/:pathMatch(.*)*', component: AppView, meta: { section: '404' } },
]

const router = createRouter({ history: createWebHistory(), routes, scrollBehavior: () => ({ top: 0 }) })
const roleWorkspace = (role) => role === 'customer' ? 'client' : role === 'sales' ? 'sales' : ['admin', 'super_admin'].includes(role) ? 'admin' : null
const loginPath = (workspace) => workspace === 'admin' ? '/admin/login' : workspace === 'sales' ? '/sales/login' : '/login'

router.beforeEach(async (to) => {
  if (!to.meta.auth) {
    const user = currentSessionUser()
    if ((to.meta.loginPortal || ['/login', '/register'].includes(to.path)) && user) {
      const workspace = roleWorkspace(user.role)
      return workspace ? `/${workspace}` : true
    }
    return true
  }
  let user = currentSessionUser()
  if (!user) {
    try { user = await ensureSession() }
    catch { return { path: loginPath(to.meta.workspace), query: { redirect: to.fullPath } } }
  }
  if (to.path === '/change-password') return roleWorkspace(user.role) ? `/${roleWorkspace(user.role)}` : '/login'
  const allowedWorkspace = roleWorkspace(user.role)
  if (to.meta.workspace !== allowedWorkspace) return allowedWorkspace ? `/${allowedWorkspace}` : '/login'
  if (to.path === '/admin/sales-users' && user.role !== 'super_admin') return '/admin'
  return true
})

export default router
