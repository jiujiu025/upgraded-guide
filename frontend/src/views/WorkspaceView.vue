<script setup>
import BrandLogo from "../components/BrandLogo.vue";
import { computed, onMounted, reactive, ref, watch } from "vue";
import {
  Activity,
  Bell,
  Box,
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
  Server,
  Settings,
  ShoppingCart,
  X,
} from "lucide-vue-next";
import { useRoute, useRouter } from "vue-router";
import {
  cancelAdminOrder,
  cancelCustomerOrder,
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
  getSalesExpiringLines,
  getSalesOrders,
  markAllNotificationsRead,
  markNotificationRead,
  renewCustomerLine,
  updateCustomerLineNote,
} from "../api";
import { session } from "../api/session";
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
const orderStatus = ref("");
const filters = reactive({ product: "", region: "", status: "", note: "" });
const editingLineId = ref(null);
const noteDraft = ref("");
const renewDraft = reactive({
  lineId: null,
  billing_days: 30,
  payment_method: "contact_manager",
});
const deliveryOrderId = ref(null);
const customerOrderCancellation = ref(null);
const orderActionConfirmation = ref(null);
const customerRenewalConfirmation = ref(null);
const deliveryConfirmation = ref(null);
const renewalConfirmation = ref(null);
const delivery = reactive({
  node_id: "",
  static_ip: "",
  expires_at: "",
  customer_note: "",
  node_link: "",
  subscription_url: "",
  node_qr_data: "",
  subscription_qr_data: "",
});

const workspace = computed(() => route.meta.workspace);
const page = computed(() => route.meta.page || "dashboard");
const storedUser = computed(() => {
  return session.user || {};
});
const clientNav = [
  ["首页", "/client", Gauge],
  ["购买线路", "/buy", ShoppingCart],
  ["我的订单", "/client/orders", PackageSearch],
  ["我的线路", "/client/lines", Server],
  ["续费管理", "/client/renewals", RotateCw],
  ["服务收件箱", "/client/inbox", MessageSquare],
  ["通知中心", "/client/notifications", Inbox],
  ["账户资料", "/client/profile", Settings],
];
const nav = computed(() =>
  workspace.value === "client"
    ? clientNav
    : workspace.value === "sales"
      ? [
          ["销售概览", "/sales", Gauge],
          ["我的客户影响", "/sales/health", Activity],
          ["客户会话", "/sales/inbox", MessageSquare],
        ]
      : [
          ["订单处理", "/admin", Box],
          ["线路健康", "/admin/health", Activity],
          ["全量会话", "/admin/inbox", MessageSquare],
          ["资源池", "/admin/resource-pool", Database],
        ],
);
const titles = {
  dashboard: "客户首页",
  orders: "我的订单",
  order: "订单详情",
  lines: "我的线路",
  line: "线路详情",
  renewals: "续费管理",
  notifications: "通知中心",
  profile: "账户资料",
  sales: "销售工作台",
  admin: "管理员订单",
};
const pageTitle = computed(() => titles[page.value] || "工作台");
const unreadCount = computed(
  () => notifications.value.filter((item) => !item.is_read).length,
);
const filteredOrders = computed(() =>
  orderStatus.value
    ? orders.value.filter((item) => item.status === orderStatus.value)
    : orders.value,
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
const pendingOrders = computed(
  () =>
    orders.value.filter(
      (item) =>
        !["delivered", "completed", "cancelled", "refunded"].includes(
          item.status,
        ),
    ).length,
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
function showSuccess(message) {
  success.value = message;
  setTimeout(() => {
    success.value = "";
  }, 2800);
}
async function logout() {
  try {
    await endSession();
  } finally {
    router.replace("/login");
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
    getCustomerOrders(),
    getCustomerLines(),
    getNotifications(),
    getCustomerRenewals(),
    getCustomerMe(),
  ]);
  orders.value = orderResponse.data;
  lines.value = lineResponse.data;
  notifications.value = notificationResponse.data;
  renewals.value = renewalResponse.data;
  profile.value = profileResponse.data;
}

async function load() {
  loading.value = true;
  error.value = "";
  detail.value = null;
  try {
    if (workspace.value === "client") {
      await loadClientBase();
      if (page.value === "order")
        detail.value = (await getCustomerOrder(route.params.id)).data;
      if (page.value === "line")
        detail.value = (await getCustomerLine(route.params.id)).data;
    } else if (workspace.value === "sales") {
      const [
        dashboardResponse,
        customerResponse,
        orderResponse,
        expiringResponse,
        healthFixture,
      ] = await Promise.all([
        getSalesDashboard(),
        getSalesCustomers(),
        getSalesOrders(),
        getSalesExpiringLines(),
        loadHealthFixture({
          role: "sales",
          username: storedUser.value.username,
        }),
      ]);
      dashboard.value = dashboardResponse.data;
      salesCustomers.value = customerResponse.data;
      salesOrders.value = orderResponse.data;
      expiringLines.value = expiringResponse.data;
      healthImpacts.value = healthFixture.incidents.map((item) => ({
        incident_id: item.id,
        line_name: item.line,
        reason: `演示数据 · ${item.region} ${item.product}`,
        status: item.status,
      }));
    } else {
      orders.value = (await getAdminOrders()).data;
    }
  } catch (requestError) {
    error.value = requestError.userMessage || "数据加载失败，请稍后重试";
  } finally {
    loading.value = false;
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
  order.status === "renewal" &&
  Boolean(order.renewal_line_id) &&
  !order.renewal_completed_at;

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
        getAdminOrders(),
        getManagedLine(order.renewal_line_id),
      ]);
      orders.value = orderResponse.data;
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
  renewDraft.billing_days = 30;
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
  };
}

async function confirmCustomerRenewal() {
  const pending = customerRenewalConfirmation.value;
  if (!pending || busy.value) return;
  const succeeded = await runAction(
    () =>
      renewCustomerLine(pending.line.id, {
        billing_days: pending.billing_days,
        payment_method: pending.payment_method,
      }),
    "续费订单已创建，线路和续费数据已刷新",
  );
  if (succeeded) {
    customerRenewalConfirmation.value = null;
    renewDraft.lineId = null;
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
    subscription_url: "",
    node_qr_data: "",
    subscription_qr_data: "",
  });
}
async function submitDelivery() {
  if (!delivery.node_id || !delivery.expires_at) {
    error.value = "请填写节点 ID 和到期时间";
    return;
  }
  const expiresAt = new Date(delivery.expires_at);
  if (Number.isNaN(expiresAt.getTime()) || expiresAt.getTime() <= Date.now()) {
    error.value = "到期时间必须晚于当前时间";
    return;
  }
  const payload = {
    node_id: Number(delivery.node_id),
    expires_at: expiresAt.toISOString(),
  };
  for (const key of [
    "static_ip",
    "customer_note",
    "node_link",
    "subscription_url",
    "node_qr_data",
    "subscription_qr_data",
  ])
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
        getAdminOrders(),
        getManagedLine(deliveredLine.id),
      ]);
      orders.value = orderResponse.data;
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
                <div v-if="!orders.length" class="inline-empty">暂无订单</div>
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
                    ...new Set(orders.map((item) => item.status)),
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
            <div v-if="!filteredOrders.length" class="workspace-state compact">
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
                v-for="order in filteredOrders"
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
              </button></div
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
                <CircleDollarSign
                  :size="18"
                />支付尚未接通，订单状态以后端记录为准。
              </div>
              <div class="detail-actions">
                <button
                  class="outline-action"
                  @click="router.push('/client/orders')"
                >
                  <ChevronRight class="back-icon" :size="15" />返回订单</button
                ><button
                  v-if="
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
                  <dd>{{ detail.node_link || "—" }}</dd>
                </div>
                <div>
                  <dt>订阅地址</dt>
                  <dd>{{ detail.subscription_url || "—" }}</dd>
                </div>
                <div>
                  <dt>节点二维码数据</dt>
                  <dd>{{ detail.node_qr_data || "—" }}</dd>
                </div>
                <div>
                  <dt>订阅二维码数据</dt>
                  <dd>{{ detail.subscription_qr_data || "—" }}</dd>
                </div>
              </dl>
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
          <template v-else-if="page === 'renewals'"
            ><div class="workspace-alert">
              续费当前只创建待处理订单，不会自动扣款，也不会立即修改原线路到期时间。
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
                  <option :value="30">30 天</option>
                  <option :value="180">180 天</option>
                  <option :value="360">360 天</option>
                </select></label
              ><label
                >支付方式<select v-model="renewDraft.payment_method">
                  <option value="contact_manager">联系经理确认</option>
                  <option value="balance">余额支付（尚未扣款）</option>
                  <option value="online">在线支付（尚未接通）</option>
                </select></label
              >
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
                <span>本月销售额</span
                ><strong>{{ money(dashboard.monthly_sales) }}</strong>
              </article>
              <article>
                <span>本月续费金额</span
                ><strong>{{ money(dashboard.monthly_renewals) }}</strong>
              </article>
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
                  ><em>{{ customer.status }}</em>
                </div>
              </section>
              <section class="workspace-panel">
                <div class="panel-head">
                  <h2>客户订单</h2>
                  <span>{{ salesOrders.length }} 笔</span>
                </div>
                <div v-if="!salesOrders.length" class="inline-empty">
                  暂无客户订单
                </div>
                <div
                  v-for="order in salesOrders.slice(0, 20)"
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
                    ...new Set(orders.map((item) => item.status)),
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
            <div v-if="!filteredOrders.length" class="workspace-state compact">
              <Box :size="28" />
              <p>暂无符合条件的订单</p>
            </div>
            <section
              v-for="order in filteredOrders"
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
              <dl class="admin-order-meta">
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
                    ].includes(order.status) && order.order_type !== 'renewal'
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
                    ) && order.order_type !== 'renewal'
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
                <label
                  >节点 ID *<input
                    v-model="delivery.node_id"
                    type="number"
                    min="1"
                    required /></label
                ><label>静态 IP<input v-model="delivery.static_ip" /></label
                ><label
                  >到期时间 *<input
                    v-model="delivery.expires_at"
                    type="datetime-local"
                    required /></label
                ><label
                  >客户备注<input
                    v-model="delivery.customer_note"
                    maxlength="1000" /></label
                ><label
                  >节点链接<input
                    v-model="delivery.node_link"
                    autocomplete="off" /></label
                ><label
                  >订阅地址<input
                    v-model="delivery.subscription_url"
                    autocomplete="off" /></label
                ><label
                  >节点二维码数据<textarea
                    v-model="delivery.node_qr_data"
                    autocomplete="off"
                  ></textarea></label
                ><label
                  >订阅二维码数据<textarea
                    v-model="delivery.subscription_qr_data"
                    autocomplete="off"
                  ></textarea>
                </label>
                <p>交付凭证仅提交至后端，不会写入浏览器日志或通知正文。</p>
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
            </section></template
          >
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
          当前支付功能尚未接通。确认后只会取消这张待处理订单，不会产生退款。
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
            <p>本步骤只提交续费申请，不会自动扣款或立即延长线路。</p>
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
        </dl>
        <div class="renewal-confirm-warning">
          管理员人工完成续费前，线路到期时间保持不变；余额和在线支付尚未接通。
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
              : "取消操作会改变订单状态且无法通过当前页面恢复，请再次核对订单与客户。"
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
          交付后客户可立即读取线路。节点链接、订阅地址等凭据仅在已填写时提交。
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
</style>
