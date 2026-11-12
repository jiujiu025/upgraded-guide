#!/usr/bin/env bash
# 生产发布脚本：拉取指定版本 → 备份 → 构建 → 迁移 → 重启 → 健康检查 → 失败回滚。
#
# 由 .github/workflows/deploy.yml 通过 SSH 调用，也可以在服务器上手工执行：
#   ./deploy/auto-deploy.sh <commit-or-tag>
#   ./deploy/auto-deploy.sh --rollback          # 回到上一个成功发布的版本
#
# 约定：
# - 只操作本项目目录与 compose 项目，不碰服务器上的其它容器/网络；
# - 迁移必须保持向后兼容（expand → migrate → contract），否则代码回滚救不了已执行的 DDL；
# - 失败时先回代码、重建、再验一次；仍失败则保留现场并退出 1，等人工按备份恢复。
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/opt/99ipxin}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
ENV_FILE="${ENV_FILE:-.env.production}"
WEB_HEALTH_URL="${WEB_HEALTH_URL:-http://127.0.0.1:18081/healthz}"
LOG_DIR="${LOG_DIR:-$PROJECT_DIR/deploy/logs}"
STATE_FILE="$LOG_DIR/last-success-ref"
HEALTH_RETRIES="${HEALTH_RETRIES:-40}"
HEALTH_INTERVAL="${HEALTH_INTERVAL:-3}"
# 这台服务器的容器 DNS 访问不了公网，compose bake 构建会 EAI_AGAIN。
# 默认用 docker build --network=host；换到网络正常的环境可设 BUILD_NETWORK_HOST=0。
BUILD_NETWORK_HOST="${BUILD_NETWORK_HOST:-1}"

cd "$PROJECT_DIR"
mkdir -p "$LOG_DIR"

COMPOSE=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")

log() { printf '[deploy %s] %s\n' "$(date '+%H:%M:%S')" "$*"; }
die() { printf '[deploy %s] ERROR: %s\n' "$(date '+%H:%M:%S')" "$*" >&2; exit 1; }

# 同一时间只允许一个发布
exec 9>"$LOG_DIR/.deploy.lock"
if ! flock -n 9; then
  die "另一个发布正在进行（$LOG_DIR/.deploy.lock）"
fi

api_ready() {
  local api_id status
  api_id="$("${COMPOSE[@]}" ps -q api 2>/dev/null || true)"
  [[ -n "$api_id" ]] || return 1
  status="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$api_id" 2>/dev/null || true)"
  [[ "$status" == "healthy" ]] || return 1
  # 容器健康检查只看 127.0.0.1:8000/readyz；这里再跑一次，确认 DB / Redis / 迁移版本都过。
  "${COMPOSE[@]}" exec -T api python -c \
    "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/readyz', timeout=5)" >/dev/null 2>&1
}

web_ok() {
  curl -fsS --max-time 5 "$WEB_HEALTH_URL" >/dev/null 2>&1
}

check_health() {
  local i
  for i in $(seq 1 "$HEALTH_RETRIES"); do
    if api_ready && web_ok; then
      log "健康检查通过（第 ${i} 次）"
      return 0
    fi
    sleep "$HEALTH_INTERVAL"
  done
  return 1
}

health_detail() {
  local api_id
  api_id="$("${COMPOSE[@]}" ps -q api 2>/dev/null || true)"
  echo "api_container=${api_id:-missing}"
  [[ -n "$api_id" ]] && docker inspect -f 'health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$api_id" 2>&1
  "${COMPOSE[@]}" exec -T api python -c \
    "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/readyz', timeout=5).read().decode()[:500])" 2>&1 || true
  curl -sS --max-time 5 "$WEB_HEALTH_URL" 2>&1 | head -c 300 || true
}

build_and_start() {
  if [[ "$BUILD_NETWORK_HOST" == "1" ]]; then
    log "构建镜像（docker build --network=host）"
    docker build --network=host --pull -t 99ipxin-api:prod backend
    docker build --network=host --pull -t 99ipxin-web:prod frontend
  else
    log "构建镜像（docker compose build）"
    "${COMPOSE[@]}" build --pull api web
  fi
  log "执行数据库迁移"
  "${COMPOSE[@]}" run --rm migrate
  log "重启 api / worker / web"
  "${COMPOSE[@]}" up -d --no-build --remove-orphans api worker web
}

backup_before_migrate() {
  if [[ "${SKIP_BACKUP:-0}" == "1" ]]; then
    log "WARN: SKIP_BACKUP=1，跳过发布前备份（仅限已手工备份的首次引导）"
    return 0
  fi
  if [[ -x scripts/backup_postgres.py ]]; then
    log "发布前备份"
    python3 scripts/backup_postgres.py || die "发布前备份失败；已停止发布（不要在没有备份的情况下迁移）"
  elif [[ -f scripts/backup_postgres.py ]]; then
    log "发布前备份"
    python3 scripts/backup_postgres.py || die "发布前备份失败；已停止发布"
  else
    log "WARN: 未找到 scripts/backup_postgres.py，跳过发布前备份"
  fi
}

rollback_to() {
  local ref="$1" reason="$2"
  log "开始回滚到 $ref（原因：$reason）"
  git checkout --detach "$ref" || die "回滚 checkout 失败，保留现场等待人工处理"
  if ! build_and_start; then
    log "ERROR: 回滚版本构建/启动失败，保留现场；请按 docs/数据库定时备份与恢复演练.md 恢复"
    return 1
  fi
  if check_health; then
    log "回滚成功，当前版本 $(git rev-parse --short HEAD)"
    return 0
  fi
  log "ERROR: 回滚后健康检查仍失败：$(health_detail)"
  return 1
}

# ---------- 参数 ----------
if [[ "${1:-}" == "--rollback" ]]; then
  [[ -f "$STATE_FILE" ]] || die "没有 last-success-ref，无法自动回滚；请手工指定版本"
  TARGET_REF="$(cat "$STATE_FILE")"
  MODE="rollback"
else
  TARGET_REF="${1:-}"
  [[ -n "$TARGET_REF" ]] || die "用法：$0 <commit-or-tag> | --rollback"
  MODE="deploy"
fi

log "项目目录 $PROJECT_DIR；目标 $TARGET_REF；模式 $MODE"

# ---------- 预检 ----------
[[ -f "$ENV_FILE" ]] || die "缺少 $ENV_FILE（生产密钥只在服务器本地保存）"
if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  git status --short
  die "工作区有未提交改动，拒绝发布（避免服务器上的手工修改被覆盖后无法追溯）"
fi

PREVIOUS_REF="$(git rev-parse HEAD)"
log "当前版本 $(git rev-parse --short HEAD)"

# 服务器上的 origin 只配了 main 的 refspec；不显式拉全部分支，
# 部署 codex 分支时 origin/<branch> 永远停在旧提交。
git fetch --tags --prune origin '+refs/heads/*:refs/remotes/origin/*'
git cat-file -e "${TARGET_REF}^{commit}" 2>/dev/null || die "目标版本不存在：$TARGET_REF"

git checkout --detach "$TARGET_REF"
log "已切到 $(git rev-parse --short HEAD)"

# 备份必须在切到目标版本之后：备份脚本本身也是随版本发布的，
# 首次部署时旧版本里还没有 scripts/backup_postgres.py。
if [[ "$MODE" == "deploy" ]]; then
  backup_before_migrate
fi

if ! build_and_start; then
  rollback_to "$PREVIOUS_REF" "构建/迁移/启动失败" || exit 1
  exit 1
fi

if ! check_health; then
  log "ERROR: 健康检查失败：$(health_detail)"
  rollback_to "$PREVIOUS_REF" "健康检查失败" || exit 1
  exit 1
fi

# 只记录“部署成功”的版本，供 --rollback 使用；回滚模式不覆盖它
if [[ "$MODE" == "deploy" ]]; then
  git rev-parse HEAD > "$STATE_FILE"
fi

"${COMPOSE[@]}" ps
log "发布完成：$(git rev-parse --short HEAD)"
log "如需回滚：$0 --rollback"
