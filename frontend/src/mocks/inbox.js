const conversations = [
  {
    id: 1001, customerId: 101, assignedSalesId: 11, subject: '香港直播线路偶发卡顿', customer: '星海直播', customerUsername: 'customer_demo', owner: '陈经理', ownerUsername: 'sales_demo',
    status: 'open', relatedType: 'line', relatedId: 701, relatedLabel: '香港直播专线 HK-09', unreadCustomer: 1, unreadStaff: 0, lastMessageAt: '2026-10-23T10:18:00+08:00',
    messages: [
      { id: 2001, senderRole: 'customer', senderName: '星海直播', content: '今天上午直播时出现两次短暂卡顿，麻烦协助确认线路情况。', createdAt: '2026-10-23T09:46:00+08:00' },
      { id: 2002, senderRole: 'sales', senderName: '陈经理', content: '已收到，我们正在核对这条线路的检测记录，有结果后会在这里同步。', createdAt: '2026-10-23T10:02:00+08:00' },
      { id: 2003, senderRole: 'admin', senderName: '技术支持', content: '请补充大致发生时间和使用的软件版本，便于进一步定位。', createdAt: '2026-10-23T10:18:00+08:00' },
    ],
  },
  {
    id: 1002, customerId: 101, assignedSalesId: 11, subject: '续费订单确认', customer: '星海直播', customerUsername: 'customer_demo', owner: '陈经理', ownerUsername: 'sales_demo',
    status: 'pending', relatedType: 'order', relatedId: 501, relatedLabel: '续费订单 ORD-20261022-031', unreadCustomer: 0, unreadStaff: 1, lastMessageAt: '2026-10-22T17:36:00+08:00',
    messages: [
      { id: 2004, senderRole: 'customer', senderName: '星海直播', content: '续费订单已经提交，请问什么时候可以确认？', createdAt: '2026-10-22T17:36:00+08:00' },
    ],
  },
  {
    id: 1003, customerId: 101, assignedSalesId: 11, subject: '线路备注修改完成', customer: '星海直播', customerUsername: 'customer_demo', owner: '陈经理', ownerUsername: 'sales_demo',
    status: 'pending', relatedType: 'line', relatedId: 702, relatedLabel: '新加坡社媒线路 SG-02', unreadCustomer: 0, unreadStaff: 0, lastMessageAt: '2026-10-19T14:22:00+08:00',
    messages: [
      { id: 2005, senderRole: 'customer', senderName: '星海直播', content: '请协助确认线路备注是否已同步。', createdAt: '2026-10-19T13:55:00+08:00' },
      { id: 2006, senderRole: 'sales', senderName: '陈经理', content: '已核对，备注修改已完成。', createdAt: '2026-10-19T14:22:00+08:00' },
    ],
  },
  {
    id: 1004, customerId: 102, assignedSalesId: 11, subject: '交付时间咨询', customer: '云帆电商', customerUsername: 'customer_yunfan', owner: '陈经理', ownerUsername: 'sales_demo',
    status: 'closed', relatedType: 'order', relatedId: 502, relatedLabel: '购买订单 ORD-20261017-018', unreadCustomer: 0, unreadStaff: 0, lastMessageAt: '2026-10-18T11:10:00+08:00',
    messages: [
      { id: 2007, senderRole: 'customer', senderName: '云帆电商', content: '请问订单预计何时交付？', createdAt: '2026-10-18T10:42:00+08:00' },
      { id: 2008, senderRole: 'sales', senderName: '陈经理', content: '订单已完成交付，请在我的线路中查看。', createdAt: '2026-10-18T11:10:00+08:00' },
    ],
  },
  {
    id: 1005, customerId: 103, assignedSalesId: 12, subject: '美国短视频线路开通咨询', customer: '远帆科技', customerUsername: 'customer_yuanfan', owner: '王经理', ownerUsername: 'sales_wang',
    status: 'open', relatedType: 'line', relatedId: 703, relatedLabel: '洛杉矶短视频线路 US-16', unreadCustomer: 0, unreadStaff: 2, lastMessageAt: '2026-10-23T09:28:00+08:00',
    messages: [
      { id: 2009, senderRole: 'customer', senderName: '远帆科技', content: '线路已经显示开通，但还没有收到使用说明。', createdAt: '2026-10-23T09:28:00+08:00' },
    ],
  },
]

const relatedOptions = {
  customer_demo: [
    { type: 'order', id: 501, label: '续费订单 ORD-20261022-031' },
    { type: 'order', id: 503, label: '购买订单 ORD-20261015-012' },
    { type: 'line', id: 701, label: '香港直播专线 HK-09' },
    { type: 'line', id: 702, label: '新加坡社媒线路 SG-02' },
  ],
}

const salesOptions = [
  { id: 11, name: '陈经理', username: 'sales_demo' },
  { id: 12, name: '王经理', username: 'sales_wang' },
]

const clone = (value) => JSON.parse(JSON.stringify(value))

export async function loadInboxFixture({ role, username, state = '' }) {
  await new Promise((resolve) => setTimeout(resolve, 420))
  if (state === 'error') throw new Error('演示会话加载失败，请重试')

  let scoped = conversations
  if (role === 'customer') scoped = conversations.filter((item) => item.customerUsername === username)
  if (role === 'sales') scoped = conversations.filter((item) => item.ownerUsername === username)
  if (state === 'empty') scoped = []

  return clone({
    generatedAt: '2026-10-23T10:20:00+08:00',
    conversations: scoped.sort((a, b) => new Date(b.lastMessageAt) - new Date(a.lastMessageAt)),
    relatedOptions: role === 'customer' ? (relatedOptions[username] || []) : [],
    salesOptions: ['admin', 'super_admin'].includes(role) ? salesOptions : [],
  })
}
