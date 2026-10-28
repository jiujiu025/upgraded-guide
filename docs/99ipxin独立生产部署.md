# 99ipxin 独立生产部署准备

本文件是待执行手册，当前未连接或修改服务器。域名为 `1.ipip99.com`，Cloudflare 代理已开启，服务器上的 Origin 证书已被确认匹配。不要把本地自签名证书验收当作该服务器的 TLS 验收。

## 资源边界

`docker-compose.prod.yml` 的项目名固定为 `99ipxin`，只创建本项目的 `postgres`、`redis`、`api`、`web` 容器、`postgres_data`/`redis_data` 持久卷和 `data`/`edge` 网络。四个服务均配置非 root 运行账号。数据库和 Redis 不发布宿主机端口；API 也不发布端口。Web 只绑定 `127.0.0.1:18081`，宿主机 Nginx 才能通过本地反代访问。生产 Compose 不调用种子脚本、不传入供应商 Key，自动采购被固定关闭。

默认 `edge` 网段为 `172.29.99.0/24`：Web `172.29.99.10`、API `172.29.99.20`、宿主机网关 `172.29.99.1`。`data` 为仅供内部容器通信的 `172.29.100.0/24`。API 只信任 Web 与 edge 网关这两个精确的代理 IP；Web 不接入 data 网络，PostgreSQL/Redis 不接入 edge 网络。服务器若已有重叠网段，必须先在本项目环境文件中整组换成未占用的地址；不能修改或复用其他项目的网络。

在服务器上先执行只读预检，确认 `99ipxin` 项目名、`18081` 端口和两个网段均未占用：

```sh
docker compose ls
docker ps --format '{{.Names}} {{.Ports}}'
docker network ls
docker network inspect <待核查的网络名>
ss -ltn '( sport = :18081 )'
```

如果已存在同名项目、同名容器/卷或网段冲突，停止部署并先核实归属。不得对其他项目执行 `down`、`stop`、`rm`、重建或网络变更。不要在此服务器上运行隔离 CI 的 `down --volumes` 清理命令。

## 本地环境文件

把仓库根目录的 `.env.production.example` 复制成服务器项目目录下的 `.env.production`，仅在服务器本地填写。数据库密码必须与 `DATABASE_URL` 中的密码一致；URL 中的特殊字符须按 URL 规则编码，静态检查器会比对解码后的值。JWT 密钥和会话哈希密钥要分别使用独立、至少 32 字节的随机值。不得使用模板占位符或默认密码。无需设置 `GLOBAL_PROXY_API_KEY`、`ADMIN_PASSWORD` 或供应商采购参数。

文件权限要求：目录仅部署账号及必要管理员可访问，`.env.production` 所有者为部署账号，权限 `0600`；备份目录同样限制为 `0700`。不要提交该文件，不要把它放入镜像构建上下文，不要在聊天、工单、CI 日志、命令行参数或 Nginx 配置中写密钥。Docker 管理员仍能通过容器检查读取环境变量，所以 Docker socket 和部署账号必须只授予可信运维人员。

```sh
chmod 600 .env.production
python scripts/verify_production_compose.py
```

静态检查只输出检查结果，不打印 Compose 展开后的环境变量。不要对真实环境执行会显示完整变量的 `docker compose config`；如需手工解析检查，使用 `config --quiet`。生产密钥只从服务器本地 `.env.production` 注入，不使用 Compose `build.args`。

## 首次部署顺序

以下命令只针对本项目文件与项目名执行。先在隔离 CI 完成文末验收，再在已通过只读预检的服务器上执行。不要启动 `docker-compose.yml`，它是开发配置并会运行种子脚本。

```sh
docker compose --env-file .env.production -f docker-compose.prod.yml build --pull api web
docker compose --env-file .env.production -f docker-compose.prod.yml up -d postgres redis
docker compose --env-file .env.production -f docker-compose.prod.yml run --rm migrate
docker compose --env-file .env.production -f docker-compose.prod.yml run --rm migrate alembic current
docker compose --env-file .env.production -f docker-compose.prod.yml run --rm migrate alembic check
docker compose --env-file .env.production -f docker-compose.prod.yml up -d api
docker compose --env-file .env.production -f docker-compose.prod.yml exec api python -m app.bootstrap_admin
docker compose --env-file .env.production -f docker-compose.prod.yml up -d web
docker compose --env-file .env.production -f docker-compose.prod.yml ps
```

初始化命令要求交互终端，用户名和两次密码提示在终端输入；密码由 `getpass` 隐藏，不能通过参数、管道或环境变量传入。它只接受生产 PostgreSQL，要求用户表完全为空，使用 PostgreSQL 事务锁防止并发重复初始化，创建审计记录，并设置首次登录必须修改密码。再次运行会失败。初始密码只经独立安全渠道交付；初始化不会打印密码。生产环境执行 `python -m app.seed` 会被拒绝。首次登录后必须通过改密流程才能访问其他受保护接口。

如果已有用户而没有超级管理员，不要清空用户表来绕过初始化限制；先做数据核查和人工恢复方案。

## 宿主机 Nginx 与 Cloudflare

只为 `1.ipip99.com` 准备独立站点配置，不编辑其他项目的 upstream、server block、证书路径或全局配置。若此域名已有 server block，应先核对归属，不能叠加冲突配置。下列片段由服务器管理员在确认现有 Nginx 布局后使用；这里不写入服务器。

```nginx
server {
    listen 443 ssl;
    server_name 1.ipip99.com;
    ssl_certificate /server/local/path/origin.crt;
    ssl_certificate_key /server/local/path/origin.key;
    include /etc/nginx/snippets/cloudflare-realip.conf;

    location / {
        proxy_pass http://127.0.0.1:18081;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto https;
        proxy_connect_timeout 5s;
        proxy_read_timeout 30s;
    }
}
```

`cloudflare-realip.conf` 必须由服务器管理员维护官方 Cloudflare 来源网段，且只对这些网段信任 `CF-Connecting-IP`。宿主机 Nginx 覆盖外部传入的 `X-Forwarded-For`；容器 Web Nginx 再追加其直连地址。API 仅信任上文的 Web IP 和 edge 网关，从可信链中解析客户端地址。若 Cloudflare 网段和来源地址未正确配置，登录限流可能按 Cloudflare 节点聚合，应在上线前用受控请求核查。公网 HTTPS 由宿主机 Nginx 终止，Cloudflare SSL/TLS 应为 Full (strict)，并验证实际 Origin 证书链和主机名。修改 Nginx 前先 `nginx -t`，只重载 Nginx，不停止其他项目容器。

## 备份与恢复

首次迁移是空库；以后每次升级前先记录 `alembic current`，备份本项目 PostgreSQL，并核对备份退出码和非零文件大小。不要备份其他项目数据库。以下命令在服务器 Linux shell 运行，备份文件名由运维人员指定；`umask 077` 保证新文件不被其他用户读取。备份应加密并转移到受控的异机位置，制定保留期和恢复演练计划。

```sh
umask 077
mkdir -p /secure/99ipxin-backups
docker compose --env-file .env.production -f docker-compose.prod.yml exec -T postgres \
  pg_dump -U ops_platform -d ops_platform -Fc > /secure/99ipxin-backups/ops_platform_YYYYMMDD.dump
test -s /secure/99ipxin-backups/ops_platform_YYYYMMDD.dump
```

恢复演练应使用单独项目名、独立空卷和与生产不重叠的网络配置。例如给演练环境准备另一个受限环境文件，并设不同的 `OPS_DATA_SUBNET` 与 `OPS_EDGE_SUBNET`，仅启动该项目的 `postgres`，然后将备份恢复进去：

```sh
docker compose -p 99ipxin-restore --env-file .env.restore -f docker-compose.prod.yml up -d postgres
docker compose -p 99ipxin-restore --env-file .env.restore -f docker-compose.prod.yml exec -T postgres \
  pg_restore -U ops_platform -d ops_platform --clean --if-exists --no-owner \
  < /secure/99ipxin-backups/ops_platform_YYYYMMDD.dump
```

恢复后在该隔离项目里核对迁移版本、关键表行数和应用读操作，再清理**仅该演练项目**的资源。实际生产恢复要另行确认维护窗口、停写、备份点和回滚方案；不得直接把示例 `pg_restore --clean` 指向正在服务的生产库。Redis 保存的是认证限流窗口，可随本项目独立卷恢复或重建；业务数据与审计的主备份对象是 PostgreSQL。

## 隔离环境验收与当前状态

在独立 Docker CI runner 上执行 `.github/workflows/production-infrastructure-acceptance.yml`。它使用一次性 PostgreSQL/Redis、随机测试密钥与临时环境文件，检查真实 PostgreSQL 全新迁移、降级与再次升级、并发交付与事务回滚、真实 Redis 多进程限流与中断失败关闭、生产镜像内容/非 root/健康状态、受信任测试证书的 HTTPS 握手。新增的独立生产栈验收步骤还检查 Compose 静态约束、生产 PostgreSQL 全新迁移与 `alembic check`、四个容器健康状态、Web `127.0.0.1:18081` 唯一发布端口、数据库/Redis/API 零宿主机端口、四个容器非 root，以及管理员初始化拒绝非交互输入。真实 PostgreSQL 用例检查管理员创建、审计、首次改密标志和重复初始化拒绝。CI 完成后只清理隔离 runner 中这两个项目的资源。

截至 2026-10-28，本机 `python scripts/verify_production_compose.py --template` 已通过；本机没有可连接的 Docker Linux daemon，因此全新 PostgreSQL 迁移、容器健康状态、非 root 运行、端口隔离的运行态和真实 PostgreSQL 管理员初始化均**未实测**。本手册不表示服务器已部署或已具备上线条件。供应商报价仍失败、城市/库存返回空；不得据此发布商品或触发采购。
