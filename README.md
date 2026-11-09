# 玖玖云运营中台

第一阶段可运行版，面向跨境专线服务的客户、销售和管理员运营流程。

详细设计与边界见 [docs/运营中台从0到1开发文档.md](docs/运营中台从0到1开发文档.md)。

## 快速启动

后端：进入 `backend`，创建虚拟环境，安装 `requirements.txt`，复制 `.env.example` 为 `.env`，执行 `alembic upgrade head`、`python -m app.seed`，再运行 `uvicorn app.main:app --reload --port 8000`。

前端：进入 `frontend`，执行 `npm ci` 和 `npm run dev`，访问 `http://localhost:5173`。

开发 Docker：复制 `backend/.env.example` 为 `backend/.env` 后执行 `docker compose up --build`。该配置包含开发种子数据和 Vite 开发服务器，禁止用于生产。

生产 Docker：使用 `docker-compose.prod.yml`。生产部署还依赖 PostgreSQL、共享 Redis、TLS 反向代理及完整的密钥和可信代理配置；请按 `backend/.env.production.example` 与下方部署文档配置，不要把真实密钥提交到仓库。生产配置不运行种子脚本，前端使用构建后的静态文件。

生产变量、隔离 PostgreSQL 验收和镜像检查命令见 [生产部署与隔离验收](docs/生产部署与隔离验收.md)。

定时任务（关单、到期提醒、支付对账）由 `worker` 容器执行 `python -m app.scheduler` 拉起，设计与运维说明见 [定时任务与支付对账设计](docs/定时任务与支付对账设计.md)。

线上收款：不是写死关闭，而是没有验收记录打不开。`python scripts/payment_selftest.py` 跑本地闭环（下单/回调验签/入账/重放/巡检），`--apply` 登记验收后可开启但单笔限额 100 元；真实支付一笔后 `--live <out_trade_no> --apply` 才放开限额。说明见 [支付与余额功能设计](docs/支付与余额功能设计.md) 第 8 节。

到期触达：销售工作台的「到期触达」面板按紧急度列出即将到期和刚过期的线路，带续费金额与可直接复制的微信文案；发完点「记一笔」留痕（渠道、结果、当时原文），避免重复催和跟进断线。站内信照旧发，两条路径互不冲突。设计见 [客户到期触达设计](docs/客户到期触达设计.md)。

销售邀请码：走哪个销售的邀请码注册，客户就自动归属到谁。销售工作台「我的邀请码」面板生成/停用码并复制注册链接（`/register?invite=码`），一人最多 10 个有效码；失效码不拒绝注册（归公海并通知管理员，客户无感知）；停用销售账号时其码级联停用。设计见 [销售邀请码客户归属设计](docs/销售邀请码客户归属设计.md)。

财务导出：后台「财务」页可按当前日期区间导出四张 CSV——订单明细、收款明细、钱包流水、月度汇总。
带 BOM 与 CRLF，Excel 双击直接打开不变乱码；四张表归属口径不同（下单时间 / 支付时间 / 记账时间 / 自然月），
加起来不会相等，这是记账规则不是故障。说明见 [财务导出与月报](docs/财务导出与月报.md)。

金额对账：`python scripts/audit_wallet_ledger.py` 只读核对"账户余额 / 明细流水 / 客户表浮点副本"三者是否自洽，退出码 1 表示账本身有问题。存量余额补进钱包用 `python scripts/migrate_customer_balance_to_wallet.py`（默认预演，加 `--apply` 才写库，可重复执行）。现状核查与迁移路径见 [金额一致性现状与迁移方案](docs/金额一致性现状与迁移方案.md)。
