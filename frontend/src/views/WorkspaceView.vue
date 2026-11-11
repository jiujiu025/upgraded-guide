<script setup>
import BrandLogo from "../components/BrandLogo.vue";
import OrderOnBehalfDialog from "../components/OrderOnBehalfDialog.vue";
import ClientBuyPanel from "../components/ClientBuyPanel.vue";
import DeliveryLinkInput from "../components/DeliveryLinkInput.vue";
import { computed, onMounted, reactive, ref, watch } from "vue";
import {
  Activity,
  Bell,
  Box,
  ChevronLeft,
  ChevronRight,
  CircleDollarSign,
  Database,
  Gauge,
  Inbox,
  LogOut,
  Menu,
  MessageSquare,
  PackageSearch,
  RefreshCw,
  RotateCw,
  Search,
  Server,
  ShieldCheck,
  ShieldOff,
  Settings,
  ShoppingCart,
  UserPlus,
  Users,
  X,
} from "lucide-vue-next";
import { useRoute, useRouter } from "vue-router";
import {
  cancelAdminOrder,
  cancelCustomerOrder,
  createSalesUser,
  completeAdminRenewal,
  confirmAdminOrder,
  deliverAdminOrder,
  getAdminOrders,
  getCustomerLine,
  getCustomerLines,
  getCustomerMe,
  getCustomerOrder,
  getCustomerOrders,
  getCustomerRenewals,
  getManagedLine,
  getNotifications,
  getSalesCustomers,
  getSalesDashboard,
  getSalesInviteCodes,
  createSalesInviteCode,
  disableSalesInviteCode,
  getSalesExpiringLines,
  getSalesLines,
  getSalesOrder,
  getSalesOrders,
  getSalesRenewals,
  renewSalesLine,
  getSalesOutreach,
  getSecurityQuestions,
  getMyNotificationChannels,
  toggleMyNotificationChannel,
  markAllNotificationsRead,
  markNotificationRead,
  recordRenewalTouch,
  renewCustomerLine,
  updateSecurityQuestions,
  updateCustomerLineNote,
} from "../api";
import { session } from "../api/session";
import { getAdminNavigation } from "../navigation/adminNavigation";
import { logout as endSession } from "../api";
import { loadHealthFixture } from "../mocks/health";

const route = useRoute();
const router = useRouter();
const loading = ref(true);
const busy = ref(false);
const error = ref("");
const success = ref("");
const mobileNav = ref(false);
const orders = ref([]);
const lines = ref([]);
const notifications = ref([]);
const renewals = ref([]);
const profile = ref(null);
const detail = ref(null);
const dashboard = ref(null);
const salesCustomers = ref([]);
const salesOrders = ref([]);
const expiringLines = ref([]);
const healthImpacts = ref([]);
const outreach = ref(null);
const outreachOpen = ref(false);
const outreachBusy = ref(0);
const touchForm = reactive({ lineId: null, channel: "wechat", outcome: "sent", note: "" });
// 我的邀请码：走谁的码注册，客户就归谁。
const inviteCodes = ref([]);
const inviteForm = reactive({ note: "" });
const inviteBusy = ref(false);
const inviteError = ref("");
const salesUserForm = reactive({ username: "", display_name: "", phone: "", password: "", confirmPassword: "" });
const securityQuestions = ref({ configured: false, question_1: null, question_2: null });
// 我的外部推送渠道：管理员绑定后，用户只能开/关，不能改推送目标（防止被改成别人的）。
const myChannels = ref([]);
const channelBusy = ref(0);
// 代客下单：销售只能给名下客户开单，折扣只有管理员能设（上限与后端保持一致，后端是唯一权威）。
const MAX_DISCOUNT_PERCENT = 30;
const onBehalfOpen = ref(false);
const onBehalfCustomer = ref(null);
const canSetDiscount = computed(() =>
  ["admin", "super_admin"].includes(storedUser.value.role),
);
const securityQuestionForm = reactive({ current_password: "", question_1: "", answer_1: "", question_2: "", answer_2: "" });
const securityQuestionOptions = [
  ["childhood_friend", "您童年最好的朋友叫什么？"],
  ["birth_city", "您出生的城市是哪里？"],
  ["first_school", "您上的第一所学校叫什么？"],
  ["favorite_teacher", "您最喜欢的老师叫什么？"],
  ["childhood_nickname", "您小时候的昵称是什么？"],
  ["first_job", "您的第一份工作是什么？"],
  ["favorite_book", "您最喜欢的一本书是什么？"],
  ["favorite_movie", "您最喜欢的一部电影是什么？"],
];
const orderStatus = ref("");
// 订单列表改成服务端分页后，这三样必须来自接口而不是"当页数据"：
// total 用来显示总数/页数，statusCounts 用来拼状态下拉框（当页只有 25 条，
// 用当页出现过的状态会让用户翻页后选不到老状态）。
const orderPage = ref(1);
const orderPageSize = 25;
const orderTotal = ref(0);
const orderStatusCounts = ref({});
const salesOrderTotal = ref(0);
const salesOrderStatusCounts = ref({});
const salesOrderPage = ref(1);
const salesOrderPageSize = 20;
// 销售端客户管理的搜索：在前端过滤，客户数量是"名下客户"，量级不大
const salesCustomerKeyword = ref("");
const filteredSalesCustomers = computed(() => {
  const keyword = salesCustomerKeyword.value.trim().toLowerCase();
  if (!keyword) return salesCustomers.value;
  return salesCustomers.value.filter((item) =>
    [item.name, item.email, item.phone].some((field) =>
      String(field || "").toLowerCase().includes(keyword),
    ),
  );
});
const orderPages = computed(() => Math.max(1, Math.ceil(orderTotal.value / orderPageSize)));
const salesOrderPages = computed(() => Math.max(1, Math.ceil(salesOrderTotal.value / salesOrderPageSize)));
const filters = reactive({ product: "", region: "", status: "", note: "" });
const editingLineId = ref(null);
const noteDraft = ref("");
const renewDraft = reactive({
  lineId: null,
  billing_days: 30,
  payment_method: "contact_manager",
});
// 续费档位由后端按商品实际可售周期返回，避免 90 天线路只能选 30/180/360。
const renewLine = computed(
  () => lines.value.find((item) => item.id === renewDraft.lineId) || null,
);
const renewPeriods = computed(() => {
  const periods = renewLine.value?.periods || [];
  if (periods.length) return periods;
  return [{ billing_days: renewLine.value?.billing_days || 30, unit_price: null }];
});
const renewUnitPrice = computed(() => {
  const period = renewPeriods.value.find(
    (item) => Number(item.billing_days) === Number(renewDraft.billing_days),
  );
  return period?.unit_price ?? null;
});
const walletPath = computed(() =>
  workspace.value === "sales" ? "/sales/wallet" : "/client/wallet",
);
const deliveryOrderId = ref(null);
const customerOrderCancellation = ref(null);
const orderActionConfirmation = ref(null);
const customerRenewalConfirmation = ref(null);
const deliveryConfirmation = ref(null);
const renewalConfirmation = ref(null);
const delivery = reactive({
  // 节点 ID 不再是必填：贴了链接后端会按链接自动关联或创建节点
  node_id: "",
  static_ip: "",
  expires_at: "",
  customer_note: "",
  node_link: "",
});

const workspace = computed(() => route.meta.workspace);
const page = computed(() => route.meta.page || "dashboard");
const storedUser = computed(() => {
  return session.user || {};
});
const clientNav = [
  ["首页", "/client", Gauge],
  ["账户余额", "/client/wallet", CircleDollarSign],
  ["购买线路", "/client/buy", ShoppingCart],
  ["我的订单", "/client/orders", PackageSearch],
  ["我的线路", "/client/lines", Server],
  ["续费管理", "/client/renewals", RotateCw],
  ["服务收件箱", "/client/inbox", MessageSquare],
  ["通知中心", "/client/notifications", Inbox],
  ["账户资料", "/client/profile", Settings],
  ["安全中心", "/client/security", ShieldCheck],
];
const nav = computed(() =>
  workspace.value === "client"
    ? clientNav
    : workspace.value === "sales"
      ? [
          // 客户有的功能销售也要有：订单、线路、续费、通知、资料全部对齐，
          // 差别只在数据范围（后端按名下客户限权），不在界面。
          ["销售概览", "/sales", Gauge],
          ["客户管理", "/sales/customers", Users],
          ["订单管理", "/sales/orders", PackageSearch],
          ["客户线路", "/sales/lines", Server],
          ["续费管理", "/sales/renewals", RotateCw],
          ["客户会话", "/sales/inbox", MessageSquare],
          ["我的钱包", "/sales/wallet", CircleDollarSign],
          ["客户影响", "/sales/health", Activity],
          ["通知中心", "/sales/notifications", Inbox],
          ["账户资料", "/sales/profile", Settings],
          ["安全中心", "/sales/security", ShieldCheck],
        ]
      : [
          ...getAdminNavigation(storedUser.value.role),
        ],
);
const titles = {
  dashboard: "客户首页",
  "client-buy": "购买线路",
  "client-wallet": "账户余额",
  "sales-wallet": "我的钱包",
  "admin-finance": "财务中心",
  orders: "我的订单",
  order: "订单详情",
  lines: "我的线路",
  line: "线路详情",
  renewals: "续费管理",
  notifications: "通知中心",
  profile: "账户资料",
  sales: "销售工作台",
  admin: "管理员订单",
  "admin-sales-users": "创建销售账号",
  "security-center": "安全中心",
};
// 同一个 page 在客户和销售下叫法不同（"我的订单" vs "客户订单"）。
const salesTitles = {
  orders: "客户订单",
  order: "订单详情",
  lines: "客户线路",
  line: "线路详情",
  renewals: "续费管理",
  notifications: "通知中心",
  profile: "账户资料",
  "sales-customers": "客户管理",
};
const pageTitle = computed(() =>
  (workspace.value === "sales" ? salesTitles[page.value] : null) ||
  titles[page.value] ||
  "工作台",
);
const securityQuestionLabel = (id) => securityQuestionOptions.find(([key]) => key === id)?.[1] || "—";
const unreadCount = computed(
  () => notifications.value.filter((item) => !item.is_read).length,
);
const orderStatusOptions = computed(() =>
  Object.keys(orderStatusCounts.value).sort(),
);
const salesOrderStatusOptions = computed(() =>
  Object.keys(salesOrderStatusCounts.value).sort(),
);
const filteredLines = computed(() =>
  lines.value.filter(
    (item) =>
      (!filters.product || item.product_type === filters.product) &&
      (!filters.region || item.region === filters.region) &&
      (!filters.status || item.status === filters.status) &&
      (!filters.note ||
        (item.customer_note || "")
          .toLowerCase()
          .includes(filters.note.toLowerCase())),
  ),
);
const lineProducts = computed(() => [
  ...new Set(lines.value.map((item) => item.product_type)),
]);
const lineRegions = computed(() => [
  ...new Set(lines.value.map((item) => item.region)),
]);
const activeLines = computed(
  () => lines.value.filter((item) => item.status === "active").length,
);
const abnormalLines = computed(
  () => lines.value.filter((item) => item.status !== "active").length,
);
const expiringCount = computed(
  () =>
    lines.value.filter(
      (item) =>
        daysLeft(item.expires_at) >= 0 && daysLeft(item.expires_at) <= 7,
    ).length,
);
// 看板徽标必须用全量状态分布算：分页后 orders 只有当页，按当页统计会漏掉
// 第 2 页上的待处理订单。
const pendingOrders = computed(() =>
  Object.entries(orderStatusCounts.value)
    .filter(
      ([status]) =>
        !["delivered", "completed", "cancelled", "refunded"].includes(status),
    )
    .reduce((total, [, count]) => total + count, 0),
);

const statusText = {
  unpaid: "未支付",
  manually_confirmed: "人工确认",
  pending: "待处理",
  pending_payment: "待支付",
  pending_confirm: "待确认",
  paid: "已支付",
  processing: "处理中",
  pending_delivery: "待交付",
  partially_delivered: "部分交付",
  delivered: "已交付",
  completed: "已完成",
  renewal: "续费中",
  cancelled: "已取消",
  refunding: "退款中",
  refunded: "已退款",
  active: "正常",
  expired: "已到期",
  disabled: "已停用",
};
const paymentText = {
  balance: "余额支付",
  online: "在线支付",
  contact_manager: "联系经理确认",
};
const money = (value) => `¥${Number(value || 0).toFixed(2)}`;
const dateTime = (value) =>
  value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "—";
function daysLeft(value) {
  return Math.ceil((new Date(value).getTime() - Date.now()) / 86400000);
}
// 订单出参里没有支付单明细，只能用"未付款 + 走在线/余额通道"做近似判断：
// 这类订单可能已经在支付通道里，取消会被后端拒绝并提示先确认支付结果。
function paymentMayBeInFlight(order) {
  return (
    Boolean(order) &&
    order.status === "pending_payment" &&
    order.payment_status === "unpaid" &&
    ["online", "balance"].includes(order.payment_method)
  );
}
function showSuccess(message) {
  success.value = message;
  setTimeout(() => {
    success.value = "";
  }, 2800);
}
const visibleOutreach = computed(() => {
  const items = outreach.value?.items || [];
  return outreachOpen.value ? items : items.slice(0, 6);
});
async function loadOutreach() {
  // 单独加载：触达清单挂了不该让整个销售工作台白屏。
  try {
    outreach.value = (await getSalesOutreach({ days: 30 })).data;
  } catch {
    outreach.value = null;
  }
}
function outreachTone(days) {
  if (days <= 0) return "bad";
  if (days <= 7) return "warn";
  return "muted";
}
function outreachDaysLabel(days) {
  if (days < 0) return `已过期 ${-days} 天`;
  if (days === 0) return "今天到期";
  return `剩 ${days} 天`;
}
async function copyOutreach(item) {
  const text = item.suggested_message;
  try {
    await navigator.clipboard.writeText(text);
    showSuccess(`已复制 ${item.customer_name} 的续费文案`);
  } catch {
    // 非 HTTPS 或浏览器拒绝授权时如实说，不假装复制成功。
    showSuccess("复制失败，请手动选中文案复制");
  }
}
async function loadInviteCodes() {
  try {
    inviteCodes.value = (await getSalesInviteCodes()).data;
  } catch {
    inviteCodes.value = [];
  }
}
async function createInviteCode() {
  inviteBusy.value = true;
  inviteError.value = "";
  try {
    await createSalesInviteCode({
      note: inviteForm.note.trim() || null,
    });
    inviteForm.note = "";
    showSuccess("邀请码已生成，复制注册链接发给客户吧");
    await loadInviteCodes();
  } catch (requestError) {
    inviteError.value = requestError.userMessage || requestError.message || "生成邀请码失败，请稍后重试";
  } finally {
    inviteBusy.value = false;
  }
}
async function disableInviteCode(item) {
  inviteBusy.value = true;
  inviteError.value = "";
  try {
    await disableSalesInviteCode(item.id);
    showSuccess(`邀请码 ${item.code} 已停用`);
    await loadInviteCodes();
  } catch (requestError) {
    inviteError.value = requestError.userMessage || requestError.message || "停用失败，请稍后重试";
  } finally {
    inviteBusy.value = false;
  }
}
async function copyInviteLink(item) {
  const link = item.register_url || `${window.location.origin}/register?invite=${item.code}`;
  try {
    await navigator.clipboard.writeText(link);
    showSuccess("注册链接已复制，粘贴到微信即可");
  } catch {
    // 非 HTTPS 下 clipboard 不可用：直接展示链接让销售手动复制。
    inviteError.value = `复制失败，请手动复制：${link}`;
  }
}
function startTouch(item) {
  touchForm.lineId = item.line_id;
  touchForm.channel = "wechat";
  touchForm.outcome = "sent";
  touchForm.note = "";
}
async function saveTouch(item) {
  outreachBusy.value = item.line_id;
  try {
    await recordRenewalTouch(item.line_id, {
      channel: touchForm.channel,
      outcome: touchForm.outcome,
      message: item.suggested_message,
      note: touchForm.note || null,
    });
    touchForm.lineId = null;
    showSuccess("已记录本次触达");
    await loadOutreach();
  } catch (err) {
    error.value = err.userMessage || "触达记录保存失败";
  } finally {
    outreachBusy.value = 0;
  }
}
async function logout() {
  try {
    await endSession();
  } finally {
    router.replace(workspace.value === "admin" ? "/admin/login" : workspace.value === "sales" ? "/sales/login" : "/login");
  }
}

async function loadClientBase() {
  const [
    orderResponse,
    lineResponse,
    notificationResponse,
    renewalResponse,
    profileResponse,
  ] = await Promise.all([
    getCustomerOrders({
      page: orderPage.value,
      page_size: orderPageSize,
      status: orderStatus.value || undefined,
    }),
    getCustomerLines(),
    getNotifications(),
    getCustomerRenewals(),
    getCustomerMe(),
  ]);
  applyOrderPage(orderResponse.data);
  lines.value = lineResponse.data;
  notifications.value = notificationResponse.data;
  renewals.value = renewalResponse.data;
  profile.value = profileResponse.data;
}

/**
 * 销售业务页的数据：与客户端同结构，只是接口换成 /api/sales/*。
 * 后端按名下客户限权，越界返回 404，所以前端不需要再过滤一遍。
 */
async function loadSalesBase() {
  const [orderResponse, lineResponse, notificationResponse, renewalResponse, customerResponse] =
    await Promise.all([
      getSalesOrders({
        page: orderPage.value,
        page_size: orderPageSize,
        status: orderStatus.value || undefined,
      }),
      getSalesLines(),
      getNotifications(),
      getSalesRenewals(),
      getSalesCustomers(),
    ]);
  applyOrderPage(orderResponse.data);
  lines.value = lineResponse.data;
  notifications.value = notificationResponse.data;
  renewals.value = renewalResponse.data;
  salesCustomers.value = customerResponse.data;
}

// 三个角色共用一套分页状态：同一时刻只渲染一个工作台，所以订单状态筛选
// 不需要按角色拆成三份。
function applyOrderPage(payload) {
  orders.value = payload.items || [];
  orderTotal.value = payload.total || 0;
  orderStatusCounts.value = payload.status_counts || {};
}

function applySalesOrderPage(payload) {
  salesOrders.value = payload.items || [];
  salesOrderTotal.value = payload.total || 0;
  salesOrderStatusCounts.value = payload.status_counts || {};
}

const orderPageParams = (page, pageSize) => ({
  page,
  page_size: pageSize,
  status: orderStatus.value || undefined,
});

// 翻页和切状态只重拉订单，不重拉线路/通知——那些请求跟订单页没关系。
async function loadOrders() {
  try {
    if (workspace.value === "sales") {
      const { data } = await getSalesOrders(
        orderPageParams(salesOrderPage.value, salesOrderPageSize),
      );
      applySalesOrderPage(data);
    } else if (workspace.value === "admin") {
      const { data } = await getAdminOrders(
        orderPageParams(orderPage.value, orderPageSize),
      );
      applyOrderPage(data);
    } else {
      const { data } = await getCustomerOrders(
        orderPageParams(orderPage.value, orderPageSize),
      );
      applyOrderPage(data);
    }
  } catch (requestError) {
    error.value = requestError.userMessage || "订单加载失败，请稍后重试";
  }
}

function changeOrderPage(next) {
  orderPage.value = next;
  loadOrders();
}

function changeSalesOrderPage(next) {
  salesOrderPage.value = next;
  loadOrders();
}

watch(orderStatus, () => {
  orderPage.value = 1;
  salesOrderPage.value = 1;
  loadOrders();
});

// 切换页面时回到第 1 页：否则从"我的订单第 3 页"点回看板，"最近订单"会渲染第 3 页的头 5 条。
watch(page, () => {
  orderPage.value = 1;
  salesOrderPage.value = 1;
});

async function load() {
  loading.value = true;
  error.value = "";
  detail.value = null;
  try {
    if (page.value === "security-center") {
      const [questionResponse, channelResponse] = await Promise.all([
        getSecurityQuestions(),
        getMyNotificationChannels().catch(() => ({ data: [] })),
      ]);
      securityQuestions.value = questionResponse.data;
      myChannels.value = channelResponse.data || [];
    } else if (workspace.value === "client") {
      await loadClientBase();
      if (page.value === "order")
        detail.value = (await getCustomerOrder(route.params.id)).data;
      if (page.value === "line")
        detail.value = (await getCustomerLine(route.params.id)).data;
    } else if (workspace.value === "sales" && page.value !== "sales") {
      // 业务页：订单/线路/续费/通知/客户管理共用客户的模板，数据换成销售接口
      await loadSalesBase();
      if (page.value === "order") {
        detail.value = (await getSalesOrder(route.params.id)).data;
      } else if (page.value === "line") {
        const found = lines.value.find((item) => String(item.id) === String(route.params.id));
        if (!found) throw new Error("线路不存在或无权访问");
        detail.value = found;
      }
    } else if (workspace.value === "sales") {
      const [
        dashboardResponse,
        customerResponse,
        orderResponse,
        expiringResponse,
        healthFixture,
        inviteResponse,
      ] = await Promise.all([
        getSalesDashboard(),
        getSalesCustomers(),
        getSalesOrders(
          orderPageParams(salesOrderPage.value, salesOrderPageSize),
        ),
        getSalesExpiringLines(),
        loadHealthFixture({
          role: "sales",
          username: storedUser.value.username,
        }),
        getSalesInviteCodes().catch(() => ({ data: [] })),
      ]);
      dashboard.value = dashboardResponse.data;
      salesCustomers.value = customerResponse.data;
      applySalesOrderPage(orderResponse.data);
      expiringLines.value = expiringResponse.data;
      inviteCodes.value = inviteResponse.data;
      healthImpacts.value = healthFixture.incidents.map((item) => ({
        incident_id: item.id,
        line_name: item.line,
        reason: `演示数据 · ${item.region} ${item.product}`,
        status: item.status,
      }));
      loadOutreach();
    } else if (page.value === "admin-sales-users") {
      orders.value = [];
    } else {
      applyOrderPage((await getAdminOrders(orderPageParams(orderPage.value, orderPageSize))).data);
    }
  } catch (requestError) {
    error.value = requestError.userMessage || "数据加载失败，请稍后重试";
  } finally {
    loading.value = false;
  }
}

async function submitSalesUser() {
  if (storedUser.value.role !== "super_admin" || busy.value) return;
  error.value = "";
  success.value = "";
  if (salesUserForm.password !== salesUserForm.confirmPassword) {
    error.value = "两次输入的初始密码不一致";
    return;
  }
  busy.value = true;
  try {
    const { data } = await createSalesUser({
      username: salesUserForm.username.trim(),
      display_name: salesUserForm.display_name.trim(),
      phone: salesUserForm.phone.trim() || null,
      password: salesUserForm.password,
    });
    success.value = `销售账号 ${data.username} 已创建，可以使用初始密码登录`;
    salesUserForm.username = "";
    salesUserForm.display_name = "";
    salesUserForm.phone = "";
    salesUserForm.password = "";
    salesUserForm.confirmPassword = "";
  } catch (requestError) {
    error.value = requestError.userMessage || "创建销售账号失败，请重试";
  } finally {
    busy.value = false;
  }
}

async function saveSecurityQuestions() {
  if (busy.value) return;
  if (securityQuestionForm.question_1 === securityQuestionForm.question_2) {
    error.value = "请选择两个不同的密保问题";
    return;
  }
  busy.value = true;
  error.value = "";
  success.value = "";
  try {
    await updateSecurityQuestions({ ...securityQuestionForm });
    securityQuestions.value = (await getSecurityQuestions()).data;
    securityQuestionForm.current_password = "";
    securityQuestionForm.answer_1 = "";
    securityQuestionForm.answer_2 = "";
    showSuccess("密保问题已安全保存");
  } catch (requestError) {
    error.value = requestError.userMessage || "保存密保问题失败，请重试";
  } finally {
    busy.value = false;
  }
}

function openOnBehalf(customer) {
  onBehalfCustomer.value = customer;
  onBehalfOpen.value = true;
}

async function handleOnBehalfCreated(order) {
  showSuccess(`订单 ${order.order_no} 已创建，金额 ${money(order.total_amount)}`);
  if (workspace.value === "sales") {
    salesOrderPage.value = 1;
    await loadOrders();
  }
}

async function toggleMyChannel(binding) {
  if (channelBusy.value) return;
  channelBusy.value = binding.id;
  error.value = "";
  try {
    const { data } = await toggleMyNotificationChannel(binding.id);
    const index = myChannels.value.findIndex((item) => item.id === binding.id);
    if (index >= 0) myChannels.value[index] = data;
    showSuccess(data.is_enabled ? "已开启该渠道的推送" : "已关闭该渠道的推送");
  } catch (requestError) {
    error.value = requestError.userMessage || "操作失败，请稍后重试";
  } finally {
    channelBusy.value = 0;
  }
}

async function runAction(action, message) {
  busy.value = true;
  error.value = "";
  try {
    await action();
    showSuccess(message);
    await load();
    return true;
  } catch (requestError) {
    error.value = requestError.userMessage || "操作失败，请稍后重试";
    return false;
  } finally {
    busy.value = false;
  }
}
function openCustomerOrderCancellation(order) {
  if (workspace.value !== "client" || busy.value) return;
  error.value = "";
  customerOrderCancellation.value = order;
}

async function submitCustomerOrderCancellation() {
  const order = customerOrderCancellation.value;
  if (!order || busy.value) return;
  busy.value = true;
  error.value = "";
  try {
    await cancelCustomerOrder(order.id);
    customerOrderCancellation.value = null;
    showSuccess("订单已取消");
    await load();
  } catch (requestError) {
    error.value = requestError.userMessage || "订单取消失败，请刷新后重试";
  } finally {
    busy.value = false;
  }
}
function openAdminOrderAction(order, action) {
  if (workspace.value !== "admin" || busy.value) return;
  error.value = "";
  orderActionConfirmation.value = { order, action };
}

async function submitAdminOrderAction() {
  const pending = orderActionConfirmation.value;
  if (!pending || busy.value) return;
  busy.value = true;
  error.value = "";
  try {
    if (pending.action === "confirm") {
      await confirmAdminOrder(pending.order.id);
    } else if (pending.action === "cancel") {
      await cancelAdminOrder(pending.order.id);
    } else {
      throw new Error("unsupported-admin-order-action");
    }
    const message = pending.action === "confirm" ? "订单已确认" : "订单已取消";
    orderActionConfirmation.value = null;
    showSuccess(message);
    await load();
  } catch (requestError) {
    error.value =
      requestError.userMessage ||
      (pending.action === "confirm"
        ? "订单确认失败，请刷新后重试"
        : "订单取消失败，请刷新后重试");
  } finally {
    busy.value = false;
  }
}
const canCompleteRenewal = (order) =>
  order.order_type === "renewal" &&
  ["renewal", "pending_payment"].includes(order.status) &&
  Boolean(order.renewal_line_id) &&
  !order.renewal_completed_at;

const detailPayable = computed(
  () =>
    workspace.value !== "admin" &&
    Boolean(detail.value) &&
    ["pending_payment", "renewal"].includes(detail.value.status),
);

function openRenewalConfirmation(order) {
  if (!canCompleteRenewal(order) || workspace.value !== "admin") return;
  error.value = "";
  renewalConfirmation.value = order;
}

async function submitRenewalCompletion() {
  const order = renewalConfirmation.value;
  if (!order || !canCompleteRenewal(order) || busy.value) return;
  busy.value = true;
  error.value = "";
  try {
    const completed = (await completeAdminRenewal(order.id)).data;
    const localIndex = orders.value.findIndex(
      (item) => item.id === completed.id,
    );
    if (localIndex >= 0) orders.value[localIndex] = completed;
    renewalConfirmation.value = null;
    try {
      const [orderResponse, lineResponse] = await Promise.all([
        getAdminOrders(orderPageParams(orderPage.value, orderPageSize)),
        getManagedLine(order.renewal_line_id),
      ]);
      applyOrderPage(orderResponse.data);
      showSuccess(
        `续费已人工完成，线路到期时间已更新至 ${dateTime(lineResponse.data.expires_at)}`,
      );
    } catch (refreshError) {
      showSuccess("续费已人工完成");
      error.value = `订单已完成，但最新线路信息刷新失败：${refreshError.userMessage || "请手动刷新后确认"}`;
    }
  } catch (requestError) {
    error.value = requestError.userMessage || "续费完成失败，请刷新订单后重试";
  } finally {
    busy.value = false;
  }
}

function beginNote(line) {
  editingLineId.value = line.id;
  noteDraft.value = line.customer_note || "";
}
async function saveNote(line) {
  const succeeded = await runAction(
    () =>
      updateCustomerLineNote(line.id, {
        customer_note: noteDraft.value.trim() || null,
      }),
    "备注已保存",
  );
  if (succeeded) editingLineId.value = null;
}
function beginRenew(line) {
  renewDraft.lineId = line.id;
  const periods = line.periods?.length ? line.periods : [];
  renewDraft.billing_days = periods.length
    ? Number(periods[0].billing_days)
    : line.billing_days || 30;
  renewDraft.payment_method = "contact_manager";
}
async function submitRenew() {
  const line = lines.value.find((item) => item.id === renewDraft.lineId);
  if (!line || busy.value) return;
  error.value = "";
  customerRenewalConfirmation.value = {
    line,
    billing_days: Number(renewDraft.billing_days),
    payment_method: renewDraft.payment_method,
    unit_price: renewUnitPrice.value,
  };
}

async function confirmCustomerRenewal() {
  const pending = customerRenewalConfirmation.value;
  if (!pending || busy.value) return;
  // 销售代客户续费：走 /sales/lines/{id}/renew，金额口径与客户自助完全一致；
  // 也不能替客户用余额/在线付款，只能落到「联系经理确认」。
  const forSales = workspace.value === "sales";
  const needsPayment = !forSales && pending.payment_method !== "contact_manager";
  const succeeded = await runAction(
    () =>
      (forSales ? renewSalesLine : renewCustomerLine)(pending.line.id, {
        billing_days: pending.billing_days,
        payment_method: pending.payment_method,
      }),
    forSales
      ? "续费订单已创建（代客），等待管理员确认完成"
      : needsPayment
        ? "续费订单已创建，正在前往账户余额完成支付"
        : "续费订单已创建，等待管理员确认完成",
  );
  if (succeeded) {
    customerRenewalConfirmation.value = null;
    renewDraft.lineId = null;
    if (needsPayment) router.push(walletPath.value);
  }
}
async function readNotification(item) {
  if (item.is_read) return;
  try {
    await markNotificationRead(item.id);
    item.is_read = true;
  } catch (requestError) {
    error.value = requestError.userMessage;
  }
}
async function readAll() {
  await runAction(markAllNotificationsRead, "全部通知已标记为已读");
}
function openDelivery(order) {
  error.value = "";
  deliveryOrderId.value = order.id;
  Object.assign(delivery, {
    node_id: "",
    static_ip: "",
    expires_at: "",
    customer_note: "",
    node_link: "",
  });
}
async function submitDelivery() {
  if (!delivery.expires_at) {
    error.value = "请填写到期时间";
    return;
  }
  if (!delivery.node_link.trim() && !delivery.node_id) {
    error.value = "请粘贴节点链接，或指定已有节点 ID";
    return;
  }
  const expiresAt = new Date(delivery.expires_at);
  if (Number.isNaN(expiresAt.getTime()) || expiresAt.getTime() <= Date.now()) {
    error.value = "到期时间必须晚于当前时间";
    return;
  }
  const payload = {
    node_id: delivery.node_id ? Number(delivery.node_id) : null,
    expires_at: expiresAt.toISOString(),
  };
  for (const key of ["static_ip", "customer_note", "node_link"])
    payload[key] = delivery[key].trim() || null;
  const order = orders.value.find((item) => item.id === deliveryOrderId.value);
  if (!order) {
    error.value = "订单已变化，请刷新后重试";
    return;
  }
  error.value = "";
  deliveryConfirmation.value = { order, payload };
}

async function confirmDelivery() {
  const pending = deliveryConfirmation.value;
  if (!pending || busy.value) return;
  busy.value = true;
  error.value = "";
  try {
    const deliveredLine = (
      await deliverAdminOrder(pending.order.id, pending.payload)
    ).data;
    deliveryConfirmation.value = null;
    deliveryOrderId.value = null;
    try {
      const [orderResponse, lineResponse] = await Promise.all([
        getAdminOrders(orderPageParams(orderPage.value, orderPageSize)),
        getManagedLine(deliveredLine.id),
      ]);
      applyOrderPage(orderResponse.data);
      showSuccess(
        `线路已交付，到期时间为 ${dateTime(lineResponse.data.expires_at)}`,
      );
    } catch (refreshError) {
      showSuccess("线路已交付");
      error.value = `交付已完成，但最新订单或线路刷新失败：${refreshError.userMessage || "请手动刷新后确认"}`;
    }
  } catch (requestError) {
    error.value = requestError.userMessage || "线路交付失败，请核对信息后重试";
  } finally {
    busy.value = false;
  }
}

watch(() => route.fullPath, load);
onMounted(load);
</script>

<template>
  <div class="workspace-shell">
    <aside class="workspace-sidebar" :class="{ open: mobileNav }">
      <div class="workspace-brand">
        <BrandLogo eager /><button aria-label="关闭导航" @click="mobileNav = false">
          <X :size="20" />
        </button>
      </div>
      <p class="workspace-role">
        {{
          workspace === "client"
            ? "客户中心"
            : workspace === "sales"
              ? "销售工作台"
              : "管理后台"
        }}
      </p>
      <nav>
        <button
          v-for="[label, path, icon] in nav"
          :key="path"
          :class="{
            active:
              route.path === path ||
              (path !== '/client' && route.path.startsWith(path)),
          }"
          @click="
            router.push(path);
            mobileNav = false;
          "
        >
          <component :is="icon" :size="17" />{{ label }}
        </button>
      </nav>
      <div class="workspace-user">
        <span>{{ storedUser.display_name || storedUser.username }}</span
        ><small>{{ storedUser.role }}</small
        ><button @click="logout"><LogOut :size="15" />退出登录</button>
      </div>
    </aside>
    <div class="workspace-main">
      <header class="workspace-header">
        <button
          class="workspace-menu"
          aria-label="打开导航"
          @click="mobileNav = true"
        >
          <Menu :size="21" />
        </button>
        <div>
          <p>
            {{
              workspace === "client"
                ? "CLIENT CENTER"
                : workspace === "sales"
                  ? "SALES OPERATIONS"
                  : "ORDER OPERATIONS"
            }}
          </p>
          <h1>{{ pageTitle }}</h1>
        </div>
        <button
          v-if="workspace === 'client'"
          class="notification-button"
          aria-label="打开通知中心"
          @click="router.push('/client/notifications')"
        >
          <Bell :size="19" /><b v-if="unreadCount">{{ unreadCount }}</b>
        </button>
      </header>
      <main class="workspace-content">
        <div v-if="success" class="workspace-toast">{{ success }}</div>
        <OrderOnBehalfDialog
          :open="onBehalfOpen"
          :customer="onBehalfCustomer"
          :can-discount="canSetDiscount"
          :max-discount-percent="MAX_DISCOUNT_PERCENT"
          @close="onBehalfOpen = false"
          @created="handleOnBehalfCreated"
        />
        <div v-if="loading" class="workspace-state">
          <span class="spinner"></span>
          <p>正在加载业务数据…</p>
        </div>
        <div
          v-else-if="error && !orders.length && !lines.length && !dashboard"
          class="workspace-state error-state"
        >
          <p>{{ error }}</p>
          <button class="outline-action" @click="load">
            <RefreshCw :size="15" />重试
          </button>
        </div>
        <template v-else>
          <div v-if="error" class="workspace-alert">
            <span>{{ error }}</span
            ><button
              v-if="
                workspace === 'client' &&
                ['lines', 'line', 'renewals'].includes(page)
              "
              @click="load"
            >
              <RefreshCw :size="14" />重试</button
            ><button @click="error = ''">关闭</button>
          </div>
          <template v-if="page === 'dashboard'"
            ><section class="metric-grid">
              <article>
                <span>当前线路</span><strong>{{ lines.length }}</strong
                ><small>{{ activeLines }} 条正常</small>
              </article>
              <article>
                <span>异常线路</span><strong>{{ abnormalLines }}</strong
                ><small>仅统计接口返回状态</small>
              </article>
              <article>
                <span>7 天内到期</span><strong>{{ expiringCount }}</strong
                ><small>请及时处理续费</small>
              </article>
              <article>
                <span>待处理订单</span><strong>{{ pendingOrders }}</strong
                ><small>含待支付与待确认</small>
              </article>
            </section>
            <div class="workspace-columns">
              <section class="workspace-panel">
                <div class="panel-head">
                  <h2>最近订单</h2>
                  <button @click="router.push('/client/orders')">
                    查看全部 <ChevronRight :size="15" />
                  </button>
                </div>
                <div v-if="!orders.length" class="inline-empty">暂无订单，<button type="button" class="link-action" @click="router.push('/client/buy')">去购买线路</button></div>
                <button
                  v-for="order in orders.slice(0, 5)"
                  :key="order.id"
                  class="compact-row"
                  @click="router.push(`/client/orders/${order.id}`)"
                >
                  <span
                    ><b>{{ order.product_name }}</b
                    ><small>{{ order.order_no }}</small></span
                  ><em :data-status="order.status">{{
                    statusText[order.status] || order.status
                  }}</em>
                </button>
              </section>
              <section class="workspace-panel">
                <div class="panel-head">
                  <h2>最近通知</h2>
                  <button @click="router.push('/client/notifications')">
                    进入收件箱 <ChevronRight :size="15" />
                  </button>
                </div>
                <div v-if="!notifications.length" class="inline-empty">
                  暂无通知
                </div>
                <button
                  v-for="item in notifications.slice(0, 5)"
                  :key="item.id"
                  class="compact-row"
                  @click="readNotification(item)"
                >
                  <span
                    ><b>{{ item.title }}</b
                    ><small>{{ dateTime(item.created_at) }}</small></span
                  ><i v-if="!item.is_read"></i>
                </button>
              </section></div
          ></template>
          <template v-else-if="page === 'orders'"
            ><div class="workspace-toolbar">
              <select v-model="orderStatus">
                <option value="">全部状态</option>
                <option
                  v-for="status in [
                    ...orderStatusOptions,
                  ]"
                  :key="status"
                  :value="status"
                >
                  {{ statusText[status] || status }}
                </option></select
              ><button class="outline-action" @click="load">
                <RefreshCw :size="15" />刷新
              </button>
            </div>
            <div v-if="!orders.length" class="workspace-state compact">
              <PackageSearch :size="28" />
              <p>暂无符合条件的订单</p>
            </div>
            <div v-else class="data-table orders-table">
              <div class="table-head">
                <span>订单号 / 产品</span><span>地区</span
                ><span>数量 / 周期</span><span>金额</span><span>状态</span
                ><span>创建时间</span><span></span>
              </div>
              <button
                v-for="order in orders"
                :key="order.id"
                class="table-row"
                @click="router.push(`/client/orders/${order.id}`)"
              >
                <span data-label="订单"
                  ><b>{{ order.order_no }}</b
                  ><small>{{ order.product_name }}</small></span
                ><span data-label="地区">{{ order.region_name }}</span
                ><span data-label="数量 / 周期"
                  >{{ order.quantity }} 条 / {{ order.billing_days }} 天</span
                ><span data-label="金额">{{ money(order.total_amount) }}</span
                ><span data-label="状态"
                  ><em :data-status="order.status">{{
                    statusText[order.status] || order.status
                  }}</em></span
                ><span data-label="创建时间">{{
                  dateTime(order.created_at)
                }}</span
                ><span><ChevronRight :size="16" /></span>
              </button>
              <footer v-if="orderTotal" class="order-pager">
                <span
                  >共 {{ orderTotal }} 笔 · 第 {{ orderPage }} /
                  {{ orderPages }} 页</span
                >
                <div>
                  <button
                    type="button"
                    aria-label="上一页订单"
                    :disabled="orderPage <= 1 || loading"
                    @click="changeOrderPage(orderPage - 1)"
                  >
                    <ChevronLeft :size="16" /></button
                  ><button
                    type="button"
                    aria-label="下一页订单"
                    :disabled="orderPage >= orderPages || loading"
                    @click="changeOrderPage(orderPage + 1)"
                  >
                    <ChevronRight :size="16" />
                  </button>
                </div>
              </footer></div
          ></template>
          <template v-else-if="page === 'order'"
            ><div
              v-if="route.query.created"
              class="workspace-alert success-alert"
            >
              订单已创建。支付功能尚未接通，当前状态为“{{
                statusText[detail?.status] || detail?.status
              }}”。
            </div>
            <section v-if="detail" class="workspace-panel detail-panel">
              <div class="detail-heading">
                <div>
                  <p>订单号</p>
                  <h2>{{ detail.order_no }}</h2>
                </div>
                <em :data-status="detail.status">{{
                  statusText[detail.status] || detail.status
                }}</em>
              </div>
              <dl class="detail-grid">
                <div>
                  <dt>产品</dt>
                  <dd>{{ detail.product_name }}</dd>
                </div>
                <div>
                  <dt>地区</dt>
                  <dd>{{ detail.region_name }}</dd>
                </div>
                <div v-if="detail.requested_city_code || detail.requested_ip_segment">
                  <dt>指定城市</dt>
                  <dd>
                    {{ detail.requested_city_name || detail.requested_city_code
                    }}<template v-if="detail.requested_ip_segment"> · 号段 {{ detail.requested_ip_segment }}</template>
                  </dd>
                </div>
                <div>
                  <dt>数量</dt>
                  <dd>{{ detail.quantity }} 条</dd>
                </div>
                <div>
                  <dt>使用周期</dt>
                  <dd>{{ detail.billing_days }} 天</dd>
                </div>
                <div>
                  <dt>下单单价</dt>
                  <dd>{{ money(detail.unit_price) }}</dd>
                </div>
                <div>
                  <dt>订单总额</dt>
                  <dd>{{ money(detail.total_amount) }}</dd>
                </div>
                <div>
                  <dt>支付方式</dt>
                  <dd>
                    {{
                      paymentText[detail.payment_method] ||
                      detail.payment_method
                    }}
                  </dd>
                </div>
                <div>
                  <dt>支付状态</dt>
                  <dd>
                    {{
                      statusText[detail.payment_status] || detail.payment_status
                    }}
                  </dd>
                </div>
                <div>
                  <dt>创建时间</dt>
                  <dd>{{ dateTime(detail.created_at) }}</dd>
                </div>
                <div>
                  <dt>客户备注</dt>
                  <dd>{{ detail.remark || "—" }}</dd>
                </div>
              </dl>
              <div class="demo-alert">
                <CircleDollarSign :size="18" />
                <template v-if="detailPayable"
                  >该订单待支付，可在账户余额页用余额或在线支付完成付款。</template
                >
                <template v-else>订单状态以后端记录为准。</template>
              </div>
              <div class="detail-actions">
                <button
                  class="outline-action"
                  @click="router.push('/client/orders')"
                >
                  <ChevronRight class="back-icon" :size="15" />返回订单</button
                ><button
                  v-if="detailPayable"
                  class="outline-action"
                  @click="router.push(walletPath)"
                >
                  去支付
                </button>
                <button
                  v-if="
                    workspace !== 'sales' &&
                    ['pending_payment', 'pending_confirm', 'renewal'].includes(
                      detail.status,
                    )
                  "
                  class="danger-action"
                  :disabled="busy"
                  @click="openCustomerOrderCancellation(detail)"
                >
                  取消订单
                </button>
              </div>
            </section></template
          >
          <template v-else-if="page === 'lines'"
            ><div class="filter-grid">
              <select v-model="filters.product">
                <option value="">全部产品</option>
                <option v-for="value in lineProducts" :key="value">
                  {{ value }}
                </option></select
              ><select v-model="filters.region">
                <option value="">全部地区</option>
                <option v-for="value in lineRegions" :key="value">
                  {{ value }}
                </option></select
              ><select v-model="filters.status">
                <option value="">全部状态</option>
                <option
                  v-for="value in [
                    ...new Set(lines.map((item) => item.status)),
                  ]"
                  :key="value"
                >
                  {{ value }}
                </option></select
              ><input v-model="filters.note" placeholder="搜索备注" />
            </div>
            <div v-if="!filteredLines.length" class="workspace-state compact">
              <Server :size="28" />
              <p>暂无符合条件的线路</p>
            </div>
            <div v-else class="data-table lines-table">
              <div class="table-head">
                <span>线路 / IP</span><span>地区 / 产品</span
                ><span>开通 / 到期</span><span>状态</span><span>备注</span
                ><span></span>
              </div>
              <div
                v-for="line in filteredLines"
                :key="line.id"
                class="table-row"
              >
                <span data-label="线路 / IP"
                  ><b>{{ line.line_name }}</b
                  ><small>{{ line.ip || "IP 待交付" }}</small></span
                ><span data-label="地区 / 产品"
                  >{{ line.region }}<small>{{ line.product_type }}</small></span
                ><span data-label="开通 / 到期"
                  >{{ dateTime(line.started_at)
                  }}<small>{{ dateTime(line.expires_at) }}</small></span
                ><span data-label="状态"
                  ><em :data-status="line.status">{{
                    statusText[line.status] || line.status
                  }}</em></span
                ><span data-label="备注" class="note-cell"
                  ><template v-if="editingLineId === line.id"
                    ><input v-model="noteDraft" maxlength="1000" /><span
                      ><button @click="saveNote(line)">保存</button
                      ><button @click="editingLineId = null">取消</button></span
                    ></template
                  ><button v-else class="note-button" @click="beginNote(line)">
                    {{ line.customer_note || "添加备注" }}
                  </button></span
                ><span
                  ><button
                    class="icon-link"
                    aria-label="查看线路详情"
                    @click="router.push(`/client/lines/${line.id}`)"
                  >
                    <ChevronRight :size="17" /></button
                ></span>
              </div></div
          ></template>
          <template v-else-if="page === 'line'"
            ><section v-if="detail" class="workspace-panel detail-panel">
              <div class="detail-heading">
                <div>
                  <p>线路详情</p>
                  <h2>{{ detail.line_name }}</h2>
                </div>
                <em :data-status="detail.status">{{
                  statusText[detail.status] || detail.status
                }}</em>
              </div>
              <dl class="detail-grid">
                <div>
                  <dt>地区</dt>
                  <dd>{{ detail.region }}</dd>
                </div>
                <div>
                  <dt>IP</dt>
                  <dd>{{ detail.ip || "待交付" }}</dd>
                </div>
                <div>
                  <dt>产品类型</dt>
                  <dd>{{ detail.product_type }}</dd>
                </div>
                <div>
                  <dt>开通日期</dt>
                  <dd>{{ dateTime(detail.started_at) }}</dd>
                </div>
                <div>
                  <dt>到期日期</dt>
                  <dd>{{ dateTime(detail.expires_at) }}</dd>
                </div>
                <div>
                  <dt>客户备注</dt>
                  <dd>{{ detail.customer_note || "—" }}</dd>
                </div>
              </dl>
              <h3 class="subheading">线路交付信息</h3>
              <div
                v-if="
                  !detail.node_link &&
                  !detail.subscription_url &&
                  !detail.node_qr_data &&
                  !detail.subscription_qr_data
                "
                class="delivery-empty"
              >
                线路交付信息将在开通后显示
              </div>
              <dl v-else class="credential-list">
                <div>
                  <dt>节点链接</dt>
                  <dd class="credential-copy">{{ detail.node_link || "—" }}</dd>
                </div>
                <div>
                  <dt>订阅地址</dt>
                  <dd class="credential-copy">{{ detail.subscription_url || "—" }}</dd>
                </div>
              </dl>
              <div
                v-if="detail.node_qr_url || detail.subscription_qr_url"
                class="qr-grid"
              >
                <figure v-if="detail.node_qr_url">
                  <img :src="detail.node_qr_url" alt="节点二维码" loading="lazy" />
                  <figcaption>节点二维码：手机扫码直接导入客户端</figcaption>
                </figure>
                <figure v-if="detail.subscription_qr_url">
                  <img :src="detail.subscription_qr_url" alt="订阅二维码" loading="lazy" />
                  <figcaption>订阅二维码：导入后自动同步全部线路</figcaption>
                </figure>
              </div>
              <div class="software-grid">
                <span
                  >Clash
                  <b>{{ detail.clash_import ? "可用" : "待接入" }}</b></span
                ><span
                  >V2Ray
                  <b>{{ detail.v2ray_import ? "可用" : "待接入" }}</b></span
                ><span
                  >NekoBox
                  <b>{{ detail.nekobox_import ? "可用" : "待接入" }}</b></span
                ><span>Sing-box <b>待接入</b></span>
              </div>
              <div class="detail-actions">
                <button
                  class="outline-action"
                  @click="router.push('/client/lines')"
                >
                  返回线路</button
                ><button
                  class="button button-small"
                  @click="
                    beginRenew(detail);
                    router.push('/client/renewals');
                  "
                >
                  发起续费
                </button>
              </div>
            </section></template
          >
          <template v-else-if="page === 'client-buy'"><ClientBuyPanel /></template>
          <template v-else-if="page === 'renewals'"
            ><div class="workspace-alert">
              续费订单不会自动扣款；选择余额或在线支付后到账户余额完成付款，付款成功即延长到期时间，选择“联系经理确认”则由管理员人工完成。
            </div>
            <section class="workspace-panel">
              <div class="panel-head"><h2>可续费线路</h2></div>
              <div v-if="!lines.length" class="inline-empty">暂无线路</div>
              <div v-for="line in lines" :key="line.id" class="renewal-row">
                <span
                  ><b>{{ line.line_name }}</b
                  ><small
                    >{{ line.region }} · 到期
                    {{ dateTime(line.expires_at) }}</small
                  ></span
                ><em :class="{ overdue: daysLeft(line.expires_at) < 0 }">{{
                  daysLeft(line.expires_at) < 0
                    ? `已到期 ${Math.abs(daysLeft(line.expires_at))} 天`
                    : `剩余 ${daysLeft(line.expires_at)} 天`
                }}</em
                ><button class="outline-action" @click="beginRenew(line)">
                  续费
                </button>
              </div>
            </section>
            <section
              v-if="renewDraft.lineId"
              class="workspace-panel renewal-form"
            >
              <h2>创建续费订单</h2>
              <label
                >续费周期<select v-model="renewDraft.billing_days">
                  <option
                    v-for="item in renewPeriods"
                    :key="item.billing_days"
                    :value="item.billing_days"
                  >
                    {{ item.billing_days }} 天{{
                      item.unit_price != null ? ` · ${money(item.unit_price)}` : ""
                    }}
                  </option>
                </select></label
              ><label
                >支付方式<select v-model="renewDraft.payment_method">
                  <option value="contact_manager">联系经理确认（人工完成）</option>
                  <option v-if="workspace !== 'sales'" value="balance">余额支付</option>
                  <option v-if="workspace !== 'sales'" value="online">在线支付</option>
                </select></label
              >
              <p class="renewal-hint">
                余额或在线支付会在账户余额页完成付款，付款成功后线路到期时间自动延长；选择“联系经理确认”则由管理员人工完成。
                <template v-if="renewUnitPrice != null"
                  >本档单价 {{ money(renewUnitPrice) }}。</template
                >
              </p>
              <div>
                <button
                  class="button button-small"
                  :disabled="busy"
                  @click="submitRenew"
                >
                  {{ busy ? "提交中…" : "创建续费订单" }}</button
                ><button class="text-action" @click="renewDraft.lineId = null">
                  取消
                </button>
              </div>
            </section>
            <section class="workspace-panel">
              <div class="panel-head"><h2>续费订单</h2></div>
              <div v-if="!renewals.length" class="inline-empty">
                暂无续费订单
              </div>
              <button
                v-for="order in renewals"
                :key="order.id"
                class="compact-row"
                @click="router.push(`/client/orders/${order.id}`)"
              >
                <span
                  ><b>{{ order.order_no }}</b
                  ><small
                    >{{ order.billing_days }} 天 ·
                    {{ money(order.total_amount) }}</small
                  ></span
                ><em :data-status="order.status">{{
                  statusText[order.status] || order.status
                }}</em>
              </button>
            </section></template
          >
          <template v-else-if="page === 'notifications'"
            ><div class="workspace-toolbar">
              <span
                >共 {{ notifications.length }} 条，{{
                  unreadCount
                }}
                条未读</span
              ><button
                class="outline-action"
                :disabled="!unreadCount || busy"
                @click="readAll"
              >
                全部标记已读
              </button>
            </div>
            <div v-if="!notifications.length" class="workspace-state compact">
              <Inbox :size="28" />
              <p>暂无通知</p>
            </div>
            <section v-else class="notification-list">
              <button
                v-for="item in notifications"
                :key="item.id"
                :class="{ unread: !item.is_read }"
                @click="readNotification(item)"
              >
                <i></i
                ><span
                  ><b>{{ item.title }}</b>
                  <p>{{ item.content }}</p>
                  <small
                    >{{ item.category }} ·
                    {{ dateTime(item.created_at) }}</small
                  ></span
                ><em>{{ item.is_read ? "已读" : "标记已读" }}</em>
              </button>
            </section></template
          >
          <template v-else-if="page === 'security-center'">
            <section class="workspace-panel security-panel">
              <div class="panel-head">
                <div><h2>登录密码</h2><p>建议定期更换；修改后所有设备都需要重新登录。</p></div>
                <button class="outline-action" type="button" @click="router.push('/change-password')">
                  修改密码
                </button>
              </div>
              <p class="security-warning">新密码需要至少 10 位，并同时包含大写、小写、数字和符号。</p>
            </section>
            <section class="workspace-panel security-panel">
              <div class="panel-head">
                <div><h2>账号密保</h2><p>{{ securityQuestions.configured ? "已设置两项密保问题" : "尚未设置密保问题" }}</p></div>
              </div>
              <dl v-if="securityQuestions.configured" class="security-current-questions"><div><dt>第一题</dt><dd>{{ securityQuestionLabel(securityQuestions.question_1) }}</dd></div><div><dt>第二题</dt><dd>{{ securityQuestionLabel(securityQuestions.question_2) }}</dd></div></dl>
              <p class="security-warning">密保答案可能被熟人或公开资料猜到。请使用只有您知道的、不易搜索到的答案；不要填写真实密码或其他账号的安全答案。</p>
              <form class="security-question-form" @submit.prevent="saveSecurityQuestions">
                <label><span>第一道问题</span><select v-model="securityQuestionForm.question_1" required><option value="" disabled>选择问题</option><option v-for="[id, label] in securityQuestionOptions" :key="id" :value="id">{{ label }}</option></select></label>
                <label><span>第一题答案</span><input v-model="securityQuestionForm.answer_1" type="password" autocomplete="off" minlength="2" maxlength="128" required /></label>
                <label><span>第二道问题</span><select v-model="securityQuestionForm.question_2" required><option value="" disabled>选择与第一题不同的问题</option><option v-for="[id, label] in securityQuestionOptions" :key="id" :value="id" :disabled="id === securityQuestionForm.question_1">{{ label }}</option></select></label>
                <label><span>第二题答案</span><input v-model="securityQuestionForm.answer_2" type="password" autocomplete="off" minlength="2" maxlength="128" required /></label>
                <label class="security-current-password"><span>当前登录密码</span><input v-model="securityQuestionForm.current_password" type="password" autocomplete="current-password" required /></label>
                <p class="security-question-note">答案只以加密哈希保存，无法在页面查看。修改密保时需要重新填写两题和当前密码。</p>
                <button class="security-save" type="submit" :disabled="busy"><ShieldCheck :size="16" />{{ busy ? "正在保存…" : "保存密保问题" }}</button>
              </form>
            </section>
            <section class="workspace-panel security-panel">
              <div class="panel-head">
                <div><h2>消息推送</h2><p>站内信始终保留；这里控制是否同步推到企业微信等外部渠道。</p></div>
              </div>
              <p v-if="!myChannels.length" class="security-warning">管理员还没有为你绑定推送渠道。绑定后新订单、客户消息和到期提醒会同时推到微信。</p>
              <div v-else class="my-channel-list">
                <div v-for="item in myChannels" :key="item.id" class="my-channel-row">
                  <span><b>{{ item.channel_name }}</b><small>推送到 {{ item.target }}</small></span>
                  <button type="button" class="outline-action" :disabled="channelBusy === item.id" @click="toggleMyChannel(item)">{{ item.is_enabled ? "已开启 · 关闭" : "已关闭 · 开启" }}</button>
                </div>
              </div>
            </section>
          </template>
          <template v-else-if="page === 'profile'"
            ><section v-if="profile" class="workspace-panel detail-panel">
              <div class="detail-heading">
                <div>
                  <p>客户档案</p>
                  <h2>{{ profile.name }}</h2>
                </div>
                <em>{{ profile.status }}</em>
              </div>
              <dl class="detail-grid">
                <div>
                  <dt>客户编号</dt>
                  <dd>#{{ profile.id }}</dd>
                </div>
                <div>
                  <dt>邮箱</dt>
                  <dd>{{ profile.email }}</dd>
                </div>
                <div>
                  <dt>手机号</dt>
                  <dd>{{ profile.phone || "—" }}</dd>
                </div>
                <div>
                  <dt>客户等级</dt>
                  <dd>{{ profile.level }}</dd>
                </div>
                <div>
                  <dt>账户余额</dt>
                  <dd>{{ money(profile.balance) }}</dd>
                </div>
                <div>
                  <dt>登录账号</dt>
                  <dd>{{ storedUser.username }}</dd>
                </div>
              </dl>
              <p class="profile-note">
                账户资料修改功能尚未开放，如需变更请联系销售经理。
              </p>
            </section></template
          >
          <template v-else-if="page === 'sales-customers'">
            <div class="workspace-toolbar">
              <label class="search-field">
                <Search :size="15" />
                <input v-model="salesCustomerKeyword" type="search" placeholder="搜索客户名称、邮箱或手机号" />
              </label>
              <span class="toolbar-hint">{{ filteredSalesCustomers.length }} / {{ salesCustomers.length }} 位客户</span>
            </div>
            <div v-if="!filteredSalesCustomers.length" class="inline-empty">
              {{ salesCustomers.length ? '没有匹配的客户' : '暂无负责客户：客户通过你的邀请码注册后会自动出现在这里' }}
            </div>
            <section v-else class="workspace-panel">
              <div class="panel-head">
                <h2>负责客户</h2>
                <span>行内可直接代客下单</span>
              </div>
              <div
                v-for="customer in filteredSalesCustomers"
                :key="customer.id"
                class="compact-row static-row"
              >
                <span>
                  <b>{{ customer.name }}</b>
                  <small>{{ customer.email }} · {{ customer.phone || "未填写手机号" }}</small>
                </span>
                <div class="customer-row-actions">
                  <em>{{ customer.status }}</em>
                  <button type="button" class="mini-order-button" @click="openOnBehalf(customer)">代客下单</button>
                </div>
              </div>
            </section>
          </template>

          <template v-else-if="page === 'sales'"
            ><section v-if="dashboard" class="metric-grid sales-metrics">
              <article>
                <span>客户总数</span><strong>{{ dashboard.customers }}</strong>
              </article>
              <article>
                <span>活跃线路</span
                ><strong>{{ dashboard.active_lines }}</strong>
              </article>
              <article>
                <span>7 天内到期</span
                ><strong>{{ dashboard.expiring_in_7_days }}</strong>
              </article>
              <article>
                <span>待确认订单</span
                ><strong>{{ dashboard.pending_confirm_orders }}</strong>
              </article>
              <article>
                <span>待交付订单</span
                ><strong>{{ dashboard.pending_delivery_orders }}</strong>
              </article>
              <article>
                <span>未读通知</span
                ><strong>{{ dashboard.unread_notifications }}</strong>
              </article>
              <article>
                <span>本月回款</span
                ><strong>{{ money(dashboard.monthly_sales) }}</strong>
              </article>
              <article>
                <span>本月续费回款</span
                ><strong>{{ money(dashboard.monthly_renewals) }}</strong>
              </article>
            </section>
            <section class="workspace-panel invite-panel">
              <div class="panel-head">
                <h2>我的邀请码</h2>
                <span>客户走你的码注册，自动归属到你名下</span>
              </div>
              <div v-if="!inviteCodes.length" class="inline-empty">
                还没有邀请码，生成一个发到微信吧
              </div>
              <div
                v-for="item in inviteCodes"
                :key="item.id"
                class="invite-row"
              >
                <div class="invite-main">
                  <b class="invite-code">{{ item.code }}</b>
                  <small
                    >{{ item.note || "未备注" }} · 已带来
                    {{ item.used_count }} 位客户<template v-if="item.max_uses"
                      >（上限 {{ item.max_uses }}）</template
                    ><template v-if="item.expires_at"
                      > · {{ String(item.expires_at).slice(0, 10) }} 到期</template
                    ></small
                  >
                </div>
                <div class="invite-actions">
                  <button
                    class="outline-action"
                    :disabled="inviteBusy"
                    @click="copyInviteLink(item)"
                  >
                    复制注册链接
                  </button>
                  <button
                    v-if="item.status === 'active'"
                    class="outline-action"
                    :disabled="inviteBusy"
                    @click="disableInviteCode(item)"
                  >
                    停用
                  </button>
                  <em v-else class="invite-disabled-tag">已停用</em>
                </div>
              </div>
              <form class="invite-form" @submit.prevent="createInviteCode">
                <input
                  v-model="inviteForm.note"
                  maxlength="120"
                  placeholder="备注（可选）：如 抖音地推 / 官网轮播 / 朋友圈"
                />
                <button
                  class="outline-action"
                  type="submit"
                  :disabled="inviteBusy"
                >
                  {{ inviteBusy ? "处理中…" : "生成新邀请码" }}
                </button>
              </form>
              <p v-if="inviteError" class="form-error" role="alert">
                {{ inviteError }}
              </p>
            </section>
            <section class="workspace-panel outreach-panel">
              <div class="panel-head">
                <h2>到期触达</h2>
                <span v-if="outreach"
                  >{{ outreach.summary.untouched }} / {{ outreach.summary.total }}
                  条待联系</span
                >
              </div>
              <p v-if="!outreach" class="outreach-hint">
                触达清单加载失败，刷新页面重试。
              </p>
              <template v-else>
                <div class="outreach-stats">
                  <span data-tone="bad"
                    >已过期 {{ outreach.summary.overdue }}</span
                  >
                  <span data-tone="warn"
                    >7 天内 {{ outreach.summary.urgent }}</span
                  >
                  <span>30 天内 {{ outreach.summary.soon }}</span>
                  <span v-if="outreach.summary.promised" data-tone="good"
                    >已答应续费 {{ outreach.summary.promised }}</span
                  >
                </div>
                <div v-if="!outreach.items.length" class="inline-empty">
                  未来 30 天没有需要续费的线路
                </div>
                <div
                  v-for="item in visibleOutreach"
                  :key="item.line_id"
                  class="outreach-row"
                >
                  <div class="outreach-main">
                    <b>{{ item.customer_name }}</b>
                    <small
                      >{{ item.line_name }} ·
                      {{ item.region || item.product_type }} ·
                      {{ String(item.expires_at).slice(0, 10) }} 到期</small
                    >
                    <span class="outreach-tags">
                      <em :data-tone="outreachTone(item.days_left)">{{
                        outreachDaysLabel(item.days_left)
                      }}</em>
                      <em v-if="item.renewal_price != null"
                        >续费 {{ money(item.renewal_price) }}</em
                      >
                      <em v-else class="outreach-noprice">价格待确认</em>
                      <em v-if="item.last_touch" data-tone="good"
                        >{{ item.last_touch.days_ago }} 天前 ·
                        {{ item.last_touch.channel_label }} ·
                        {{ item.last_touch.outcome_label }}</em
                      >
                      <em v-else data-tone="muted">未联系</em>
                    </span>
                    <p class="outreach-message">{{ item.suggested_message }}</p>
                  </div>
                  <div class="outreach-actions">
                    <button class="outline-action" @click="copyOutreach(item)">
                      复制文案
                    </button>
                    <button class="outline-action" @click="startTouch(item)">
                      记一笔
                    </button>
                  </div>
                  <div
                    v-if="touchForm.lineId === item.line_id"
                    class="outreach-form"
                  >
                    <label
                      >渠道<select v-model="touchForm.channel">
                        <option
                          v-for="choice in outreach.channels"
                          :key="choice.value"
                          :value="choice.value"
                        >
                          {{ choice.label }}
                        </option>
                      </select></label
                    >
                    <label
                      >结果<select v-model="touchForm.outcome">
                        <option
                          v-for="choice in outreach.outcomes"
                          :key="choice.value"
                          :value="choice.value"
                        >
                          {{ choice.label }}
                        </option>
                      </select></label
                    >
                    <label class="outreach-note"
                      >备注<input
                        v-model="touchForm.note"
                        maxlength="200"
                        placeholder="例如：说周五打款"
                    /></label>
                    <button
                      class="outline-action"
                      :disabled="outreachBusy === item.line_id"
                      @click="saveTouch(item)"
                    >
                      {{ outreachBusy === item.line_id ? "保存中" : "保存" }}
                    </button>
                    <button class="text-action" @click="touchForm.lineId = null">
                      取消
                    </button>
                  </div>
                </div>
                <button
                  v-if="outreach.items.length > 6"
                  class="outreach-more"
                  @click="outreachOpen = !outreachOpen"
                >
                  {{
                    outreachOpen
                      ? "收起"
                      : `查看全部 ${outreach.items.length} 条`
                  }}
                </button>
              </template>
            </section>
            <div class="workspace-columns">
              <section class="workspace-panel">
                <div class="panel-head">
                  <h2>负责客户</h2>
                  <span>{{ salesCustomers.length }} 位</span>
                </div>
                <div v-if="!salesCustomers.length" class="inline-empty">
                  暂无负责客户
                </div>
                <div
                  v-for="customer in salesCustomers"
                  :key="customer.id"
                  class="compact-row static-row"
                >
                  <span
                    ><b>{{ customer.name }}</b
                    ><small
                      >{{ customer.email }} ·
                      {{ customer.phone || "未填写手机号" }}</small
                    ></span
                  >
                  <div class="customer-row-actions">
                    <em>{{ customer.status }}</em>
                    <button
                      type="button"
                      class="mini-order-button"
                      @click="openOnBehalf(customer)"
                    >
                      代客下单
                    </button>
                  </div>
                </div>
              </section>
              <section class="workspace-panel">
                <div class="panel-head">
                  <h2>客户订单</h2>
                  <span>{{ salesOrderTotal }} 笔</span>
                </div>
                <div v-if="!salesOrders.length" class="inline-empty">
                  暂无客户订单
                </div>
                <div
                  v-for="order in salesOrders"
                  :key="order.id"
                  class="compact-row static-row"
                >
                  <span
                    ><b>{{ order.order_no }}</b
                    ><small
                      >{{ order.product_name }} ·
                      {{ money(order.total_amount) }}</small
                    ></span
                  ><em :data-status="order.status">{{
                    statusText[order.status] || order.status
                  }}</em>
                </div>
                <footer v-if="salesOrderTotal > salesOrderPageSize" class="order-pager">
                  <span
                    >共 {{ salesOrderTotal }} 笔 · 第 {{ salesOrderPage }} /
                    {{ salesOrderPages }} 页</span
                  >
                  <div>
                    <button
                      type="button"
                      aria-label="上一页客户订单"
                      :disabled="salesOrderPage <= 1"
                      @click="changeSalesOrderPage(salesOrderPage - 1)"
                    >
                      <ChevronLeft :size="16" /></button
                    ><button
                      type="button"
                      aria-label="下一页客户订单"
                      :disabled="salesOrderPage >= salesOrderPages"
                      @click="changeSalesOrderPage(salesOrderPage + 1)"
                    >
                      <ChevronRight :size="16" />
                    </button>
                  </div>
                </footer>
              </section>
              <section class="workspace-panel">
                <div class="panel-head"><h2>即将到期线路</h2></div>
                <div v-if="!expiringLines.length" class="inline-empty">
                  暂无即将到期线路
                </div>
                <div
                  v-for="line in expiringLines"
                  :key="line.id"
                  class="compact-row static-row"
                >
                  <span
                    ><b>{{ line.line_name }}</b
                    ><small
                      >{{ line.region }} · {{ line.ip || "IP 待交付" }}</small
                    ></span
                  ><em>{{ daysLeft(line.expires_at) }} 天</em>
                </div>
              </section>
              <section class="workspace-panel">
                <div class="panel-head"><h2>线路健康影响</h2></div>
                <div v-if="!healthImpacts.length" class="inline-empty">
                  当前没有线路健康异常
                </div>
                <div
                  v-for="impact in healthImpacts"
                  :key="impact.incident_id"
                  class="compact-row static-row"
                >
                  <span
                    ><b>{{ impact.line_name }}</b
                    ><small>{{
                      impact.reason || "未填写异常原因"
                    }}</small></span
                  ><em>{{ impact.status }}</em>
                </div>
              </section>
            </div></template
          >
          <template v-else-if="page === 'admin'"
            ><div class="workspace-toolbar">
              <select v-model="orderStatus">
                <option value="">全部状态</option>
                <option
                  v-for="status in [
                    ...orderStatusOptions,
                  ]"
                  :key="status"
                  :value="status"
                >
                  {{ statusText[status] || status }}
                </option></select
              ><button class="outline-action" @click="load">
                <RefreshCw :size="15" />刷新
              </button>
            </div>
            <div v-if="!orders.length" class="workspace-state compact">
              <Box :size="28" />
              <p>暂无符合条件的订单</p>
            </div>
            <section
              v-for="order in orders"
              :key="order.id"
              class="workspace-panel admin-order"
            >
              <div class="admin-order-head">
                <span
                  ><b>{{ order.order_no }}</b
                  ><small
                    >{{ order.product_name }} · {{ order.region_name }}</small
                  ></span
                ><em :data-status="order.status">{{
                  statusText[order.status] || order.status
                }}</em>
              </div>
              <div v-if="order.source_type === 'resource_pool'" class="resource-order-note"><ShieldOff :size="14" />资源池 1 订单 · 当前待付款和人工采购审核，系统不会自动采购或开通</div>
              <dl class="admin-order-meta">
                <div v-if="order.requested_city_code || order.requested_ip_segment">
                  <dt>指定城市</dt>
                  <dd>
                    {{ order.requested_city_name || order.requested_city_code
                    }}<template v-if="order.requested_ip_segment"> · 号段 {{ order.requested_ip_segment }}</template>
                  </dd>
                </div>
                <div>
                  <dt>数量 / 周期</dt>
                  <dd>{{ order.quantity }} 条 / {{ order.billing_days }} 天</dd>
                </div>
                <div>
                  <dt>单价快照</dt>
                  <dd>{{ money(order.unit_price) }}</dd>
                </div>
                <div>
                  <dt>订单总额</dt>
                  <dd>{{ money(order.total_amount) }}</dd>
                </div>
                <div>
                  <dt>支付状态</dt>
                  <dd>
                    {{
                      statusText[order.payment_status] || order.payment_status
                    }}
                  </dd>
                </div>
                <div>
                  <dt>客户编号</dt>
                  <dd>#{{ order.customer_id }}</dd>
                </div>
                <div>
                  <dt>创建时间</dt>
                  <dd>{{ dateTime(order.created_at) }}</dd>
                </div>
              </dl>
              <div class="admin-actions">
                <button
                  v-if="
                    [
                      'pending_confirm',
                      'pending_payment',
                      'paid',
                      'processing',
                    ].includes(order.status) && order.order_type !== 'renewal' && order.source_type !== 'resource_pool'
                  "
                  class="button button-small"
                  :disabled="busy"
                  @click="openAdminOrderAction(order, 'confirm')"
                >
                  确认订单</button
                ><button
                  v-if="
                    ['pending_delivery', 'partially_delivered'].includes(
                      order.status,
                    ) && order.order_type !== 'renewal' && order.source_type !== 'resource_pool'
                  "
                  class="outline-action"
                  @click="openDelivery(order)"
                >
                  手动交付</button
                ><button
                  v-if="canCompleteRenewal(order)"
                  class="button button-small"
                  :disabled="busy"
                  @click="openRenewalConfirmation(order)"
                >
                  人工完成续费</button
                ><button
                  v-if="
                    ![
                      'delivered',
                      'partially_delivered',
                      'completed',
                      'cancelled',
                      'refunding',
                      'refunded',
                    ].includes(order.status)
                  "
                  class="danger-action"
                  :disabled="busy"
                  @click="openAdminOrderAction(order, 'cancel')"
                >
                  取消订单
                </button>
              </div>
              <form
                v-if="deliveryOrderId === order.id"
                class="delivery-form"
                @submit.prevent="submitDelivery"
              >
                <h3>交付线路</h3>
                <DeliveryLinkInput v-model="delivery.node_link" :disabled="busy" />
                <div class="delivery-grid">
                  <label>静态 IP<input v-model="delivery.static_ip" /></label>
                  <label
                    >到期时间 *<input
                      v-model="delivery.expires_at"
                      type="datetime-local"
                      required /></label
                  ><label class="wide">客户备注<input v-model="delivery.customer_note" maxlength="1000" /></label>
                </div>
                <details class="delivery-advanced">
                  <summary>改用已有节点 ID</summary>
                  <label
                    >节点 ID<input
                      v-model="delivery.node_id"
                      type="number"
                      min="1"
                      placeholder="不贴链接时才需要填" /></label>
                </details>
                <p>二维码与 Clash 订阅由后端按链接生成，客户在「我的线路」里扫码或复制订阅地址即可使用。</p>
                <div>
                  <button
                    class="button button-small"
                    :disabled="busy"
                    type="submit"
                  >
                    核对交付信息</button
                  ><button
                    class="text-action"
                    type="button"
                    @click="deliveryOrderId = null"
                  >
                    取消
                  </button>
                </div>
              </form>
            </section>
            <footer v-if="orderTotal" class="order-pager">
              <span
                >共 {{ orderTotal }} 笔 · 第 {{ orderPage }} /
                {{ orderPages }} 页</span
              >
              <div>
                <button
                  type="button"
                  aria-label="上一页订单"
                  :disabled="orderPage <= 1 || loading"
                  @click="changeOrderPage(orderPage - 1)"
                >
                  <ChevronLeft :size="16" /></button
                ><button
                  type="button"
                  aria-label="下一页订单"
                  :disabled="orderPage >= orderPages || loading"
                  @click="changeOrderPage(orderPage + 1)"
                >
                  <ChevronRight :size="16" />
                </button>
              </div>
            </footer></template
          >
          <template v-else-if="page === 'admin-sales-users'">
            <section class="workspace-panel sales-user-panel">
              <div class="panel-head"><h2>新建销售账号</h2></div>
              <form class="sales-user-form" @submit.prevent="submitSalesUser">
                <label><span>登录用户名</span><input v-model="salesUserForm.username" autocomplete="off" minlength="3" maxlength="80" pattern="[A-Za-z0-9][A-Za-z0-9_.-]*" required /></label>
                <label><span>销售姓名</span><input v-model="salesUserForm.display_name" autocomplete="name" maxlength="120" required /></label>
                <label><span>联系电话</span><input v-model="salesUserForm.phone" type="tel" autocomplete="tel" maxlength="40" /></label>
                <label><span>初始密码</span><input v-model="salesUserForm.password" type="password" autocomplete="new-password" minlength="10" maxlength="128" required /><small>至少 10 位，包含大小写字母、数字和特殊字符</small></label>
                <label><span>确认初始密码</span><input v-model="salesUserForm.confirmPassword" type="password" autocomplete="new-password" minlength="10" maxlength="128" required /></label>
                <p class="sales-user-note">账号仅具有销售权限；初始密码由创建者通过安全渠道单独告知。</p>
                <button class="sales-user-submit" type="submit" :disabled="busy"><UserPlus :size="16" />{{ busy ? "正在创建…" : "创建销售账号" }}</button>
              </form>
            </section>
          </template>
        </template>
      </main>
    </div>
    <div
      v-if="mobileNav"
      class="workspace-overlay"
      @click="mobileNav = false"
    ></div>
    <div
      v-if="customerOrderCancellation"
      class="renewal-confirm-backdrop"
      role="presentation"
      @click.self="busy || (customerOrderCancellation = null)"
    >
      <section
        class="renewal-confirm-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="customer-order-cancel-title"
      >
        <header>
          <div>
            <h2 id="customer-order-cancel-title">确认取消订单</h2>
            <p>取消后订单将终止处理，当前页面不能恢复。</p>
          </div>
          <button
            type="button"
            aria-label="关闭确认窗口"
            :disabled="busy"
            @click="customerOrderCancellation = null"
          >
            <X :size="19" />
          </button>
        </header>
        <dl>
          <div>
            <dt>订单</dt>
            <dd>{{ customerOrderCancellation.order_no }}</dd>
          </div>
          <div>
            <dt>产品</dt>
            <dd>{{ customerOrderCancellation.product_name }}</dd>
          </div>
          <div>
            <dt>数量 / 周期</dt>
            <dd>
              {{ customerOrderCancellation.quantity }} 条 /
              {{ customerOrderCancellation.billing_days }} 天
            </dd>
          </div>
          <div>
            <dt>订单金额</dt>
            <dd>{{ money(customerOrderCancellation.total_amount) }}</dd>
          </div>
        </dl>
        <div class="renewal-confirm-warning">
          <template v-if="paymentMayBeInFlight(customerOrderCancellation)">
            该订单可能已在支付通道处理中。若刚完成付款，取消失败属正常，请先到钱包页确认支付结果。
          </template>
          <template v-else>
            确认后订单将终止处理；已到账的款项不会自动退回，需联系管理员走退款。
          </template>
        </div>
        <p v-if="error" class="renewal-confirm-error" role="alert">
          {{ error }}
        </p>
        <footer>
          <button
            class="outline-action"
            type="button"
            :disabled="busy"
            @click="customerOrderCancellation = null"
          >
            返回</button
          ><button
            class="danger-action"
            type="button"
            :disabled="busy"
            @click="submitCustomerOrderCancellation"
          >
            {{ busy ? "正在取消…" : "确认取消订单" }}
          </button>
        </footer>
      </section>
    </div>
    <div
      v-if="customerRenewalConfirmation"
      class="renewal-confirm-backdrop"
      role="presentation"
      @click.self="customerRenewalConfirmation = null"
    >
      <section
        class="renewal-confirm-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="customer-renewal-confirm-title"
      >
        <header>
          <div>
            <h2 id="customer-renewal-confirm-title">确认创建续费订单</h2>
            <p>本步骤只创建续费订单；未完成付款或人工确认前不会扣款，也不会延长线路。</p>
          </div>
          <button
            type="button"
            aria-label="关闭确认窗口"
            :disabled="busy"
            @click="customerRenewalConfirmation = null"
          >
            <X :size="19" />
          </button>
        </header>
        <dl>
          <div>
            <dt>线路</dt>
            <dd>{{ customerRenewalConfirmation.line.line_name }}</dd>
          </div>
          <div>
            <dt>当前到期时间</dt>
            <dd>{{ dateTime(customerRenewalConfirmation.line.expires_at) }}</dd>
          </div>
          <div>
            <dt>续费周期</dt>
            <dd>{{ customerRenewalConfirmation.billing_days }} 天</dd>
          </div>
          <div>
            <dt>支付方式</dt>
            <dd>
              {{ paymentText[customerRenewalConfirmation.payment_method] }}
            </dd>
          </div>
          <div v-if="customerRenewalConfirmation.unit_price != null">
            <dt>应付金额</dt>
            <dd>{{ money(customerRenewalConfirmation.unit_price) }}</dd>
          </div>
        </dl>
        <div class="renewal-confirm-warning">
          {{
            customerRenewalConfirmation.payment_method === "contact_manager"
              ? "提交后由管理员人工确认完成，确认前线路到期时间保持不变。"
              : "创建后需在账户余额页完成付款，付款成功后线路到期时间自动延长。"
          }}
        </div>
        <p v-if="error" class="renewal-confirm-error" role="alert">
          {{ error }}
        </p>
        <footer>
          <button
            class="outline-action"
            type="button"
            :disabled="busy"
            @click="customerRenewalConfirmation = null"
          >
            返回</button
          ><button
            class="button button-small"
            type="button"
            :disabled="busy"
            @click="confirmCustomerRenewal"
          >
            {{ busy ? "正在创建…" : "确认创建续费订单" }}
          </button>
        </footer>
      </section>
    </div>
    <div
      v-if="orderActionConfirmation"
      class="renewal-confirm-backdrop"
      role="presentation"
      @click.self="orderActionConfirmation = null"
    >
      <section
        class="renewal-confirm-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="order-action-confirm-title"
      >
        <header>
          <div>
            <h2 id="order-action-confirm-title">
              {{
                orderActionConfirmation.action === "confirm"
                  ? "确认处理订单"
                  : "确认取消订单"
              }}
            </h2>
            <p>
              {{
                orderActionConfirmation.action === "confirm"
                  ? "确认后订单将进入待交付流程。"
                  : "取消后订单将终止处理，并通知对应客户。"
              }}
            </p>
          </div>
          <button
            type="button"
            aria-label="关闭确认窗口"
            :disabled="busy"
            @click="orderActionConfirmation = null"
          >
            <X :size="19" />
          </button>
        </header>
        <dl>
          <div>
            <dt>订单</dt>
            <dd>{{ orderActionConfirmation.order.order_no }}</dd>
          </div>
          <div>
            <dt>客户</dt>
            <dd>客户 #{{ orderActionConfirmation.order.customer_id }}</dd>
          </div>
          <div>
            <dt>产品</dt>
            <dd>{{ orderActionConfirmation.order.product_name }}</dd>
          </div>
          <div>
            <dt>订单金额</dt>
            <dd>{{ money(orderActionConfirmation.order.total_amount) }}</dd>
          </div>
        </dl>
        <div class="renewal-confirm-warning">
          {{
            orderActionConfirmation.action === "confirm"
              ? "此操作只确认订单并进入交付环节，不代表客户已完成在线支付。"
              : "取消操作会改变订单状态且无法通过当前页面恢复；已收款、支付中的订单会被系统拒绝，请走退款或等支付结果。"
          }}
        </div>
        <p v-if="error" class="renewal-confirm-error" role="alert">
          {{ error }}
        </p>
        <footer>
          <button
            class="outline-action"
            type="button"
            :disabled="busy"
            @click="orderActionConfirmation = null"
          >
            返回</button
          ><button
            :class="[
              orderActionConfirmation.action === 'cancel'
                ? 'danger-action'
                : 'button button-small',
            ]"
            type="button"
            :disabled="busy"
            @click="submitAdminOrderAction"
          >
            {{
              busy
                ? "正在提交…"
                : orderActionConfirmation.action === "confirm"
                  ? "确认订单"
                  : "确认取消订单"
            }}
          </button>
        </footer>
      </section>
    </div>
    <div
      v-if="deliveryConfirmation"
      class="renewal-confirm-backdrop"
      role="presentation"
      @click.self="deliveryConfirmation = null"
    >
      <section
        class="renewal-confirm-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="delivery-confirm-title"
      >
        <header>
          <div>
            <h2 id="delivery-confirm-title">确认交付线路</h2>
            <p>提交后客户将立即看到新线路和已填写的交付信息。</p>
          </div>
          <button
            type="button"
            aria-label="关闭确认窗口"
            :disabled="busy"
            @click="deliveryConfirmation = null"
          >
            <X :size="19" />
          </button>
        </header>
        <dl>
          <div>
            <dt>订单</dt>
            <dd>{{ deliveryConfirmation.order.order_no }}</dd>
          </div>
          <div>
            <dt>客户</dt>
            <dd>客户 #{{ deliveryConfirmation.order.customer_id }}</dd>
          </div>
          <div>
            <dt>线路影响</dt>
            <dd>
              新建第 {{ deliveryConfirmation.order.delivered_quantity + 1 }} /
              {{ deliveryConfirmation.order.quantity }} 条线路
            </dd>
          </div>
          <div>
            <dt>到期时间</dt>
            <dd>{{ dateTime(deliveryConfirmation.payload.expires_at) }}</dd>
          </div>
        </dl>
        <div class="renewal-confirm-warning">
          交付后客户可立即读取线路。二维码与 Clash 订阅地址由后端按节点链接自动生成。
        </div>
        <p v-if="error" class="renewal-confirm-error" role="alert">
          {{ error }}
        </p>
        <footer>
          <button
            class="outline-action"
            type="button"
            :disabled="busy"
            @click="deliveryConfirmation = null"
          >
            取消</button
          ><button
            class="button button-small"
            type="button"
            :disabled="busy"
            @click="confirmDelivery"
          >
            {{ busy ? "正在交付…" : "确认交付线路" }}
          </button>
        </footer>
      </section>
    </div>
    <div
      v-if="renewalConfirmation"
      class="renewal-confirm-backdrop"
      role="presentation"
      @click.self="renewalConfirmation = null"
    >
      <section
        class="renewal-confirm-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="renewal-confirm-title"
      >
        <header>
          <div>
            <h2 id="renewal-confirm-title">确认人工完成续费</h2>
            <p>确认后将立即延长线路到期时间，此操作由后端保证幂等。</p>
          </div>
          <button
            type="button"
            aria-label="关闭确认窗口"
            :disabled="busy"
            @click="renewalConfirmation = null"
          >
            <X :size="19" />
          </button>
        </header>
        <dl>
          <div>
            <dt>续费订单</dt>
            <dd>{{ renewalConfirmation.order_no }}</dd>
          </div>
          <div>
            <dt>客户</dt>
            <dd>客户 #{{ renewalConfirmation.customer_id }}</dd>
          </div>
          <div>
            <dt>线路</dt>
            <dd>线路 #{{ renewalConfirmation.renewal_line_id }}</dd>
          </div>
          <div>
            <dt>续费周期</dt>
            <dd>{{ renewalConfirmation.billing_days }} 天</dd>
          </div>
        </dl>
        <div class="renewal-confirm-warning">
          请确认线下付款或续费条件已经核实。前端不会执行余额扣款或在线支付。
        </div>
        <p v-if="error" class="renewal-confirm-error" role="alert">
          {{ error }}
        </p>
        <footer>
          <button
            class="outline-action"
            type="button"
            :disabled="busy"
            @click="renewalConfirmation = null"
          >
            取消</button
          ><button
            class="button button-small"
            type="button"
            :disabled="busy"
            @click="submitRenewalCompletion"
          >
            {{ busy ? "正在完成续费…" : "确认并延长线路" }}
          </button>
        </footer>
      </section>
    </div>
  </div>
</template>

<style scoped>
.renewal-confirm-error {
  margin: 12px 0 0;
  color: #a94432;
  font-size: 12px;
  line-height: 1.5;
}
.workspace-alert > span {
  min-width: 0;
  overflow-wrap: anywhere;
}
.sales-user-panel {
  max-width: 720px;
}
.sales-user-form {
  display: grid;
  gap: 16px;
  padding: 18px;
}
.sales-user-form label {
  display: grid;
  gap: 6px;
  min-width: 0;
  color: #526862;
  font-size: 12px;
  font-weight: 650;
}
.sales-user-form input {
  width: 100%;
  min-height: 42px;
  padding: 8px 11px;
  border: 1px solid #c8d6d2;
  border-radius: 5px;
  background: #fff;
  color: #263b37;
}
.sales-user-form small,
.sales-user-note {
  color: #71847e;
  font-size: 11px;
  font-weight: 400;
}
.sales-user-note {
  margin: 0;
  line-height: 1.6;
}
.sales-user-submit {
  justify-self: start;
  min-height: 40px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 8px 14px;
  border-radius: 5px;
  background: #227f70;
  color: #fff;
  font-size: 12px;
  font-weight: 700;
}
.sales-user-submit:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
@media (max-width: 640px) {
  .sales-user-panel {
    max-width: none;
  }
  .sales-user-form {
    padding: 14px;
  }
}
.security-panel {
  max-width: 760px;
}
.security-panel .panel-head p {
  margin: 4px 0 10px;
  color: #71847e;
  font-size: 11px;
}
.security-current-questions {
  display: grid;
  gap: 8px;
  padding: 12px 18px 0;
  margin: 0;
}
.security-current-questions div {
  display: grid;
  grid-template-columns: 70px minmax(0, 1fr);
  gap: 10px;
}
.security-current-questions dt {
  color: #71847e;
  font-size: 11px;
}
.security-current-questions dd {
  margin: 0;
  font-size: 12px;
  overflow-wrap: anywhere;
}
.security-warning {
  margin: 14px 18px 0;
  padding: 10px 12px;
  border-radius: 5px;
  background: #fff3de;
  color: #755b2e;
  font-size: 11px;
  line-height: 1.6;
}
.security-question-form {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
  padding: 18px;
}
.security-question-form label {
  display: grid;
  gap: 5px;
  min-width: 0;
  color: #526862;
  font-size: 11px;
}
.security-question-form input,
.security-question-form select {
  width: 100%;
  min-width: 0;
  min-height: 40px;
  padding: 7px 9px;
  border: 1px solid #c8d6d2;
  border-radius: 5px;
  background: #fff;
  color: #263b37;
}
.security-current-password,
.security-question-note {
  grid-column: 1 / -1;
}
.security-question-note {
  margin: 0;
  color: #71847e;
  font-size: 11px;
}
.security-save {
  justify-self: start;
  min-height: 40px;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 8px 13px;
  border-radius: 5px;
  background: #227f70;
  color: #fff;
  font-size: 12px;
  font-weight: 700;
}
.security-save:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
@media (max-width: 640px) {
  .security-panel {
    max-width: none;
  }
  .security-question-form {
    grid-template-columns: 1fr;
    padding: 14px;
  }
  .security-current-password,
  .security-question-note {
    grid-column: auto;
  }
  .security-warning {
    margin-right: 14px;
    margin-left: 14px;
  }
}
.resource-order-note{display:flex;align-items:center;gap:7px;margin:0 18px 14px;padding:9px 11px;border:1px solid #e8d3ab;border-radius:5px;background:#fff8eb;color:#755316;font-size:10px}
.outreach-panel .panel-head{margin-bottom:10px}
.outreach-hint{margin:10px 0 0;font-size:12px;color:#8a6a3c}
.outreach-stats{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0 12px}
.outreach-stats span{font-size:11px;padding:3px 9px;border-radius:999px;background:#eef3f1;color:#5b726b}
.outreach-stats span[data-tone="bad"]{background:#fdecea;color:#a83e2c}
.outreach-stats span[data-tone="warn"]{background:#fff4e2;color:#8a6321}
.outreach-stats span[data-tone="good"]{background:#e6f6ee;color:#1d6b52}
.outreach-row{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px 16px;padding:13px 2px;border-bottom:1px solid #e4e9e7}
.outreach-row:last-of-type{border-bottom:0}
.outreach-main{display:grid;gap:3px;min-width:0}
.outreach-main b{font-size:13px}
.outreach-main small{font-size:11px;color:#788a85}
.outreach-tags{display:flex;flex-wrap:wrap;gap:6px;margin-top:2px}
.outreach-tags em{font-style:normal;font-size:11px;padding:2px 7px;border-radius:4px;background:#eef3f1;color:#4f6a63;white-space:nowrap}
.outreach-tags em[data-tone="bad"]{background:#fdecea;color:#a83e2c}
.outreach-tags em[data-tone="warn"]{background:#fff4e2;color:#8a6321}
.outreach-tags em[data-tone="good"]{background:#e6f6ee;color:#1d6b52}
.outreach-tags em[data-tone="muted"]{background:#f0f2f1;color:#7c8b86}
.outreach-tags .outreach-noprice{background:#fff4e2;color:#8a6321}
.outreach-message{margin:4px 0 0;padding:8px 10px;background:#f2f6f4;border-left:2px solid #c9dad4;border-radius:4px;font-size:12px;line-height:1.6;color:#3f574f}
.outreach-actions{display:flex;align-items:start;gap:8px}
.outreach-actions .outline-action{min-height:32px;padding:5px 10px;font-size:12px}
.outreach-form{grid-column:1/-1;display:flex;flex-wrap:wrap;align-items:end;gap:10px;padding:10px 12px;background:#f2f6f4;border-radius:6px}
.outreach-form label{display:grid;gap:4px;font-size:11px;color:#6b8078}
.outreach-form select,.outreach-form input{height:34px;padding:0 9px;border:1px solid #cfdad7;border-radius:5px;background:#fff;font-size:12px}
.outreach-form .outreach-note{flex:1;min-width:160px}
.outreach-form .outreach-note input{width:100%}
.outreach-more{margin-top:10px;font-size:12px;color:#3c7f74;font-weight:650}
@media(max-width:760px){.outreach-row{grid-template-columns:1fr}.outreach-actions{justify-content:flex-start}}
.order-pager{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-top:8px;padding:8px 10px;border-top:1px solid #e3ebe7;color:#788a85;font-size:12px}
.order-pager>div{display:flex;gap:6px}
.order-pager button{width:30px;height:30px;display:grid;place-items:center;border:1px solid #d3ded8;border-radius:5px;background:#fff;color:#4e685d}
.order-pager button:disabled{opacity:.4;cursor:not-allowed}
.invite-panel .panel-head{margin-bottom:10px}
.invite-row{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px 16px;align-items:center;padding:12px 2px;border-bottom:1px solid #e4e9e7}
.invite-row:last-of-type{border-bottom:0}
.invite-main{display:grid;gap:3px;min-width:0}
.invite-code{font:15px ui-monospace,monospace;letter-spacing:.18em;color:#1f5c50}
.invite-main small{font-size:11px;color:#788a85}
.invite-actions{display:flex;align-items:center;gap:8px}
.invite-actions .outline-action{min-height:32px;padding:5px 10px;font-size:12px}
.invite-disabled-tag{font-style:normal;font-size:11px;padding:2px 7px;border-radius:4px;background:#f0f2f1;color:#7c8b86}
.invite-form{display:flex;flex-wrap:wrap;gap:10px;margin-top:12px;padding-top:12px;border-top:1px solid #e4e9e7}
.invite-form input{flex:1;min-width:200px;height:36px;padding:0 10px;border:1px solid #cfdad7;border-radius:5px;background:#fff;font-size:12px}
@media(max-width:760px){.invite-row{grid-template-columns:1fr}.invite-actions{justify-content:flex-start}}
.my-channel-list{display:grid;gap:2px}
.my-channel-row{display:flex;align-items:center;justify-content:space-between;gap:14px;padding:11px 2px;border-bottom:1px solid #e4e9e7}
.my-channel-row:last-child{border-bottom:0}
.my-channel-row b{font-size:13px;color:#28433c}
.my-channel-row small{display:block;margin-top:3px;font-size:11px;color:#788a85;word-break:break-all}
.my-channel-row .outline-action{min-height:32px;padding:5px 10px;font-size:12px;white-space:nowrap}
.customer-row-actions{display:flex;align-items:center;gap:8px;flex:none}
.mini-order-button{min-height:28px;padding:0 9px;border:1px solid #b8c8c4;border-radius:5px;background:#fff;color:#2c6f60;font-size:11px;font-weight:650;white-space:nowrap}
.mini-order-button:hover{border-color:#3d9587;background:#f1f6f3;color:#247d70}
@media(max-width:760px){.compact-row{align-items:flex-start;flex-direction:column;gap:8px}.customer-row-actions{width:100%;justify-content:space-between}}
</style>
