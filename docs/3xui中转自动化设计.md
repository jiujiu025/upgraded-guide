# 3x-ui 中转自动化设计（社媒 / 直播专线）

> 现状：`transit_service.provision_transit()` 直接抛 `TransitNotConfigured`，社媒与直播单
> 买到上游住宅 IP 后停在 `pending_transit`，只能人工在面板上点。
> 本文把手工步骤拆成可编程动作，并固定数据模型、并发与失败处理。

## 1. 手工步骤 → 自动化动作

| 人工在面板做的 | 自动化对应动作 | 3x-ui 接口 |
| --- | --- | --- |
| 入站列表 → 添加入站（vless + reality） | 生成 UUID / 端口，构造 inbound JSON，`inbounds/add` | `POST /panel/api/inbounds/add` |
| Xray 设置 → 出站规则 → 加 sk5 落地 | 在模板 `outbounds` 追加 socks5 出站（指向已采购的住宅 IP/端口/账密） | 读模板 → 改 → `POST /panel/xray/update` |
| 路由规则里绑定入站与出站 | `routing.rules` 追加 `{inboundTag:["inbound-<port>"], outboundTag:"socks-<标签>"}` | 同上（一次保存） |
| 重启面板 / 重启 Xray | 保存后重启 Xray 使其生效 | `POST /panel/api/server/restartXray` |
| 导出入站链接 | 按 inbound 字段拼 `vless://` 链接，备注写「地区-出口IP」 | 无需接口，本地拼 |

链接格式（与手工导出一致）：

```
vless://{uuid}@{vps_ip}:{port}?type=tcp&security=reality&pbk={public_key}&fp={fp}&sni={sni}&sid={short_id}&spx=%2F#{国家名}-{出口IP}
```

`#` 后的备注做 URL 编码；`flow` 留空以匹配现有客户端配置（手工示例里没有 `flow` 参数）。

## 2. 数据模型

两张表，迁移编号接 `0035`：

**`transit_vps`（中转机池）**

| 字段 | 说明 |
| --- | --- |
| `id / name` | 例：US-LA-01 |
| `country_code` | 该机器能承接的落地国家；下单国家必须匹配 |
| `base_url` | 面板地址，含面板路径 |
| `api_token_enc` | 加密存储的 API Token（复用现有凭据加密） |
| `connect_host` | 客户侧连接用的公网 IP/域名（可能是 NAT 映射后的地址） |
| `reality_private_key / public_key / short_id` | **留空 = 每条入站自动生成独立密钥对**（与面板现状一致）；填了才固定复用 |
| `sni / dest / fingerprint` | 例：`yahoo.com` / `yahoo.com:443` / `random` |
| `port_start / port_end` | 入站端口分配区间，例 52000-52999 |
| `max_inbounds` | 单机客户上限，超了不再分配（保护重启影响面） |
| `is_enabled / note` | 运营开关 |

**`transit_provisions`（每条线路的中转记录）**

| 字段 | 说明 |
| --- | --- |
| `order_id + line_index` | 唯一键，与 `procurements` 对齐，保证不重复建 |
| `transit_vps_id` | 落到哪台机 |
| `inbound_id / inbound_port / uuid` | 面板侧对象，重试时按它查重 |
| `outbound_tag` | 例 `socks-ORD20261126AB12-1` |
| `exit_ip` | 上游住宅出口 IP，用于备注与核对 |
| `encrypted_exit_credentials` | 上游 socks 账密（加密），重试时重建出站用 |
| `status` | `pending / inbound_created / configured / failed` |
| `error_code / error_message` | 失败原因，供后台重试 |

## 3. 一次交付的调用序列

```
provision_transit(order, accounts)
  1. 选机：country_code 匹配 + is_enabled + 未达 max_inbounds（按 inbound 占用数）
  2. 分配端口：port_range 内取未被 inbounds/list 占用的最小空闲端口
  3. 建入站：settings.clients[0] = {id: uuid, flow: "", email: "ORD...-1"}
             生成 x25519 密钥对（每条入站独立，43 字符 base64url）+ 8 位 shortId
             streamSettings = tcp + reality（sni/dest/fingerprint 取机器配置）
             remark 直接写「地区-出口IP」（建的时候就知道出口）
  3.5 出口账密加密留档（`encrypted_exit_credentials`）：入站建好但出站没保存时，
             重试靠它重建 socks 出站，不必回头找上游要密码
  4. 读 Xray 模板 → 追加 socks5 出站（住宅 IP + 端口 + 账密）
  5. 追加 routing rule（inbound-{port} → socks-{tag}）→ 保存 → 重启 Xray
  6. 回写入站 remark = 「{国家中文名}-{出口IP}」，导出链接
  7. 返回 TransitEndpoint（server/port/protocol/用户名密码为空/exit_ip）
```

`provision_transit` 返回后，`auto_procure_order` 继续走原有逻辑生成 `CustomerLine`：
`node_link` 填 vless 链接，`static_ip` 填出口 IP（客户真正关心的落地地址）。

## 4. 并发与幂等（资金与稳定性）

- **同一台 VPS 改 Xray 模板必须串行**：模板是全量覆盖写，两个订单同时读改写会互相丢配置。
  做法：`SELECT ... FOR UPDATE` 锁 `transit_vps` 行，锁内完成「读模板 → 追加 → 保存 → 重启」。
- **幂等键 `(order_id, line_index)`**：重试前先 `inbounds/get/{id}` 确认入站是否已存在；
  存在就跳过创建，只补后续步骤，绝不建第二条。
- **已建入站但模板保存失败**：记 `inbound_created`，订单仍 `pending_transit`，
  后台重试只补出站与路由，不重复建入站。
- **重启的代价**：每次保存模板并重启 Xray，该机上所有客户会瞬断 1-3 秒。
  所以按国家分机、单机限 100-200 条，并允许把同一批订单合并成一次保存。

## 5. 已实现（2026-11-26）

| 位置 | 内容 |
| --- | --- |
| `app/models/entities.py` | `TransitVps` / `TransitRoute` / `TransitProvision` |
| `alembic/versions/0036_transit.py` | 三张表与索引 |
| `app/services/xui_client.py` | 面板客户端：Token / 账号密码 / CSRF 三种登录自动降级，入站 CRUD、Xray 模板读写、重启 |
| `app/services/transit_credentials.py` | 面板密码与出口账密的 AES-GCM（AAD 带机器名 / 订单号，贴错即解密失败） |
| `app/services/transit_service.py` | 选机、端口分配、入站/出站/路由构造、链接拼接、幂等重试、`retry_provision` |
| `app/services/procurement_service.py` | 社媒单交付改用中转入口（vless），`deliver_pending_transit` 供后台重试 |
| `app/api/transit_admin_routes.py` | 机器池 CRUD、只读测连通、国家映射、待配中转列表、`POST /admin/orders/{id}/retry-transit` |
| `frontend/src/views/TransitPanelsView.vue` | 后台「中转面板」页：面板增改/检测、国家映射、待配中转重试 |
| `tests/test_transit_provision.py` / `test_transit_admin.py` | 入站只建一次、保存失败留 `inbound_created`、凭据不回显、重试交付 |

后台接口：

```
GET    /api/admin/transit/vps              机器列表（只回 has_password / has_api_token）
POST   /api/admin/transit/vps              录入机器（密码/Token 加密）
PATCH  /api/admin/transit/vps/{id}         改配置；password 传空串表示清除
POST   /api/admin/transit/vps/{id}/test    只读连通性：入站数、socks 出站数、路由数、下一个空闲端口
GET    /api/admin/transit/routes           国家 → 区域组（首次访问自动播种默认映射）
PUT    /api/admin/transit/routes/{country} 改某个国家走哪组机器
GET    /api/admin/transit/provisions       待配中转 / 失败记录
POST   /api/admin/orders/{id}/retry-transit 补做中转并交付（仅 pending_transit 可用）
```

## 6. 落地步骤

1. 跑迁移：`alembic upgrade head`（新增 0036）。
2. 配 `SUPPLIER_SECRET_ENCRYPTION_KEY`（32 字节 hex，与供应商凭据共用）；不配则密码无法加密保存。
3. 录第一台美国机：`POST /api/admin/transit/vps`，字段见第 4 节；`connect_host` 填客户实际连接的地址。
4. 测连通：`POST /api/admin/transit/vps/{id}/test`，确认能列出已有入站、读到 Xray 模板。
5. 核对国家映射：`GET /api/admin/transit/routes`，默认北美/欧洲走 `us`、东南亚/南非走 `hk`，按实际机器改。
6. 开自动采购：后台「资源池」里打开开关，或 `SUPPLIER_AUTO_PROCUREMENT_ENABLED=true`。
7. 小额真实验证：下一单社媒，确认订单从 `pending_transit` 直接变成 `delivered`，
   客户线路里是 `vless://` 链接，且 `curl ifconfig.me` 返回住宅出口 IP。

### 运营注意

- **单机容量**：每次保存模板并重启，该机所有客户瞬断 1-3 秒。默认 `max_inbounds=200`，
  按机器性能下调；超过就加机器或拆组。
- **同组多机**：选机按本地占用数最少的优先，新单自然摊开，不需要人工指定。
- **密钥策略**：默认每条入站独立一对 x25519 密钥（面板上 34 条入站实测就是各不相同的
  privateKey）。只有想统一 pbk 时，才在机器上填固定密钥。
- **未映射国家**：`transit_routes` 里没有的国家会直接报"没有配置中转区域"，
  订单停 `pending_transit`，不会乱配到别的组。
- **停用一台面板**：只关 `is_enabled`，新单不再选它，同组其他机器按"占用最少"接手；
  机上已有线路照常跑，客户不受影响。若该组没有其它启用中的机器，新单会停在
  `pending_transit`（页面上停用前会先提示，并显示这台已挂多少条线路）。
- **停用不等于迁机**：入站绑定在具体 VPS 上，把客户挪到另一台等于重建线路、换链接。
  这个动作不做自动化，需要时按订单单独处理。

## 6.5 实测记录（2026-11-26，面板 2.4.6b）

用真实面板（34 条入站、19 个 socks 出站、29 条路由）验证过的接口事实：

| 动作 | 实际接口 | 返回 |
| --- | --- | --- |
| 登录 | `POST /login`（表单 username/password） | `{"success":true}` + cookie |
| 入站列表 | `GET /panel/api/inbounds/list` | `obj` 数组，含 `tag`（= `inbound-{port}`） |
| 读 Xray 模板 | **`POST /panel/xray/`** | `obj` 是**字符串**，解开是 `{xraySetting, inboundTags}` |
| 存 Xray 模板 | `POST /panel/xray/update`（表单 `xraySetting`） | `{"success":true}` |
| 重启 Xray | `POST /panel/server/restartXrayService` | 页面上「Restart Xray」按钮就打这个 |
| 默认模板 | `GET /panel/setting/getDefaultJsonConfig` | 新面板的初始模板 |

`GET /panel/xray/` 返回的是 183KB 的页面 HTML，早期按 GET 实现会读不到配置——已改成 POST 优先。

## 7. 验收

1. `scripts/xui_probe.py` 能读到入站列表与 Xray 模板（只读，不改配置）。
2. 用一台测试 VPS + 一笔真实订单：建入站 → 加出站 → 绑路由 → 重启 → 拼链接 →
   客户端能连上，`curl ifconfig.me` 返回的正是上游住宅 IP。
3. 断网 / 面板 500 时：订单停 `pending_transit`，采购记录保留上游单号，重试只补缺的步骤。
4. 同机并发两单：两条入站、两条出站、两条路由都在，模板没有丢配置。

