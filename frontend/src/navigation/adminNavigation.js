import {
  Activity, Box, CircleDollarSign, Database, MessageSquare, PackageSearch, Tags,
  ShieldCheck, UserCog, Users,
} from 'lucide-vue-next'

export function getAdminNavigation(role) {
  return [
    ['订单处理', '/admin', Box],
    ['订单中心', '/admin/orders', PackageSearch],
    ['财务中心', '/admin/finance', CircleDollarSign],
    ['客户管理', '/admin/customers', Users],
    ['商品与价格', '/admin/catalog', Tags],
    ['线路健康', '/admin/health', Activity],
    ['全量会话', '/admin/inbox', MessageSquare],
    ['资源池', '/admin/resource-pool', Database],
    ['安全中心', '/admin/security', ShieldCheck],
    ...(role === 'super_admin' ? [['销售管理', '/admin/sales-management', UserCog]] : []),
  ]
}
