import { createRouter, createWebHistory } from 'vue-router'
import AppView from '../views/AppView.vue'
import WorkspaceView from '../views/WorkspaceView.vue'
import HealthView from '../views/HealthView.vue'
import InboxView from '../views/InboxView.vue'
import SupplierPoolView from '../views/SupplierPoolView.vue'
import ChangePasswordView from '../views/ChangePasswordView.vue'
import { ensureSession, currentSessionUser } from '../api'

const marketingRoutes = [
  ['/', 'home'], ['/products', 'products'], ['/products/:slug', 'product-detail'], ['/regions', 'regions'], ['/buy', 'buy'], ['/checkout', 'checkout'], ['/help', 'help'], ['/help/:slug', 'article'], ['/resources', 'resources'], ['/guide', 'resources'], ['/guide/:slug', 'article'], ['/resources/:slug', 'article'], ['/about', 'about'], ['/contact', 'contact'], ['/login', 'login'], ['/register', 'register'],
].map(([path, section]) => ({ path, component: AppView, meta: { section } }))

const workspaceRoute = (path, workspace, page) => ({ path, component: WorkspaceView, meta: { auth: true, workspace, page } })
const routes = [
  ...marketingRoutes,
  { path: '/change-password', component: ChangePasswordView, meta: { auth: true } },
  workspaceRoute('/client', 'client', 'dashboard'),
  workspaceRoute('/client/dashboard', 'client', 'dashboard'),
  workspaceRoute('/client/orders', 'client', 'orders'),
  workspaceRoute('/client/orders/:id', 'client', 'order'),
  workspaceRoute('/client/lines', 'client', 'lines'),
  workspaceRoute('/client/lines/:id', 'client', 'line'),
  workspaceRoute('/client/renewals', 'client', 'renewals'),
  workspaceRoute('/client/notifications', 'client', 'notifications'),
  { path: '/client/inbox', component: InboxView, meta: { auth: true, workspace: 'client', page: 'inbox' } },
  workspaceRoute('/client/profile', 'client', 'profile'),
  workspaceRoute('/sales', 'sales', 'sales'),
  { path: '/sales/health', component: HealthView, meta: { auth: true, workspace: 'sales', page: 'sales-health' } },
  { path: '/sales/inbox', component: InboxView, meta: { auth: true, workspace: 'sales', page: 'sales-inbox' } },
  workspaceRoute('/admin', 'admin', 'admin'),
  { path: '/admin/health', component: HealthView, meta: { auth: true, workspace: 'admin', page: 'admin-health' } },
  { path: '/admin/inbox', component: InboxView, meta: { auth: true, workspace: 'admin', page: 'admin-inbox' } },
  { path: '/admin/resource-pool', component: SupplierPoolView, meta: { auth: true, workspace: 'admin', page: 'admin-resource-pool' } },
  { path: '/:pathMatch(.*)*', component: AppView, meta: { section: '404' } },
]

const router = createRouter({ history: createWebHistory(), routes, scrollBehavior: () => ({ top: 0 }) })
const roleWorkspace = (role) => role === 'customer' ? 'client' : role === 'sales' ? 'sales' : ['admin', 'super_admin'].includes(role) ? 'admin' : null

router.beforeEach(async (to) => {
  if (!to.meta.auth) return true
  let user = currentSessionUser()
  if (!user) { try { user = await ensureSession() } catch { return { path: '/login', query: { redirect: to.fullPath } } } }
  if (user.must_change_password) return to.path === '/change-password' ? true : '/change-password'
  if (to.path === '/change-password') return roleWorkspace(user.role) ? `/${roleWorkspace(user.role)}` : '/login'
  const allowedWorkspace = roleWorkspace(user.role)
  if (to.meta.workspace !== allowedWorkspace) return allowedWorkspace ? `/${allowedWorkspace}` : '/login'
  return true
})

export default router
