"""PostgreSQL 定时备份：docker compose pg_dump -Fc + 完整性校验 + 保留策略 + 运行报告。

只做"把生产库导出一份可恢复的文件"，不做任何写库动作。
所有密钥都留在 `.env.production` 里，通过 compose 注入容器，本脚本从不读取、
不打印、不落盘任何凭据。

设计要点（每一条都对应一次真实事故的预防）：

1. **先写临时文件再原子改名**。半截 dump 永远不会被当成可用备份。
2. **导出后立刻校验**。`pg_restore --list` 必须能列出预期的核心表，
   否则本次视为失败——"文件存在但打不开"是最常见的备份事故。
3. **保留策略按日期 + ISO 周分桶**，不做"删最旧 N 个"这种会一次删光历史
   的粗暴策略；未来时间戳的文件永不删除。
4. **并发保护**：同一时间只允许一个备份进程，避免 dump 互相抢 IO。
5. **失败不清理**。只有本次备份 + 校验都成功，才允许按策略裁剪旧文件。
6. **--check-only 给监控用**：只看最近一次成功备份是否过旧，不产生新备份。

退出码：
  0  成功
  1  参数/环境错误
  2  pg_dump 失败
  3  备份校验失败
  4  异地转储（--offsite-command）失败
  5  已有备份进程在跑
  6  备份过期（仅 --check-only）
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_COMPOSE_FILE = ROOT / "docker-compose.prod.yml"
DEFAULT_ENV_FILE = ROOT / ".env.production"
DEFAULT_BACKUP_DIR = Path("/secure/99ipxin-backups")

FILE_PREFIX = "ops_platform_"
FILE_SUFFIX = ".dump"
# 20261112T033000Z —— UTC，按字典序即时间序，跨机器/跨时区不歧义
FILE_PATTERN = re.compile(r"^ops_platform_(\d{8}T\d{6}Z)\.dump$")

# dump 里必须存在的核心表：缺任何一张都说明备份不是完整业务库
REQUIRED_TABLES = (
    "alembic_version",
    "users",
    "customers",
    "orders",
    "customer_lines",
    "payment_orders",
    "wallet_accounts",
    "audit_logs",
)

# 小于这个体积的 dump 一律视为异常（空库 dump 也有几十 KB）
MIN_DUMP_BYTES = 16 * 1024


class BackupError(RuntimeError):
    """带退出码的失败，便于 systemd / cron 区分处理。"""

    def __init__(self, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code


@dataclass
class BackupResult:
    dump_path: Path | None = None
    sha256: str | None = None
    size_bytes: int = 0
    duration_seconds: float = 0.0
    verified_entries: int = 0
    pruned: list[Path] = field(default_factory=list)
    offsite_ok: bool | None = None
    dry_run: bool = False

    def as_report(self) -> dict:
        return {
            "status": "ok",
            "finished_at": utc_now().isoformat().replace("+00:00", "Z"),
            "dump": str(self.dump_path) if self.dump_path else None,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "duration_seconds": round(self.duration_seconds, 3),
            "verified_entries": self.verified_entries,
            "pruned": [str(path) for path in self.pruned],
            "offsite_ok": self.offsite_ok,
            "dry_run": self.dry_run,
        }


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- 命名与解析


def backup_filename(now: datetime) -> str:
    """`ops_platform_20261112T033000Z.dump`，UTC 时间，字典序即时间序。"""
    return f"{FILE_PREFIX}{now.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}{FILE_SUFFIX}"


def parse_backup_time(name: str) -> datetime | None:
    match = FILE_PATTERN.match(Path(name).name)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def collect_backups(backup_dir: Path) -> list[tuple[Path, datetime]]:
    """只认本脚本产出的 dump 命名，其他文件一律不碰。"""
    entries: list[tuple[Path, datetime]] = []
    if not backup_dir.is_dir():
        return entries
    for path in backup_dir.iterdir():
        if not path.is_file():
            continue
        stamp = parse_backup_time(path.name)
        if stamp is not None:
            entries.append((path, stamp))
    return sorted(entries, key=lambda item: item[1], reverse=True)


# ---------------------------------------------------------------- 保留策略


def select_prunable(
    entries: list[tuple[Path, datetime]],
    now: datetime,
    keep_daily: int = 14,
    keep_weekly: int = 8,
) -> tuple[list[Path], list[Path]]:
    """返回 (待删除, 保留)。

    - 最近 `keep_daily` 天：按自然日全留；
    - 更早的：按 ISO 周分桶，保留最近 `keep_weekly` 个周桶里**每周最后一份**；
    - 时间戳在未来（时钟漂移/人为改名）的文件永不删除；
    - 最新一份备份永远保留，即使策略算出要删（双重保险）。
    """
    if keep_daily < 0 or keep_weekly < 0:
        raise ValueError("保留数量不能为负")
    if not entries:
        return [], []

    newest_path, newest_time = max(entries, key=lambda item: item[1])
    today = now.astimezone(timezone.utc).date()
    keep: set[Path] = {newest_path}
    leftover: list[tuple[Path, datetime]] = []

    for path, stamp in entries:
        age_days = (today - stamp.astimezone(timezone.utc).date()).days
        if age_days < 0:
            keep.add(path)
        elif age_days < keep_daily:
            keep.add(path)
        else:
            leftover.append((path, stamp))

    buckets: dict[tuple[int, int], list[tuple[Path, datetime]]] = {}
    for path, stamp in leftover:
        iso = stamp.astimezone(timezone.utc).isocalendar()
        buckets.setdefault((iso[0], iso[1]), []).append((path, stamp))
    for key in sorted(buckets, reverse=True)[:keep_weekly]:
        latest = max(buckets[key], key=lambda item: item[1])
        keep.add(latest[0])

    prunable = sorted(path for path, _ in entries if path not in keep)
    return prunable, sorted(keep)


# ---------------------------------------------------------------- 外部命令


def compose_command(
    *,
    compose_file: Path = DEFAULT_COMPOSE_FILE,
    env_file: Path = DEFAULT_ENV_FILE,
    project: str = "99ipxin",
    service: str = "postgres",
) -> list[str]:
    return [
        "docker",
        "compose",
        "--project-name",
        project,
        "--env-file",
        str(env_file),
        "-f",
        str(compose_file),
        "exec",
        "-T",
        service,
    ]


def pg_dump_command(
    *,
    compose_file: Path = DEFAULT_COMPOSE_FILE,
    env_file: Path = DEFAULT_ENV_FILE,
    project: str = "99ipxin",
    service: str = "postgres",
    database: str = "ops_platform",
    db_user: str = "ops_platform",
) -> list[str]:
    return compose_command(compose_file=compose_file, env_file=env_file, project=project, service=service) + [
        "pg_dump",
        f"--username={db_user}",
        f"--dbname={database}",
        "--format=custom",
        "--no-password",
    ]


def pg_restore_list_command(
    *,
    compose_file: Path = DEFAULT_COMPOSE_FILE,
    env_file: Path = DEFAULT_ENV_FILE,
    project: str = "99ipxin",
    service: str = "postgres",
) -> list[str]:
    return compose_command(compose_file=compose_file, env_file=env_file, project=project, service=service) + [
        "pg_restore",
        "--list",
    ]


def parse_table_data_names(list_output: str) -> set[str]:
    """从 `pg_restore --list` 里取有数据段的表名。

    行格式：`215; 0 0 TABLE DATA public users postgres`
    （TOC 条目号; 表 OID 段 OID 类型 模式 名称 属主），用正则只抓类型 + 模式 + 名称，
    避免依赖列宽在不同 pg 小版本间的变化。
    """
    names: set[str] = set()
    for line in list_output.splitlines():
        match = re.search(r"\bTABLE DATA\s+(\S+)\s+(\S+)", line)
        if match:
            names.add(match.group(2))
    return names


def verify_toc(list_output: str, required: tuple[str, ...] = REQUIRED_TABLES) -> int:
    """校验 `pg_restore --list` 输出：核心表齐全才返回 TOC 条目数。"""
    entries = [line for line in list_output.splitlines() if line.strip() and not line.startswith(";")]
    if not entries:
        raise BackupError("备份校验失败：TOC 为空，dump 不可恢复", exit_code=3)

    present = parse_table_data_names(list_output)
    missing = [table for table in required if table not in present]
    if missing:
        raise BackupError(f"备份校验失败：缺少核心表 {', '.join(missing)}", exit_code=3)
    return len(entries)


@contextlib.contextmanager
def backup_lock(lock_path: Path, stale_minutes: int = 180):
    """O_EXCL 锁；进程崩溃留下的旧锁超过 stale_minutes 可被接管。"""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            try:
                age_minutes = (time.time() - lock_path.stat().st_mtime) / 60
            except FileNotFoundError:
                continue
            if age_minutes < stale_minutes:
                raise BackupError(f"已有备份进程在运行（锁文件 {lock_path}）", exit_code=5)
            lock_path.unlink(missing_ok=True)
            continue
        os.write(fd, f"{os.getpid()} {utc_now().isoformat()}\n".encode())
        os.close(fd)
        break
    try:
        yield
    finally:
        lock_path.unlink(missing_ok=True)


# ---------------------------------------------------------------- 主流程


def sha256_of(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if os.name != "posix":
        # Windows 没有 POSIX 位；生产是 Linux，这条检查只在 POSIX 上生效
        return
    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        raise BackupError(f"备份目录权限过宽（{oct(mode)}）：先 chmod 700 {path}", exit_code=1)


def _run(command: list[str], *, stdin_path: Path | None = None, timeout: int = 3600) -> subprocess.CompletedProcess:
    stdin = open(stdin_path, "rb") if stdin_path else subprocess.DEVNULL
    try:
        return subprocess.run(command, stdin=stdin, capture_output=True, timeout=timeout, check=False)
    finally:
        if stdin_path:
            stdin.close()


def run_backup(args: argparse.Namespace) -> BackupResult:
    started = time.monotonic()
    backup_dir: Path = args.backup_dir
    now = utc_now()

    if args.check_only:
        entries = collect_backups(backup_dir)
        if not entries:
            raise BackupError(f"没有任何备份文件：{backup_dir}", exit_code=6)
        newest_path, newest_time = entries[0]
        age_hours = (now - newest_time).total_seconds() / 3600
        if age_hours > args.max_age_hours:
            raise BackupError(
                f"最近一次备份已过期：{newest_path.name}（{age_hours:.1f} 小时前）", exit_code=6
            )
        result = BackupResult(dump_path=newest_path, size_bytes=newest_path.stat().st_size)
        print(json.dumps({**result.as_report(), "status": "fresh", "age_hours": round(age_hours, 1)},
                         ensure_ascii=False))
        return result

    if not args.env_file.is_file():
        raise BackupError(f"环境文件不存在：{args.env_file}")
    if not args.compose_file.is_file():
        raise BackupError(f"Compose 文件不存在：{args.compose_file}")

    lock_path = backup_dir / ".99ipxin-backup.lock"
    if args.dry_run:
        ensure_private_dir(backup_dir)

    dump_path = backup_dir / backup_filename(now)
    if args.dry_run:
        command = pg_dump_command(
            compose_file=args.compose_file, env_file=args.env_file, project=args.project,
            service=args.service, database=args.database, db_user=args.db_user,
        )
        result = BackupResult(dump_path=dump_path, dry_run=True)
        print(json.dumps({**result.as_report(), "command": command}, ensure_ascii=False))
        return result

    with backup_lock(lock_path, stale_minutes=args.stale_lock_minutes):
        ensure_private_dir(backup_dir)
        partial = dump_path.with_suffix(dump_path.suffix + ".part")
        command = pg_dump_command(
            compose_file=args.compose_file, env_file=args.env_file, project=args.project,
            service=args.service, database=args.database, db_user=args.db_user,
        )
        with partial.open("wb") as handle:
            completed = subprocess.run(
                command, stdout=handle, stderr=subprocess.PIPE, timeout=args.timeout, check=False
            )
        if completed.returncode != 0:
            partial.unlink(missing_ok=True)
            stderr = completed.stderr.decode("utf-8", "replace").strip().splitlines()
            tail = stderr[-1] if stderr else "无错误输出"
            raise BackupError(f"pg_dump 失败（{completed.returncode}）：{tail}", exit_code=2)

        size = partial.stat().st_size
        if size < MIN_DUMP_BYTES:
            partial.unlink(missing_ok=True)
            raise BackupError(f"dump 体积异常（{size} 字节），疑似空备份", exit_code=3)

        os.chmod(partial, 0o600)
        os.replace(partial, dump_path)

        result = BackupResult(dump_path=dump_path, size_bytes=size)

        if not args.skip_verify:
            listed = _run(pg_restore_list_command(
                compose_file=args.compose_file, env_file=args.env_file, project=args.project,
                service=args.service,
            ),  # 不带文件名时 pg_restore 从 stdin 读；传 "-" 会被当成普通文件名
                stdin_path=dump_path, timeout=args.timeout)
            if listed.returncode != 0:
                raise BackupError(
                    f"备份校验失败：pg_restore --list 退出 {listed.returncode}", exit_code=3
                )
            result.verified_entries = verify_toc(listed.stdout.decode("utf-8", "replace"))

        digest = sha256_of(dump_path)
        result.sha256 = digest
        checksum_file = dump_path.with_suffix(".dump.sha256")
        checksum_file.write_text(f"{digest}  {dump_path.name}\n", encoding="utf-8")
        os.chmod(checksum_file, 0o600)

        if args.offsite_command:
            env = {**os.environ, "BACKUP_FILE": str(dump_path), "BACKUP_SHA256": digest}
            offsite = subprocess.run(args.offsite_command, shell=True, capture_output=True, timeout=args.timeout,
                                     env=env, check=False)
            result.offsite_ok = offsite.returncode == 0
            if not result.offsite_ok:
                _write_report(backup_dir, result, extra={"offsite_error": "异地转储失败，见 journal"})
                raise BackupError(f"异地转储失败（{offsite.returncode}），本地备份保留", exit_code=4)

        entries = collect_backups(backup_dir)
        prunable, _ = select_prunable(entries, utc_now(), args.keep_daily, args.keep_weekly)
        removed: list[Path] = []
        for path in prunable:
            try:
                path.unlink()
                path.with_suffix(".dump.sha256").unlink(missing_ok=True)
                removed.append(path)
            except OSError as exc:  # 删不掉不算备份失败，但要在报告里留痕
                print(f"warn: 无法删除旧备份 {path}: {exc}", file=sys.stderr)
        result.pruned = removed
        result.duration_seconds = time.monotonic() - started
        _write_report(backup_dir, result)

    print(json.dumps(result.as_report(), ensure_ascii=False))
    return result


def _write_report(backup_dir: Path, result: BackupResult, extra: dict | None = None) -> None:
    if result.dump_path is None:
        return
    report = {**result.as_report(), **(extra or {})}
    report_path = result.dump_path.with_suffix(".dump.json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(report_path, 0o600)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="99ipxin PostgreSQL 定时备份")
    parser.add_argument("--compose-file", type=Path, default=DEFAULT_COMPOSE_FILE)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--backup-dir", type=Path, default=DEFAULT_BACKUP_DIR)
    parser.add_argument("--project", default="99ipxin", help="compose 项目名")
    parser.add_argument("--service", default="postgres", help="compose 中的数据库服务名")
    parser.add_argument("--database", default="ops_platform")
    parser.add_argument("--db-user", default="ops_platform")
    parser.add_argument("--keep-daily", type=int, default=14, help="最近 N 天每天全留")
    parser.add_argument("--keep-weekly", type=int, default=8, help="更早的按周保留 N 周")
    parser.add_argument("--max-age-hours", type=float, default=30.0, help="--check-only 的过期阈值")
    parser.add_argument("--timeout", type=int, default=3600, help="单条命令超时（秒）")
    parser.add_argument("--stale-lock-minutes", type=int, default=180)
    parser.add_argument("--offsite-command", default=None,
                        help="成功后执行的异地转储命令，可用 $BACKUP_FILE / $BACKUP_SHA256")
    parser.add_argument("--skip-verify", action="store_true", help="跳过 pg_restore --list 校验（不建议）")
    parser.add_argument("--check-only", action="store_true", help="只检查最近备份是否新鲜")
    parser.add_argument("--dry-run", action="store_true", help="只打印将执行的命令，不导出")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if shutil.which("docker") is None and not args.dry_run:
        raise BackupError("找不到 docker 命令")
    try:
        run_backup(args)
    except BackupError as exc:
        payload = {"status": "error", "message": str(exc), "exit_code": exc.exit_code}
        print(json.dumps(payload, ensure_ascii=False))
        print(f"error: {exc}", file=sys.stderr)
        return exc.exit_code
    except subprocess.TimeoutExpired:
        print(json.dumps({"status": "error", "message": "命令超时"}, ensure_ascii=False))
        print("error: 命令超时", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
