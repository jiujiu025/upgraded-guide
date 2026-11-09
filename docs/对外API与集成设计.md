# 对外 API 与系统集成设计

> 状态：**P0 已实施（2026-11-09）**，P1/P2 仍为设计。问题（2026-11-09）：「API 对接是否需要完善？」本文先给结论，再给分档方案，最后列明确不做的东西。P0 实施细节见文末 §7。

## 0. 结论

**分三档，只有 P0 是上线前必须做的；P1/P2 取决于有没有真实的对接方。**

| 档 | 内容 | 什么时候做 | 工作量 |
| --- | --- | --- | --- |
| P0 | 生产关掉 `/docs` 与 `/openapi.json`、CORS 支持多来源、公开接口加限流 | **上线前必做** | 0.5 天 |
| P1 | API Key 体系（客户自有 ERP 查线路、拉订单） | 有客户明确提出要接 API 时 | 2–3 天 |
| P2 | 对外 webhook（订单/线路状态推给客户系统） | 有客户要求「状态变了主动通知我」时 | 2 天 |

判断依据：**如果只是自家官网，不要上 API Key。**官网和前台是同一套 SPA，走现有会话 + CORS 白名单就够了；多一层认证就多一套密钥泄露面。P1/P2 是给「第三方系统」用的，没有第三方就别做。

## 1. 现状盘点

| 维度 | 现状 | 位置 |
| --- | --- | --- |
| 公开接口 | 6 个：`/api/public/products`、`/products/{id}`、`/regions`、`/orders/preview`、`/register`、`/invite-codes/{code}` | `api/routes.py:618-708,984` |
| 认证方式 | Cookie 会话 + CSRF；**没有** API Key / OAuth 客户端凭证 | `api/deps.py`、`session_service.py` |
| CORS | 单来源白名单 `frontend_origin`，生产强制 https 且禁止 `*` | `main.py:35`、`core/config.py:151` |
| 接口文档 | `/openapi.json`、`/docs` **默认全开**（实测 200） | `main.py:34` |
| 公开接口限流 | 注册、邀请码校验有限流；**产品列表与算价接口无限制** | `registration_service.py` |
| 支付回调 | 已有（微信支付异步通知 + 对账任务） | `api/finance_routes.py`、`tasks/payments.py` |
| 出站集成 | 已有供应商目录/报价/库存拉取（定时任务） | `tasks/suppliers.py` |
| 对外 webhook | 无 | — |

一句话：**对内够用，对外缺一层门。**

## 2. P0：生产加固（必做，0.5 天）

### 2.1 关闭接口文档

现在 `FastAPI(title=..., version=...)` 没传 `docs_url`，等于把全部接口结构、字段、错误码挂在公网。

```
FastAPI(
    title=..., version=...,
    docs_url=None if settings.environment == "production" else "/docs",
    redoc_url=None if settings.environment == "production" else "/redoc",
    openapi_url=None if settings.environment == "production" else "/openapi.json",
)
```

开发/测试环境保留，生产彻底 404。**不要**用中间件拦 `User-Agent`，那不是安全边界。

### 2.2 CORS 支持多来源

现状 `allow_origins=[settings.frontend_origin]`，官网独立域名（如 `99ip.com`）会被浏览器拦掉。

- 新增 `frontend_origins: str = ""`（逗号分隔，追加白名单），保留 `frontend_origin` 作为主来源，二者合并去重。
- 校验沿用现有规则：每个来源必须 https、不得带路径/查询/凭据、生产禁止 `*` 与 localhost。
- 只放行**自家域名**。客户要求把自己的系统域名加进来时，那是 P1 的 API Key 场景，不是加 CORS。

### 2.3 公开接口限流

`/public/products`、`/public/orders/preview` 目前无限流，一个脚本就能把算价接口刷成压测。

- 复用现有 limiter（`registration_service._enforce` 同一套后端，memory/redis 可切）。
- 新配置 `public_rate_limit: int = 60`（每 IP 每分钟），作用于两个只读公开接口。
- 超限返回 429 + `Retry-After`，与登录限流口径一致。

### 2.4 验收

1. `ENVIRONMENT=production` 启动后 `/docs`、`/openapi.json`、`/redoc` 全部 404；dev 环境仍 200。
2. 两个白名单域名都能跨域成功；非白名单域名预检被拒。
3. 公开接口第 61 次/分钟 → 429；第 60 次正常。

## 3. P1：API Key 体系（按需，2–3 天）

**触发条件**：有客户说「把线路到期时间同步到我们的系统」「我们 ERP 要自动拉订单」。没有这种话就不做。

### 3.1 数据模型

表 `api_clients`：

| 列 | 说明 |
| --- | --- |
| id | 主键 |
| name | 对接方名称（如「XX 客户 ERP」） |
| key_prefix | 密钥前 8 位明文，列表页展示用，便于识别是哪个 key |
| key_hash | `sha256(salt + key)`，**不存明文**，与密码同级别处理 |
| scopes | 逗号分隔，如 `products:read,orders:read` |
| rate_limit_per_minute | 每客户端限流，默认 120 |
| is_enabled / expires_at | 停用与过期 |
| last_used_at | 最近调用时间，用于发现僵尸 key |
| created_by / created_at | 审计 |

表 `api_client_requests`（可选，只做按分钟聚合，不存明细）：`client_id, minute, count, error_count`。明细全存会让库爆掉，聚合够用。

### 3.2 密钥与认证

- 格式：`ipx_live_<32 字节 base32>`，**只在创建时显示一次**，之后只能轮换不能查看。
- 认证依赖 `require_scope("orders:read")`：先看 `X-API-Key`，命中则按 scope 校验；未命中回落到现有会话认证。两套并存，互不影响。
- 失败一律 401 + 统一文案，不区分「key 不存在 / 已停用 / 已过期」。
- 每个 key 独立限流（按 key 计数，不是按 IP——对方可能走同一出口）。

### 3.3 scope 清单（够用即止）

| scope | 允许的端点 | 说明 |
| --- | --- | --- |
| products:read | `/public/products`、`/public/regions` | 公开数据，scope 只用于限流归属 |
| orders:read | `/api/ext/orders`、`/api/ext/orders/{id}` | **只返回该 key 绑定客户的数据** |
| lines:read | `/api/ext/lines` | 线路状态、到期时间、流量 |
| orders:write | `POST /api/ext/orders` | 可选。开了就等于让对方系统能下单，默认不给 |

`/api/ext/*` 是一组**新写的窄接口**，不复用后台接口：字段少、语义稳定、不返回内部 ID 与成本。直接开放 `/api/admin/*` 或 `/api/customer/*` 是错的——前者权限模型对不上，后者绑死会话。

### 3.4 管理页

`/admin/api-clients`（super_admin）：创建（选客户、选 scope、设过期）、停用、轮换（旧 key 立即失效）、查看最近调用时间。创建后弹一次明文 + 复制按钮。

### 3.5 审计

- 创建/停用/轮换写 `audit_logs`。
- 每次调用至少记录 `client_id` 到请求日志（现有 `RequestLoggingMiddleware` 加一个字段），不做逐条落库。

## 4. P2：对外 webhook（按需，2 天）

**触发条件**：客户说「订单状态变了要主动推给我们」，而不是「我们自己来查」。

### 4.1 事件

| 事件 | 触发点 |
| --- | --- |
| `order.created` | 订单创建成功（含代客下单） |
| `order.paid` | 收款确认 / 在线支付成功 |
| `order.delivered` | 交付完成 |
| `line.expiring` | 到期前 N 天（与站内提醒同一任务触发） |

### 4.2 投递

直接复用通知分发的成熟模式，不重新发明：

- 表 `webhook_endpoints`（url、secret、events、is_enabled、customer_id）+ `webhook_deliveries`。
- 幂等靠 `(event_id, endpoint_id)` 唯一约束；同一事件重发不会重复。
- 重试：1 分钟 / 5 分钟 / 30 分钟三次，之后 `failed`，管理页可见并支持手动重放。
- 签名头：`X-99IP-Signature: t=<unix>,v1=<hmac_sha256(secret, t + "." + body)>`，接收方校验 5 分钟内有效，防重放。
- 超时 5 秒，只认 2xx。

### 4.3 不做

- 不做「全量事件订阅」（只给四类状态变更）。
- 不做双向同步（对方改单我们不接）。
- 不做 webhook 的图形化调试台。

## 5. 明确不做

1. **GraphQL**——一个后台管理系统，REST + 固定字段足够，GraphQL 只会让权限校验变难。
2. **对外暴露后台管理接口**（确认订单、交付、退款、改价）——这些必须人操作，不开放给机器。
3. **第三方 OAuth 登录**（微信/钉钉扫码登录后台）——内部就几个人，账号密码 + 会话够用。
4. **客户子账号体系**——等真有客户要求「我们公司三个人要分权限」再做。
5. **公开注册开放 API**——注册必须走官网表单 + 邀请码，不开放机器批量注册。

## 6. 建议节奏

1. **本周**：做 P0（半天），跟 M1 生产部署一起验收。这一档不做，等于把接口文档和无限流的算价接口直接挂公网。
2. **等信号**：把「客户要 API」记进需求池，出现第一个明确对接方时再做 P1；P1 做的时候只开 `orders:read` + `lines:read`，`orders:write` 默认不给。
3. **P2 最后**：只有当客户明确要「推送」而不是「查询」时才做。

一句话给玖夜：**P0 必做（半天），P1/P2 先不做**——你现在没有第三方对接方，提前建 API Key 体系等于给自己加维护负担和攻击面。

## 7. P0 实施记录（2026-11-09）

| 项 | 落地 | 位置 |
| --- | --- | --- |
| 关闭接口文档 | `api_docs_paths(environment)`：production 返回三个 None，dev/test 保留 | `app/main.py` |
| CORS 多来源 | 新增 `FRONTEND_ORIGINS`（逗号分隔），与 `FRONTEND_ORIGIN` 合并去重为 `settings.cors_origins`；额外来源在生产同样必须 https、禁 localhost、禁路径/凭据 | `app/core/config.py`、`app/main.py` |
| 公开接口限流 | 新增 `PUBLIC_RATE_LIMIT`（默认 60 次/分钟/IP），作用于 `/public/products`、`/public/products/{id}`、`/public/regions`、`/public/orders/preview` | `services/registration_service.py`、`api/routes.py` |

实现细节与取舍：

- **公开限流四个口共用一个计数桶**（`public_read`）。分开计数会让攻击者按端点分摊额度；合在一起对正常浏览没有影响。
- 新增 `tests/conftest.py`，每个用例前清空进程内限流计数。限流后端是模块级单例，跨用例累计会让后面的用例莫名 429。
- 生产**不要**通过关闭 `SecurityHeadersMiddleware` 或加 IP 白名单来"代替"限流，限流就是限流。

验收（`tests/test_public_api_hardening.py`，9 条）：

1. production 下 `/docs`、`/redoc`、`/openapi.json` 全部不存在；dev 下都正常。
2. 多来源合并去重；通配符在任意环境都拒绝；生产拒绝 http 与 localhost 的额外来源。
3. 第 61 次 `/public/products` → 429；`/public/orders/preview` 同样计数。
4. 公开限流不影响需要登录的接口。

剩余（P1/P2）按 §0 的判断继续挂着：**没有明确对接方就不做**。
