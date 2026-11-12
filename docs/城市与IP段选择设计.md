# 城市码与 IP 段选择设计（客户可选精细化选项）

> 状态：已实现（2026-11-10）。迁移 `0030_city_options`。
> 相关：`docs/985proxy对接设计.md`（上游接口与采购链路）、`docs/商品上下架与销售端升级设计.md`（可见性）。

---

## 1. 为什么做、边界在哪

官网的产品结构只到**国家**：客户选「美国」，我们交付美国住宅 IP。
但实际成交里，做 TikTok、支付、电商的客户经常有更细的要求：

- 「要洛杉矶的，不要纽约」——城市
- 「要 ISP 原生段，不要机房段」——IP 段（号段）

这两件事最终都落到上游的一个参数上：`buy_data[].city`（必填）与
`buy_data[].spec_ip_segment`（可选）。所以我们把城市与号段做成**可选的精细化选项**，
而不是新的产品线：

- **不选** = 自动分配（按该国库存最多的城市下单），官网结构、商品、价格都不变；
- **选了** = 按所选交付，**不允许静默换城市/换段**——静默替换等于交付了客户没要的东西，
  后面必然退款、必然扯皮。

一句话：城市是内部参数，客户可感知的只是「我能不能指定」。

---

## 2. 数据模型

表 `catalog_city_options`（一张表，按「资源类型 + 国家」维护）：

| 字段 | 说明 |
| --- | --- |
| `resource_category` | 资源类型，如 `static_residential`。与商品的 `products.resource_category` 对应 |
| `country_code` | 国家代码，如 `US`（与 `regions.country_code` 对应） |
| `city_code` | **城市码**，客户与运营看到的稳定标识（`LAX` / `NYC` / `TYO`，可自定义） |
| `city_name` | 城市名（中文展示：洛杉矶） |
| `ip_segments` | 可售 IP 段，逗号分隔短文本（`1.1.1,1.1.2`）；空 = 该城市不允许指定号段 |
| `is_enabled` | 是否对客户开放。停用后历史订单不受影响，但不能再下单 |
| `sort_order` | 排序（小的在前） |
| `note` | 备注（例如「示例号段，接上游后覆盖」） |
| `data_source` | `manual`（后台手填）/ `supplier_global_proxy`（池 1 城市号段同步）/ `supplier_985`（待接） |

为什么按「分类 + 国家」而不是按商品：同一国家的多个 SKU（30/90/180 天）共用一份城市清单，
上游库存也是按「国家 + 城市」给的。按商品记一定会出现同一城市在不同 SKU 上状态不一致。

号段为什么用逗号文本而不是单表：号段是消耗品，单城市几十个量级，运营维护成本低；
真正接上游后由号段快照（`supplier_ip_segment_snapshots` / 985 的 `spec_ip_segment_list`）
驱动，这张表只保留人工兜底清单。

订单侧新增三个快照字段（`orders`）：

| 字段 | 说明 |
| --- | --- |
| `requested_city_code` | 下单时选择的城市码；空 = 自动分配 |
| `requested_city_name` | 城市名快照（后台改城市名不影响历史单） |
| `requested_ip_segment` | 下单时选择的 IP 段；空 = 不指定 |

---

## 3. 客户流程

三个下单入口都接了同一个组件 `CitySegmentPicker.vue`：

| 入口 | 位置 | 说明 |
| --- | --- | --- |
| 客户中心购买 | 「购买线路」页 第 ③ 步「城市与 IP 段」 | 登录态，直接调 `/catalog/cities` |
| 官网选购 | `/buy` STEP 04 | 未登录不展示；登录后才有城市卡片 |
| 销售代客 | 代客下单弹窗 | 销售按客户要求代选，落到同一张单的同一组字段 |

**城市按「产品卡片」的形态呈现**，不是藏在下拉里的参数：选完国家后，客户看到的是
`自动分配` / `美国 — 洛杉矶` / `美国 — 纽约` 这样一组卡片，和上面的产品卡片视觉同级。
选中城市后，号段区域才出现，并带一个筛选框（输入 `23.94` 只看该前缀的段）。

交互规则：

1. 后台没给该「分类 + 国家」配城市 → 整块不渲染（客户中心里步骤号自动从 ④ 顶成 ③，
   不会出现「④ 前面没有 ③」的怪编号），客户只看到原来的流程。
2. 城市卡片标题是「国家 — 城市」（如 `美国 — 华盛顿`），副行是城市码；
   卡片底部标「可指定 N 个号段」或「不限号段」。
3. 选中城市后有号段：筛选框（前缀/子串匹配）+ 号段 chips + 「显示 x / N」计数；
   筛选把已选段过滤掉时，已选段仍以「· 已选」的虚线 chip 固定显示，不会让人以为选择丢了。
4. 该城市没有号段 → 显示「该城市暂未开放指定号段，交付时自动分配」，只锁城市。
5. **换城市自动清空号段**——旧段不属于新城市，留着必然被后端拒单。
6. 换商品/换国家 → 旧选择失效时组件自动清空，并通知父组件重算价格。
7. 客户在摘要里能看到所选城市与号段，订单详情也会显示「指定城市 · 号段」。

后端校验（`build_order`，客户自助与代客共用）：

| 情况 | 结果 |
| --- | --- |
| 未选城市、未选段 | 正常下单，`requested_*` 全空（自动分配） |
| 城市不在清单 / 已停用 | `422 城市「XXX」暂不可售，请重新选择或改为自动分配` |
| 段不属于所选城市 | `422 IP 段 1.1.1 不属于城市「洛杉矶」` |
| 只给段没给城市 | 反查唯一所属城市；命中多个要求先选城市；没有则 422 |
| 该国家根本没配城市却传了城市 | `422 该地区暂不支持指定城市，请选择自动分配` |

**不做的两件事**：不自动改选、不按「库存不足」偷偷换城市。失败就让人改，
和 985 设计里「段被占完 → 退款 + 提示改选」是同一个原则。

### 3.1 为什么不把城市拆成两个商品（SKU）

「美国-华盛顿」「美国-纽约」在页面上看起来是两个产品，但**不需要真的建两个 product 行**：

| 做法 | 代价 |
| --- | --- |
| 每城市一个 SKU | 33 国 × N 城市 × 4 个周期，商品表与价格表按城市数量级膨胀；改一次价要改几十行；续费、交付、线路都要按城市重挂 |
| 一个 SKU + 城市维度（当前做法） | 价格仍按「国家 + 周期」一份；城市只影响交付参数（上游 `city`），订单快照记录选择 |

只有在**不同城市售价不同**时才值得拆 SKU。真到那一步，加一张「城市加价」表
（`category + country + city_code → 加价/独立月价`）比复制商品更省事：目录、续费、报表
都不用动。当前实现的接口与订单字段已经预留了城市维度，加价是纯增量改动。

---

## 4. 后台维护

「商品与价格 → 城市与号段」页签：

- 新增城市：资源类型 + 国家 + 城市码 + 城市名 + 可选 IP 段（逗号分隔）+ 排序；
- 行内编辑城市名 / 号段，失焦即保存；排序与上下架一列搞定；
- 停用只影响「客户还能不能选」，历史订单快照不动；
- 删除会二次确认，提示「已下单的订单不受影响」。

接口（`/api/admin/catalog-management`，管理员/超管）：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/cities?resource_category=&country_code=&keyword=` | 清单 + 可选分类/国家 |
| POST | `/cities` | 新增，同 (分类, 国家, 城市码) 重复 → 409 |
| PATCH | `/cities/{id}` | 改城市码/名称/号段/排序/启停 |
| DELETE | `/cities/{id}` | 删除 |

客户侧接口（登录态）：

```
GET /api/catalog/cities?product_id=2
{
  "supported": true,
  "resource_category": "static_residential",
  "country_code": "US",
  "country_name": "美国",
  "auto_label": "自动分配（不指定城市）",
  "items": [
    {"city_code": "LAX", "city_name": "洛杉矶", "segments": ["23.94.5"], "note": "示例号段"}
  ]
}
```

未登录一律 401，与价格口径一致；`supported=false` 时前端不渲染任何城市 UI。

---

## 5. 与 985proxy 的衔接

| 本项目的值 | 985 上游参数 | 说明 |
| --- | --- | --- |
| `country_code` | `buy_data[].country` | 国家代码 |
| `requested_city_code` → `city_name` | `buy_data[].city` | 上游 `city` 必填；客户没选时由「库存最多的城市」自动填 |
| `requested_ip_segment` | `buy_data[].spec_ip_segment` | `/res_static/spec_ip_segment_list` 返回的段；需 `can_spec=true` |
| `catalog_city_options` | `/res_static/inventory` | 同步任务按国家写城市与库存，写入时 `data_source=supplier_985` |
| `ip_segments` | `/res_static/spec_ip_segment_list`（需同时传 country + city） | 号段接口以城市为父级参数，所以**选段前必须先定位城市** |

同步策略（接上游时实现）：

1. 只同步「在售且有商品」的国家，避免 34 国 × 多城市打爆接口；
2. 每个国家只取库存 Top 3 城市，`stock=0` 的城市标停用而不是删除；
3. 号段只同步 `can_spec=true` 的城市，且每个城市最多保留 60 个段（与后台校验一致）；
4. 同步写入**不覆盖** `data_source=manual` 的行，避免运营手工配的被定时任务冲掉。
5. 池 1 已实现：`POST /admin/suppliers/sync/city-segments` 按城市拉真实 IP 段并合并；
   上游英文城市名通过别名表映射到规范城市码，映射不上的先停用待归属（见
   `docs/资源池1原生静态住宅接入.md` 第 4.1 节）。

采购链路里的用法：

```
支付成功 → 采购单 → 985 buy（city=快照城市 或 自动选城，spec_ip_segment=快照段）
        → order_result(status=3) → 写回 customer_lines（IP/账密/到期）
```

失败处理沿用 `docs/985proxy对接设计.md` 的结论：段被占完 → 采购失败 → 退款 + 通知，
提示客户改选；不做静默替换。

---

## 6. 已落地的代码与验证

| 层 | 文件 |
| --- | --- |
| 模型/迁移 | `app/models/entities.py`（`CatalogCityOption`）、`alembic/versions/0030_city_options.py` |
| 服务 | `app/services/city_option_service.py`（归一化、查询、下单校验） |
| 接口 | `app/api/routes.py`（`GET /catalog/cities`、`build_order`）、`app/api/catalog_admin_routes.py`（CRUD） |
| 前端 | `components/CitySegmentPicker.vue`、`ClientBuyPanel.vue`、`BuyFlow.vue`、`Checkout.vue`、`OrderOnBehalfDialog.vue`、`views/CatalogManagementView.vue` |
| 种子 | `scripts/seed_city_options.py`（35 国 51 城，幂等；美国洛杉矶带示例号段） |
| 测试 | `tests/test_city_options.py`（8 例）、`frontend/tests/CityOptions.test.js`（7 例） |

验收口径：`pytest` 355 passed / 6 skipped，前端 vitest 49 passed，`vite build` 通过，
`alembic check` 无漂移，`ruff check --select F,E9` 全绿。

---

## 7. 后续（接上游后）

0. **多池归属先定方向**：见 `docs/城市号段来源与多池归属方案.md`。985 只有只读接口前，同步进来的城市与号段一律不开放（`sync_city_segments_985` 已加护栏）；
1. `supplier_sync` 增加 985 城市/号段实现，写入 `catalog_city_options`（`data_source=supplier_985`）；
2. 库存进 `stock` 字段后，选购页可显示「该城市剩余 N 条」；
3. 采购单里带上 `requested_city_*` / `requested_ip_segment`，并在采购失败时按段/城市给出具体原因；
4. 若要防「好段被低价卖掉」，可在后台加「锁定号段」——当前停用整条城市已够用。
