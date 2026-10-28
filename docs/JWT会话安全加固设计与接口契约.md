# JWT 会话安全加固：设计与接口契约

状态：已按确认方案实现于本地工作区；未部署。

## 1. 现状与目标

访问 JWT 有效期为 10 分钟；刷新凭据由 HttpOnly Cookie 承载，前端不持久化访问令牌。

目标是短时效访问令牌、可立即撤销的刷新会话、刷新重放检测，以及账号安全变更后统一失效。现有 `require_roles`、客户归属和销售范围查询继续由后端执行。旧版不含会话 ID 的 JWT 在切换后拒绝，用户需重新登录。

## 2. 推荐会话模型

- 访问令牌：HS256 JWT，默认 10 分钟；包含 `sub`、`sid`、`jti`、`iat`、`exp`、`iss`、`aud`、`token_type=access`、`auth_version`。角色不作为授权依据；每次请求验证签名、声明、数据库会话未撤销/未过期、用户启用且版本匹配，再从数据库读取当前角色和资源范围。
- 刷新令牌：密码学安全随机数生成的 256 位不透明值，绝对有效期 7 天，不滑动续期。仅在 `HttpOnly`、`Secure`、`SameSite=Lax`、`Path=/`、无 `Domain` 的 `__Host-ops_refresh` Cookie 中保存；响应正文、日志和审计均不包含原值。
- 数据库：`users.auth_version`；`auth_sessions` 存储用户、签发版本、轮换序号、创建/过期/最近使用/撤销时间和原因；`auth_refresh_tokens` 存储会话 ID、轮换序号、令牌 HMAC-SHA256 哈希、签发和消费时间。哈希建立唯一索引，原令牌不落库；HMAC 使用独立的 `SESSION_TOKEN_HASH_KEY`，生产必须显式提供。
- 刷新在同一事务中锁定会话行，验证当前令牌，消费旧哈希、递增序号并写入新哈希，然后提交和设置新 Cookie。已消费哈希再次出现时撤销该会话并返回 401；未知哈希只返回 401，避免任意猜测触发撤销。并发轮换由数据库锁与唯一约束保护。
- 密码变更/重置、停用、角色变更均在同一事务中递增 `auth_version`、撤销该用户全部会话并写入审计。管理员重置客户、销售或管理员密码时须二次确认，设置 `must_change_password`；下次登录仅可修改密码。单会话管理员撤销不影响同用户其他会话。账号停用还由请求时 `is_active` 校验拦截。

## 3. Cookie、CSRF 与跨域

- 登录、注册、刷新及登出的 Cookie 相关请求校验 `Origin`；只允许精确配置的前端来源，拒绝不可信来源。刷新和登出还需 `X-CSRF-Token`。CSRF 令牌由服务端 HMAC 会话 ID 与当前轮换序号生成，在登录/刷新响应及 `GET /api/auth/csrf` 返回；前端仅存内存。CSRF 令牌不会写入 Cookie 或持久化浏览器存储，错误响应不回显凭据。
- 推荐同源部署：浏览器访问同一 HTTPS 域名的页面与 `/api`，沿用现有 Nginx/Vite 代理。跨子域仅支持同站点 HTTPS，例如页面与 API 属于同一主域；Cookie 保持 API 主机专用，不设置父域 `Domain`。前端对 API 设置 `withCredentials`，后端 CORS 只允许精确来源及凭据，绝不使用 `*`。不同站点跨域不作为本阶段支持目标。
- 开发环境也保持 `Secure` Cookie，使用本地 HTTPS 的 `localhost` 前端和 Vite `/api` 代理；`FRONTEND_ORIGIN` 与实际浏览器来源一致。不得为方便本地调试在生产关闭 `Secure` 或放宽 CORS。缓存和代理不得记录 Cookie、Authorization、CSRF 头或令牌响应。
- `/api/auth/csrf` 只在当前刷新 Cookie 对应未撤销的有效会话时返回令牌，并设置 `Cache-Control: no-store`。客户端刷新前先取得 CSRF；若 Cookie 不存在或失效，则转为访客状态。

## 4. 接口契约

| 接口 | 输入 | 输出与状态 | 权限与副作用 |
| --- | --- | --- | --- |
| `POST /api/auth/login` | 现有用户名/密码 JSON | 200：`access_token`、`token_type=bearer`、`expires_in=600`、`csrf_token`、`user`；设置刷新 Cookie | 现有限流与锁定；创建会话。失败 401，不设置 Cookie |
| `POST /api/public/register` | 现有注册 JSON | 201：同登录响应及 Cookie | 仅新客户；用户、客户档案、会话同事务创建 |
| `GET /api/auth/csrf` | 刷新 Cookie | 200：`csrf_token`；无效时 401 | 不轮换令牌，不返回访问令牌 |
| `POST /api/auth/refresh` | 刷新 Cookie、`X-CSRF-Token`，空正文 | 200：同登录响应及新刷新 Cookie；过期/撤销/重放 401，CSRF 失败 403 | 原子轮换；重放撤销当前会话 |
| `POST /api/auth/logout` | 刷新 Cookie、`X-CSRF-Token`，空正文 | 204；清除刷新 Cookie | 撤销当前会话；已失效 Cookie 幂等清除 |
| `GET /api/auth/me` | `Authorization: Bearer <access_token>` | 200：现有 `UserOut`；无效 401 | 从数据库确认会话、用户和当前角色 |
| `GET /api/admin/users/{user_id}/sessions` | 访问 JWT | 200：会话 ID、时间、是否当前、撤销状态；不含令牌或哈希 | 管理员按角色边界查看 |
| `POST /api/admin/users/{user_id}/sessions/{session_id}/revoke` | 访问 JWT，空正文 | 204；无权 403，未找到 404 | 撤销指定会话并审计 |
| `POST /api/admin/users/{user_id}/sessions/revoke-all` | 访问 JWT，空正文 | 200：`revoked_count` | 撤销目标用户全部会话并审计 |
| `POST /api/auth/change-password` | 当前密码、新密码 JSON + 访问 JWT | 204；清除当前刷新 Cookie | 更新密码、撤销自己的全部会话，需重新登录 |
| `POST /api/admin/users/{user_id}/reset-password` | 新密码、`confirm=true` JSON + 访问 JWT | 204 | 二次确认后重置并撤销全部会话，标记下次登录必须改密；新密码须通过独立安全渠道交付，不在响应/日志/通知/审计中记录 |
| `PATCH /api/admin/users/{user_id}/access` | `role` 和/或 `is_active` + 访问 JWT | 200：`UserOut` | 变更即撤销全部会话并审计；仅超级管理员可改角色或操作管理员账号，普通管理员仅可停用/启用客户与销售 |

授权约束：客户不能访问销售/管理员接口；销售不能访问管理员会话；管理员不能修改超级管理员，也不能通过会话接口读取其他资源范围。禁止停用最后一个活跃超级管理员。所有认证失败使用不含内部凭据的固定错误码；会话列表只包含必要元数据。

## 5. 前端契约

- 将访问令牌、CSRF 令牌和 `UserOut` 放在单例内存会话状态中；移除所有 `ops_token`/`ops_user` 的 `localStorage` 读写，包括登录/注册、工作台、资源池、收件箱、健康页和结算页。首次加载时删除旧版本留下的这两个键，不能再把旧 JWT 当作登录依据。选购草稿等非凭据数据可保留原存储策略。
- 页面重载时路由守卫等待会话初始化：`GET /auth/csrf`、`POST /auth/refresh`、`GET /auth/me`；失败视为访客。登录/注册直接填充内存状态。收到业务接口 401 时单次刷新并重试一次原请求；刷新接口自身不递归重试。登出后清理内存并通知其他标签页。
- 同标签页刷新使用单一 Promise；跨标签页通过 Web Locks 串行化刷新，BroadcastChannel 只传会话变化信号，不传令牌。不支持锁的浏览器在并发风险下要求重新登录，不发送可能重放的第二次刷新。
- 前端角色路由只改善导航；后端继续执行 `require_roles` 和客户/销售资源范围校验。

## 6. 实施与验证边界

新增迁移仅在隔离测试数据库执行，不升级现有开发库或生产库。测试覆盖过期、轮换、重放、登出、管理员撤销、密码及账号变更、CSRF、Cookie 属性和客户/销售/管理员隔离。浏览器在本地 HTTPS 下验证登录、重载、刷新、跨标签页、登出和跨子域请求。运行全量后端/前端测试、`alembic check`、前端构建；真实 PostgreSQL、Redis、Docker 未执行时明确标为未实测。

当前没有邮件验证或找回密码服务；管理员重置的新密码应经独立安全渠道交付，不声称已有自助找回流程。

## 7. 本地浏览器验收记录（2026-10-28）

使用隔离 SQLite、临时自签名证书、Chrome Headless 和 `https://localhost:5173`，经真实浏览器页面及网络请求验收；服务、临时数据库、证书和浏览器配置已清理。此证书仅供本地验收，不代表生产 TLS 配置。

| 操作 | 浏览器观察 |
| --- | --- |
| 管理员登录并进入 `/admin` | 页面打开；`localStorage` 和 `sessionStorage` 均无 Token 键 |
| 检查刷新 Cookie | `__Host-ops_refresh`，`Secure=true`，`HttpOnly=true`，`SameSite=Lax`，`Path=/`，浏览器显示当前主机 `localhost`，未设置父域 Domain |
| 刷新 `/admin` | 页面由 Cookie 恢复到 `/admin`；Cookie 值轮换 |
| 缺失/错误 CSRF 调用刷新 | 均返回 403 |
| 管理员重置隔离客户密码 | 返回 204；旧访问会话随后返回 401；新登录响应要求修改密码 |
| 登出并再次获取 CSRF | 登出返回 204，随后返回 401，Cookie 被清除 |

验收中发现 `GET /auth/csrf` 的初版实现要求 `Origin`，导致页面重载后的 GET 请求返回 403；已改为缺失 Origin 时允许，同源以外的显式 Origin 仍拒绝。此轮未完成的跨子域和双标签页验证，见第 8 节补充验收。

最终检查：隔离库迁移到 `0013_auth_sessions` 后 `alembic check` 无新增操作；后端 87 通过、3 跳过；前端 9 通过；前端构建通过。真实 PostgreSQL、Redis、Docker 均未验证。

## 8. 跨子域与双标签页补充验收（2026-10-28）

隔离 SQLite 与本地临时证书供 Chrome Headless 真实页面使用，前端为 `https://app.test.local:5183`，API 为 `https://api.test.local:8011`。Chrome 仅在此隔离配置中映射 `*.test.local` 到回环地址并接受临时自签名证书；证书信任链不代表生产验证。后端只允许前端的精确 Origin。浏览器网络记录只保留 URL、请求来源、状态码和 CORS 响应头，不保存令牌、Cookie 值或请求头原文。

| 页面操作及请求来源 | 浏览器结果 |
| --- | --- |
| 在 `app.test.local:5183/login` 输入隔离管理员账号登录 | 跨域 `POST /auth/login` 200，`GET /auth/me` 200；CORS `Access-Control-Allow-Origin` 精确为 `https://app.test.local:5183`；进入 `/admin` |
| 从前端页面带凭据 `GET /auth/csrf` | 200；缺失或错误 CSRF 的 `POST /auth/refresh` 均为 403 |
| 从前端页面带正确 CSRF 刷新 | `POST /auth/refresh` 200；浏览器中的刷新 Cookie 值改变，但未记录值 |
| 查看 API 主机 Cookie | `__Host-ops_refresh` 仅属 `api.test.local`，`Path=/`、`Secure`、`HttpOnly`、`SameSite=Lax`；`Set-Cookie` 没有 `Domain` 属性 |
| 在 `evil.test.local:5183` 页面发起跨域刷新 | Origin 为 `https://evil.test.local:5183` 的预检 400，无 `Access-Control-Allow-Origin`，浏览器拒绝脚本读取 |
| 在未配置的 `app.test.local:5184` 页面发起跨域刷新 | Origin 为 `https://app.test.local:5184` 的预检 400，无 `Access-Control-Allow-Origin`，浏览器拒绝脚本读取 |
| 打开第二个已登录 `/admin` 标签页，依次重载两个标签页 | 两页分别取得 CSRF 200、刷新 200、进入 `/admin`；每次刷新后 Cookie 值均改变，内存访问令牌存在 |
| 在标签页一点击“退出登录” | CSRF 200、登出 204；两页均转到 `/login`，标签页二的内存访问令牌为空；之后 CSRF 为 401 |
| 检查两个标签页的 Web Storage | `localStorage` 与 `sessionStorage` 均没有 Token 键 |

为使双标签页操作可靠，前端登出前重新获取当前 CSRF，收到登出广播的其他受保护标签页立即清除内存并跳转登录页。此验收仅覆盖本地浏览器路径；真实 PostgreSQL、Redis、Docker 未验证，不表示上线就绪。
