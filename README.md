# 玖玖云运营中台

第一阶段可运行版，面向跨境专线服务的客户、销售和管理员运营流程。

详细设计与边界见 [docs/运营中台从0到1开发文档.md](docs/运营中台从0到1开发文档.md)。

## 快速启动

后端：进入 `backend`，创建虚拟环境，安装 `requirements.txt`，复制 `.env.example` 为 `.env`，执行 `alembic upgrade head`、`python -m app.seed`，再运行 `uvicorn app.main:app --reload --port 8000`。

前端：进入 `frontend`，执行 `npm ci` 和 `npm run dev`，访问 `http://localhost:5173`。

开发 Docker：复制 `backend/.env.example` 为 `backend/.env` 后执行 `docker compose up --build`。该配置包含开发种子数据和 Vite 开发服务器，禁止用于生产。

生产 Docker：使用 `docker-compose.prod.yml`。生产部署还依赖 PostgreSQL、共享 Redis、TLS 反向代理及完整的密钥和可信代理配置；请按 `backend/.env.production.example` 与下方部署文档配置，不要把真实密钥提交到仓库。生产配置不运行种子脚本，前端使用构建后的静态文件。

生产变量、隔离 PostgreSQL 验收和镜像检查命令见 [生产部署与隔离验收](docs/生产部署与隔离验收.md)。
