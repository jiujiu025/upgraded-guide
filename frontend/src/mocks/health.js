const lines = [
  { id: 101, name: '香港边缘节点-01', region: '香港', product: '直播', ip: '203.0.113.18', status: 'offline', latency: null, lastChecked: '2026-10-23T09:42:00+08:00', affectedCustomers: 3, owner: '陈经理', ownerUsername: 'sales_demo', expiresAt: '2026-11-22T18:00:00+08:00', customers: ['星海直播', '云帆电商', '北辰传媒'], timeline: [{ time: '09:42', label: '连续 3 次探测失败', state: 'offline' }, { time: '09:39', label: '丢包率升至 68%', state: 'abnormal' }, { time: '09:35', label: '延迟 72ms，线路正常', state: 'normal' }], records: [{ time: '09:44', owner: '陈经理', action: '已通知受影响客户，等待节点排查' }] },
  { id: 102, name: '新加坡直播专线-03', region: '新加坡', product: '直播', ip: '198.51.100.24', status: 'abnormal', latency: 186, lastChecked: '2026-10-23T09:43:00+08:00', affectedCustomers: 2, owner: '陈经理', ownerUsername: 'sales_demo', expiresAt: '2026-10-30T18:00:00+08:00', customers: ['远帆科技', '峰岚文化'], timeline: [{ time: '09:43', label: '丢包率 12%，延迟 186ms', state: 'abnormal' }, { time: '09:38', label: '延迟 143ms', state: 'degraded' }, { time: '09:33', label: '延迟 69ms，线路正常', state: 'normal' }], records: [{ time: '09:45', owner: '陈经理', action: '已建立故障跟进记录' }] },
  { id: 103, name: '东京社媒节点-02', region: '日本', product: '社媒', ip: '192.0.2.44', status: 'degraded', latency: 142, lastChecked: '2026-10-23T09:41:00+08:00', affectedCustomers: 1, owner: '陈经理', ownerUsername: 'sales_demo', expiresAt: '2026-11-05T18:00:00+08:00', customers: ['青屿出海'], timeline: [{ time: '09:41', label: '延迟超过 140ms', state: 'degraded' }, { time: '09:36', label: '延迟 118ms', state: 'normal' }], records: [] },
  { id: 104, name: '洛杉矶短视频-06', region: '美国', product: '短视频', ip: '198.51.100.61', status: 'pending', latency: null, lastChecked: '2026-10-23T09:30:00+08:00', affectedCustomers: 0, owner: '王经理', ownerUsername: 'sales_wang', expiresAt: '2026-12-01T18:00:00+08:00', customers: [], timeline: [{ time: '09:30', label: '检测任务等待执行', state: 'pending' }], records: [] },
  { id: 105, name: '法兰克福住宅-01', region: '德国', product: '静态住宅 IP', ip: '192.0.2.87', status: 'normal', latency: 78, lastChecked: '2026-10-23T09:44:00+08:00', affectedCustomers: 0, owner: '王经理', ownerUsername: 'sales_wang', expiresAt: '2026-12-18T18:00:00+08:00', customers: [], timeline: [{ time: '09:44', label: '延迟 78ms，线路正常', state: 'normal' }, { time: '09:39', label: '延迟 81ms，线路正常', state: 'normal' }], records: [] },
  { id: 106, name: '首尔社媒节点-04', region: '韩国', product: '社媒', ip: '203.0.113.76', status: 'normal', latency: 52, lastChecked: '2026-10-23T09:44:00+08:00', affectedCustomers: 0, owner: '陈经理', ownerUsername: 'sales_demo', expiresAt: '2026-11-28T18:00:00+08:00', customers: [], timeline: [{ time: '09:44', label: '延迟 52ms，线路正常', state: 'normal' }, { time: '09:39', label: '延迟 49ms，线路正常', state: 'normal' }], records: [] },
  { id: 107, name: '伦敦短视频-02', region: '英国', product: '短视频', ip: '198.51.100.109', status: 'normal', latency: 91, lastChecked: '2026-10-23T09:43:00+08:00', affectedCustomers: 0, owner: '刘经理', ownerUsername: 'sales_liu', expiresAt: '2026-12-08T18:00:00+08:00', customers: [], timeline: [{ time: '09:43', label: '延迟 91ms，线路正常', state: 'normal' }], records: [] },
  { id: 108, name: '悉尼住宅-02', region: '澳大利亚', product: '静态住宅 IP', ip: '192.0.2.126', status: 'normal', latency: 104, lastChecked: '2026-10-23T09:42:00+08:00', affectedCustomers: 0, owner: '刘经理', ownerUsername: 'sales_liu', expiresAt: '2027-01-03T18:00:00+08:00', customers: [], timeline: [{ time: '09:42', label: '延迟 104ms，线路正常', state: 'normal' }], records: [] },
  { id: 109, name: '香港直播专线-07', region: '香港', product: '直播', ip: '203.0.113.142', status: 'normal', latency: 36, lastChecked: '2026-10-23T09:44:00+08:00', affectedCustomers: 0, owner: '陈经理', ownerUsername: 'sales_demo', expiresAt: '2026-12-24T18:00:00+08:00', customers: [], timeline: [{ time: '09:44', label: '延迟 36ms，线路正常', state: 'normal' }], records: [] },
  { id: 110, name: '新加坡短视频-08', region: '新加坡', product: '短视频', ip: '198.51.100.155', status: 'normal', latency: 64, lastChecked: '2026-10-23T09:43:00+08:00', affectedCustomers: 0, owner: '王经理', ownerUsername: 'sales_wang', expiresAt: '2026-11-19T18:00:00+08:00', customers: [], timeline: [{ time: '09:43', label: '延迟 64ms，线路正常', state: 'normal' }], records: [] },
]

const incidents = [
  { id: 201, lineId: 101, line: '香港边缘节点-01', region: '香港', product: '直播', startedAt: '2026-10-23T09:39:00+08:00', affectedCustomers: 3, owner: '陈经理', ownerUsername: 'sales_demo', status: '处理中', severity: 'offline' },
  { id: 202, lineId: 102, line: '新加坡直播专线-03', region: '新加坡', product: '直播', startedAt: '2026-10-23T09:38:00+08:00', affectedCustomers: 2, owner: '陈经理', ownerUsername: 'sales_demo', status: '待确认', severity: 'abnormal' },
  { id: 203, lineId: 103, line: '东京社媒节点-02', region: '日本', product: '社媒', startedAt: '2026-10-23T09:41:00+08:00', affectedCustomers: 1, owner: '陈经理', ownerUsername: 'sales_demo', status: '观察中', severity: 'degraded' },
]

const clone = (value) => JSON.parse(JSON.stringify(value))
const statusPriority = { offline: 0, abnormal: 1, degraded: 2, pending: 3, normal: 4 }
const buildStats = (items) => {
  const measured = items.filter((item) => Number.isFinite(item.latency))
  return {
    total: items.length,
    normal: items.filter((item) => item.status === 'normal').length,
    abnormal: items.filter((item) => ['abnormal', 'degraded'].includes(item.status)).length,
    offline: items.filter((item) => item.status === 'offline').length,
    averageLatency: measured.length ? Math.round(measured.reduce((sum, item) => sum + item.latency, 0) / measured.length) : null,
    affectedCustomers: new Set(items.flatMap((item) => item.customers)).size,
  }
}

const summarize = (items, key) => [...new Set(items.map((item) => item[key]))].map((name) => {
  const scoped = items.filter((item) => item[key] === name)
  return { name, total: scoped.length, normal: scoped.filter((item) => item.status === 'normal').length, issues: scoped.filter((item) => item.status !== 'normal').length }
})

export async function loadHealthFixture({ role, username, state = '' }) {
  await new Promise((resolve) => setTimeout(resolve, 420))
  if (state === 'error') throw new Error('演示数据加载失败，请重试')
  let scopedLines = role === 'sales' ? lines.filter((item) => item.ownerUsername === username) : lines
  let scopedIncidents = role === 'sales' ? incidents.filter((item) => item.ownerUsername === username) : incidents
  if (state === 'empty') { scopedLines = []; scopedIncidents = [] }
  const sortedLines = [...scopedLines].sort((a, b) => statusPriority[a.status] - statusPriority[b.status])
  return clone({
    generatedAt: '2026-10-23T09:45:00+08:00',
    stats: { ...buildStats(sortedLines), unresolved: scopedIncidents.filter((item) => item.status !== '已恢复').length },
    lines: sortedLines,
    incidents: scopedIncidents,
    regions: summarize(sortedLines, 'region'),
    products: summarize(sortedLines, 'product'),
  })
}
