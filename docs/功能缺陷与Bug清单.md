# 功能缺陷与 Bug 清单

- 审查时间：2026-11-09
- 范围：`backend/app`（API / 服务 / 定时任务，约 8.4k 行）、`frontend/src` 主要视图与路由、迁移与测试
- 方法：基线测试 → 静态检查（ruff `F,E9`）→ 关键链路走查 → 对可疑点写最小复现脚本（直连路由函数 + 内存 SQLite）
- 基线：后端 **262 passed / 6 skipped**（含 26 个回归用例；2026-11-09 下午 M0 收尾新增 6 个密码与限流用例），前端 18 passed；`vite build` 通过

## 0. 结论

两条**必现 500** 已定位并修复（余额支付、资源池下单）。2026-11-09 玖夜拍板「业绩按收款算、续费接支付」后，2.5、2.6 已落地；同日又修完**资金闸门（2.1、2.2、2.7）、到期状态（2.4）、改密入口（2.3）与收件箱顺序（2.8）**。至此 8 项 P1 全部关闭。同日下午 M0 收尾批次修复 P2-3.1（bcrypt 72 字节，四处密码入口全部补齐显式拒绝）、P2-3.2（找回密码弱口令，complexity 校验早已覆盖）、P2-3.3（登录限流改双键：IP 总量 30 次/分钟 + IP×用户名 10 次/分钟，办公室 NAT 集中登录不再互踩）。本轮抽查的客户/销售数据范围过滤未发现越权，但这不等于完整渗透测试。

| 级别 | 数量 | 性质 |
| --- | --- | --- |
| P0 已修复 | 2 | 功能 100% 不可用，含资金链路 |
| P1 已修复 | 8 | 资金闸门、到期状态、业绩口径、续费支付、改密入口、收件箱（2026-11-09 全部关闭） |
| P1 待修 | 0 | — |
| P2 已修复 | 3 | 3.1 密码 72 字节、3.2 找回弱口令、3.3 登录限流双键（2026-11-09 M0 收尾） |
| P2 待排期 | 5 | 口径、边界、性能、可用性 |
| P3 | 5 | 死代码 / 死状态 / 健壮性 |

---

## 1. 已修复（P0）

### 1.1 余额支付对"有销售归属的客户"必现 500

- 位置：`backend/app/api/finance_routes.py:501`
- 现象：`sqlite3.ProgrammingError: Error binding parameter 9: type 'User' is not supported`
- 根因：`accrue_sales_commission(db, payment, customer, user)` 第 4 个参数是 `actor_user_id: int | None`，这里传了整个 `User` 对象，落库时绑定到 `sales_commission_entries.actor_user_id`（Integer）直接报错。
- 触发条件：客户的 `sales_id` 非空（有归属销售），即绝大多数真实客户。无销售的客户反而正常，所以联调时容易漏掉。
- 影响：余额支付 100% 失败；事务回滚，钱不会扣，但客户端只看到"服务暂时不可用"。
- 修复：改为 `user.id`。
- 回归用例：`tests/test_payment_and_resource_order.py::test_balance_payment_succeeds_and_records_commission_actor`（校验订单变 paid、余额扣减、佣金 15%、`actor_user_id` 正确）。

### 1.2 资源池下单必现 500（订单中心不可用）

- 位置：`backend/app/api/routes.py:1108`（修复前）
- 现象：`NameError: name 'retail_rule' is not defined`
- 根因：审计文案引用了从未赋值的 `retail_rule`；ruff `F821` 可静态发现。
- 影响：`POST /api/admin/order-center/orders` 100% 失败，前端"订单中心 → 新建资源池订单"完全不可用；`db.flush()` 已写入的订单被外层 `except` 回滚。
- 修复：下单前按（分类、国家、30 天档）查出 `RetailPriceRule`，审计写 `source_version`；取不到时回退文案。
- 回归用例：`tests/test_payment_and_resource_order.py::test_resource_pool_order_creation_succeeds`（180 天 × 2 条 = ¥720，同时钉住"线性无折扣"口径与审计内容）。

---

## 2. P1：建议本轮修

### 2.1 已付款订单可以被"取消"，且不产生退款（✅ 2026-11-09 已修复）

- 位置：`backend/app/api/routes.py`（`admin_cancel_order`）
- 原现象：只排除 `partially_delivered/delivered/completed/cancelled/refunding/refunded`，`paid`、`processing`、`pending_delivery` 都能取消；取消只改 `order.status`，不写 `PaymentRefund`、不冲销佣金、不退回余额。
- 现行为：`paid`、`processing` 进入黑名单，提示"已收款订单请走退款"；同时统一走 `ensure_order_cancellable`，存在 `pending` / `manual_review` 支付单时一律拒绝。`pending_delivery` 仍允许取消（未发货，属于正常撤单）。
- 回归：`tests/test_order_cancel_and_line_expiry.py::test_admin_cannot_cancel_paid_order`。

### 2.2 未付款订单可以直接确认并交付（✅ 2026-11-09 已修复）

- 位置：`backend/app/services/order_service.py`（新增 `ensure_payment_settled`）、`routes.py` 确认/交付接口
- 原现象：`pending_payment` 允许确认，交付接口完全不看 `payment_status`，流程可以是"未支付 → 确认 → 待交付 → 已交付"。
- 现规则：
  - `balance` / `online`：`payment_status` 必须是 `paid` 或 `manually_confirmed`，否则 409「订单尚未收款」；确认与交付**各校验一次**——只拦确认不够，状态可能被其他路径推到 `pending_delivery`。
  - `contact_manager`（线下转账/对公）：保留先确认后到账的现网流程，不拦；但确认与交付的审计文案都写入 `收款状态` 与 `收款方式`，事后可对账。
- 回归：`tests/test_payment_gate_and_inbox.py` 的 `test_unpaid_online_order_cannot_be_confirmed_or_delivered`、`test_paid_order_can_be_confirmed_and_delivered`、`test_contact_manager_order_keeps_offline_flow`。

### 2.3 修改密码页面在路由层被自我重定向，UI 无入口（✅ 2026-11-09 已修复）

- 位置：`frontend/src/router/index.js`、`WorkspaceView.vue`（安全中心）、`ChangePasswordView.vue`
- 原现象：已登录用户访问 `/change-password`，守卫立即 `return /{workspace}`，页面永远打不开；安全中心只有"密保问题"。
- 现行为：
  - 删掉 `/change-password` 的自我重定向；工作区校验改为「路由显式声明 `meta.workspace` 时才比对」，改密页不带 workspace，三端共用。
  - 安全中心（客户/销售/管理员）新增「登录密码」面板，按钮跳 `/change-password`。
  - 改密成功后的"重新登录"按角色跳 `/admin/login`、`/sales/login` 或 `/login`（原来一律跳 `/login`，销售和管理员会落到客户端登录页）。
- 回归：`frontend` 构建通过（路由为纯前端改动，未新增单测）。

### 2.4 线路到期后状态永远停在 `active`（✅ 2026-11-09 已修复）

- 位置：`backend/app/tasks/lines.py`（新增 `expire_lines`）、`tasks/registry.py`、`core/config.py`
- 原现象：全仓没有任何代码把 `CustomerLine.status` 写成 `expired`；前端 `WorkspaceView.vue` 用 `status === "active"` 统计"正常线路"，所以该数字只增不减。
- 现行为：新增定时任务 `expire_lines`（每小时），把 `status == "active" AND expires_at <= now - grace` 置为 `expired`，并发一次「线路已到期」通知（按"线路 + 到期日"去重，续费后再次到期可再提醒）。
  - 只翻 `active`：`disabled`（人工停用）与 `deleted` 不动，避免时间任务改写人工状态；续费成功由 `settle_renewal` 把 `expired` 拉回 `active`。
  - 宽限期由 `LINE_EXPIRY_GRACE_HOURS` 控制，默认 0（到期即标），取值 0–720。
  - 任务排在 `line_expiry_reminders` 之前，同一轮里先落状态再算即将到期。
- **连带修复**：`renewal_outreach_service` 的清单原来只查 `status == "active"`，却声称包含"刚过期"的线路——落 `expired` 后这批最该打电话的客户会消失。已改为 `status in {active, expired}`。
- 回归：`tests/test_order_cancel_and_line_expiry.py` 中 `test_expire_lines_only_touches_active`、`test_expire_lines_is_idempotent_and_notifies_once`、`test_expire_lines_respects_grace_hours`、`test_outreach_queue_includes_expired_lines`。

### 2.5 销售"本月销售额"包含未付款订单，且月界按 UTC 切（✅ 2026-11-09 已按"收款口径"修复）

- 位置：`backend/app/api/routes.py`（`received_revenue_cents` + `sales_dashboard`）；前端 `WorkspaceView.vue` 卡片文案
- 原现象：只排除 `cancelled/refunding/refunded`，`pending_payment`、`pending_confirm` 全额计入；`month_start` 用 UTC 零点，上海时间每月 1 日 00:00–08:00 的订单会算进上月。
- 现口径（玖夜 2026-11-09 拍板：**业绩按收款算**）：
  - 普通单 = 区间内 `paid_at` 命中、`purpose='order_payment'`、状态 `paid/refunded` 的 `PaymentOrder` 金额之和；
  - 续费单同理，单独统计；
  - 区间内已完成的退款按 `created_at` 冲减（退款不追溯改写上月业绩）；
  - 月界改为 `local_day_range_utc`（上海时区本月 1 日 00:00 至今），与财务概览一致。
- 前端文案同步改为"本月回款 / 本月续费回款"，避免继续被读成下单额。
- 回归：`backend/tests/test_renewal_payment.py::test_sales_dashboard_counts_received_cash_only`。

### 2.6 续费：可选"余额支付/在线支付"但根本不会扣款；周期选项与后端档位不一致（✅ 2026-11-09 已接支付）

- 位置：`backend/app/services/order_service.py`（`initial_order_status` / `is_payable_order` / `settle_renewal`）、`backend/app/api/finance_routes.py`；前端 `WorkspaceView.vue` 续费表单
- 原现象 A：续费订单初始状态恒为 `renewal`，而余额/在线支付接口要求 `status == "pending_payment"`，选"余额支付"后订单躺着不动。
- 原现象 B：周期写死 `30/180/360`。后端对 30 天商品支持 `30/90/180/360`，对 180 天商品只支持 180；给 180 天线路选 30 天直接 422。
- 现行为：
  - 续费单选 `balance/online` → 初始状态 `pending_payment`，进钱包"待支付订单"；付成功即 `settle_renewal` 自动延长到期时间、订单转 `completed`、按收款计提佣金；
  - 选 `contact_manager` → 仍为 `renewal`，由管理员人工完成（状态 `payment_status=manually_confirmed`，因无收款记录故不计业绩）；
  - 管理员"线下收款登记"同样支持续费单，收款后自动完成续期并计入业绩；
  - 续费退款会回退对应天数（若该线路之后又完成过续费则跳过，交人工核对），并冲销佣金；
  - 线路周期由 `GET /customer/lines` 的 `periods` 字段下发（`pricing_service.periods_of`），前端按实际档位渲染，不再写死。
- 回归：`backend/tests/test_renewal_payment.py`（4 个用例，覆盖余额续费、旧"续费中"单补付、业绩口径、退款回退）。

### 2.7 客户可以在支付进行中取消订单（✅ 2026-11-09 已修复）

- 位置：`backend/app/services/order_service.py`（`active_payment_order` / `ensure_order_cancellable`）、`routes.py` 两个取消接口、`finance_routes.py`（`_record_paid_order`）
- 原现象：`customer_cancel_order` 不检查是否存在 `PaymentOrder.status in {pending, manual_review}`。客户发起在线支付后点取消，订单变 `cancelled`；回调到达后本地把支付单置 `manual_review`，钱可能已付。
- 现行为分三层：
  1. **取消闸门**：客户和管理员取消前都调 `ensure_order_cancellable`，`pending` → 409「订单正在支付中」，`manual_review` → 409「请先到财务核对」，口径与 `close_expired_orders` 一致。
  2. **管理员黑名单**：`paid`、`processing` 不允许取消（见 2.1）。
  3. **回调兜底**：`_record_paid_order` 发现订单已 `cancelled/refunding/refunded` 时不再推进订单，支付单转 `manual_review` 并通知管理员（按 `out_trade_no` 去重）；`settle_renewal` 入口同样拒绝已关闭订单，避免"单子取消了、线路却续上了"。
- 未做：客户自助"撤销未完成支付"（把 `pending` 支付单作废后重新下单）。这是体验优化，仍按原计划单独排期。
- 回归：`tests/test_order_cancel_and_line_expiry.py` 中 `test_customer_cannot_cancel_order_while_payment_pending`、`test_customer_cannot_cancel_order_under_manual_review`、`test_customer_can_cancel_without_payment_record`、`test_late_callback_does_not_revive_cancelled_order`、`test_cancelled_renewal_cannot_extend_line`。

### 2.8 会话消息超过 100 条后，只显示最早的 100 条（✅ 2026-11-09 已修复）

- 位置：`backend/app/api/inbox_routes.py`（`list_messages`）、`frontend/src/views/InboxView.vue`
- 原现象：`created_at` 升序 + `page_size=100`，第 101 条之后的新消息不在首屏，前端又没有翻页控件。
- 现行为：接口新增 `order=asc|desc`（默认 `asc`，老客户端行为不变）；前端改为 `order=desc` 取最近 100 条后反转展示，并在列表顶部提示「更早的 N 条消息未显示」。
- 说明：完整的历史翻页仍未做（需要向上滚动加载），本轮只保证"打开会话看到的是最新消息"。
- 回归：`tests/test_payment_gate_and_inbox.py::test_inbox_messages_can_return_latest_first`（同时钉住默认 `asc` 不变）。

---

## 3. P2：口径 / 边界 / 性能

| # | 问题 | 位置 | 说明与建议 |
| --- | --- | --- | --- |
| 3.1 | ~~bcrypt 72 字节截断~~ | `core/security.py:8-13` | ✅ 2026-11-09 M0 收尾修复：四处密码入口（注册 `RegisterIn`、自助改密、管理员重置、找回密码）全部显式拒绝超 72 字节，错误信息带实际字节数；中文多字节密码用例已覆盖（`tests/test_password_policy_and_login_limit.py`）。 |
| 3.2 | ~~找回密码可设弱密码~~ | `schemas/common.py:183` | ✅ 已修复：`PasswordRecoveryIn.validate_new_password` 补齐大小写+数字+符号与 72 字节校验（复杂度校验在早前批次已加，本轮复核确认）。 |
| 3.3 | ~~登录限流按出口 IP，10 次/分钟~~ | `registration_service.py` | ✅ 2026-11-09 M0 收尾修复：改双键——`login_ip`（IP 总量 30 次/分钟，新增配置 `LOGIN_IP_RATE_LIMIT`）+ `login_account`（IP×用户名 10 次/分钟）。办公室 NAT 集中登录按账号分桶互不影响；换用户名轰炸由 IP 总量兜底。限流移入登录函数体内（需按用户名分桶）。 |
| 3.4 | 财务"销售佣金"列表不受日期区间约束 | `finance_routes.py:240-246` | 上方汇总按区间，下方列表是全时段累计，同一页面两个口径。建议加 `created_at` 区间过滤。 |
| 3.5 | ~~销售看板"7 天内到期"未过滤线路状态~~ | `routes.py` | ✅ 2026-11-09 已加 `status == "active"`。 |
| 3.6 | 线路列表与触达队列 N+1 | `routes.py:164-185`、`renewal_outreach_service.py:180` | 每条线路 3 次 `db.get`；触达队列每条线路查一次价目表。300 条线路约 900+ 次查询。建议批量取 Product/Node/Region 与价目表。 |
| 3.7 | 手动触发定时任务是同步执行 | `task_admin_routes.py:51-61` | 供应商目录/报价刷新要打外部接口，可能几十秒；HTTP 请求会一直挂着直到超时。建议改成 enqueue 后立即返回，前端轮询流水。 |
| 3.8 | 多处硬上限且无分页 | `routes.py:395`（客户 200）、`1019`（订单 1000）、`1009`（订阅 2000） | 超过上限的数据静默消失，界面没有任何提示。建议补分页或至少返回 `truncated: true`。 |
| 3.9 | 收件箱禁止一切 URL | `inbox_service.py:16` | 正则 `https?://\S+` 会拦掉客户发的测速链接、截图链接、工单链接。若确为防凭据泄露，建议只拦代理协议（vless/vmess/ss）与"密码/token=..."，普通链接放行。 |

---

## 4. P3：死代码 / 死状态 / 健壮性

1. `must_change_password` 是死字段：`routes.py:257` 每次登录强制置 `False`，管理员重置密码时置 `True` 也不会有任何效果。
2. `Order.status` 的 `refunding`、`payment_status` 的 `partially_refunded` 从未被写入，只出现在过滤条件里；状态机文档与实现已脱节。
3. `ruff check app --select F,E9` 仍有 12 项：11 个未使用导入（`finance_routes.py`、`wallet_service.py`、`payment_gateway.py` 等）+ `supplier_routes.py:224` 未使用变量 `settings`。
4. `BuyFlow.vue:54` 直接 `JSON.parse(sessionStorage...)`，本地存储被写坏时整个产品列表加载失败，报错还是"网络"类文案。
5. `Customer.email` 没有唯一约束，注册/后台创建都允许重复邮箱；不影响登录，但客户合并、通知去重会踩坑。

---

## 5. 建议修复顺序

1. **已完成（2026-11-09）**：P1-1～P1-8 全部关闭——资金闸门（取消、收款、回调兜底）、到期状态、业绩口径、续费支付、改密入口、收件箱顺序。
2. **已完成（2026-11-09 下午 M0 收尾）**：P2-3.1（密码 72 字节）、P2-3.2（找回弱口令复核）、P2-3.3（登录限流双键）。
3. **下一步建议**：P2-3.7（手动触发定时任务同步执行）在供应商接口慢时会拖住请求；P2-3.4（佣金列表区间）口径问题。
4. **收尾**：P3-3（ruff 未使用导入/变量清理，12 项）、会话历史向上翻页。

## 6. 复现与验证

```bash
cd backend
./.venv/Scripts/python.exe -m pytest -q                  # 238 passed / 6 skipped
./.venv/Scripts/python.exe -m pytest tests/test_payment_and_resource_order.py -q
./.venv/Scripts/python.exe -m ruff check app --select F,E9
cd ../frontend && npm test -- --run                      # 18 passed
```

本轮改动文件：

- `backend/app/api/finance_routes.py`（余额支付佣金记录人）
- `backend/app/api/routes.py`（资源池下单审计引用）
- `backend/tests/test_payment_and_resource_order.py`（新增 2 个回归用例）

> 说明：P1 及以下均未改动代码，避免在未确认业务口径前自行改规则。
