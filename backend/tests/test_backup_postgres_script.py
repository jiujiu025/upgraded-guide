"""备份脚本的纯函数与安全边界测试。

这里不连数据库、不跑 docker：只钉住"命名可解析、保留策略不会删错、校验能识别残缺
dump、权限过宽要拒绝、并发要拦住"这几件事。真实的 pg_dump 走服务器验收，见
`docs/数据库定时备份与恢复演练.md`。
"""

import importlib.util
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "backup_postgres.py"

spec = importlib.util.spec_from_file_location("backup_postgres", SCRIPT)
backup = importlib.util.module_from_spec(spec)
# dataclass 解析注解时要按模块名回查 sys.modules，先注册再执行
sys.modules["backup_postgres"] = backup
spec.loader.exec_module(backup)


def ts(text: str) -> datetime:
    return datetime.strptime(text, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)


def fake_entry(tmp_path: Path, stamp: str) -> tuple[Path, datetime]:
    path = tmp_path / backup.backup_filename(ts(stamp))
    path.write_bytes(b"x")
    return path, ts(stamp)


TOC_SAMPLE = """;
; Archive created at 2026-11-12 03:30:00 UTC
;     dbname: ops_platform
;
; Selected TOC Entries:
;
3; 2615 2200 SCHEMA - public postgres
215; 0 0 TABLE DATA public users postgres
216; 0 0 TABLE DATA public orders postgres
217; 0 0 TABLE DATA public customers postgres
218; 0 0 TABLE DATA public customer_lines postgres
219; 0 0 TABLE DATA public alembic_version postgres
220; 0 0 TABLE DATA public payment_orders postgres
221; 0 0 TABLE DATA public wallet_accounts postgres
222; 0 0 TABLE DATA public audit_logs postgres
"""


def test_filename_is_utc_and_parsable():
    name = backup.backup_filename(datetime(2026, 11, 12, 3, 30, tzinfo=timezone.utc))
    assert name == "ops_platform_20261112T033000Z.dump"
    assert backup.parse_backup_time(name) == datetime(2026, 11, 12, 3, 30, tzinfo=timezone.utc)


def test_parse_rejects_foreign_files():
    for name in ("ops_platform.dump", "backup.tar.gz", "ops_platform_2026-11-12.dump",
                 "ops_platform_20261112T033000Z.dump.part"):
        assert backup.parse_backup_time(name) is None


def test_filename_sorts_chronologically():
    names = [backup.backup_filename(ts(stamp)) for stamp in ("20260101T000000Z", "20261112T033000Z",
                                                             "20260305T120000Z")]
    assert sorted(names) == ["ops_platform_20260101T000000Z.dump",
                             "ops_platform_20260305T120000Z.dump",
                             "ops_platform_20261112T033000Z.dump"]


def test_select_prunable_keeps_daily_window(tmp_path):
    now = ts("20261112T033000Z")
    entries = [fake_entry(tmp_path, f"202611{day:02d}T033000Z") for day in range(1, 12)]
    entries.sort(key=lambda item: item[1], reverse=True)

    prunable, keep = backup.select_prunable(entries, now, keep_daily=14, keep_weekly=8)
    assert prunable == []
    assert len(keep) == 11


def test_select_prunable_buckets_by_iso_week(tmp_path):
    now = ts("20261112T033000Z")
    # 从今天往前 40 天，每天一份；最近 3 天全留，更早的按周各留最后一份
    entries = [fake_entry(tmp_path, (now - timedelta(days=offset)).strftime("%Y%m%dT033000Z"))
               for offset in range(40)]
    entries.sort(key=lambda item: item[1], reverse=True)

    prunable, keep = backup.select_prunable(entries, now, keep_daily=3, keep_weekly=2)
    kept_times = sorted(stamp for path, stamp in entries if path in keep)

    # 最近 3 天（11-10/11/12）必须在
    assert {stamp.strftime("%Y%m%d") for stamp in kept_times} >= {"20261110", "20261111", "20261112"}
    # 更早的只保留 2 个周桶，每周恰好一份
    older = [stamp for stamp in kept_times if stamp < ts("20261110T033000Z")]
    assert len(oldest_weeks := {stamp.isocalendar()[:2] for stamp in older}) <= 2
    assert len(oldest_weeks) == len(older)
    assert len(prunable) == len(entries) - len(keep)
    assert len(keep) == 5


def test_select_prunable_never_deletes_newest(tmp_path):
    now = ts("20261112T033000Z")
    entries = [fake_entry(tmp_path, "20200101T033000Z")]  # 唯一一份，而且严重过期
    prunable, keep = backup.select_prunable(entries, now, keep_daily=14, keep_weekly=8)
    assert prunable == []
    assert keep == [entries[0][0]]


def test_select_prunable_ignores_future_timestamps(tmp_path):
    now = ts("20261112T033000Z")
    entries = [fake_entry(tmp_path, "20261101T033000Z"), fake_entry(tmp_path, "20270101T000000Z")]
    prunable, keep = backup.select_prunable(entries, now, keep_daily=0, keep_weekly=0)
    # 未来时间戳不删；今天之前的按策略清空
    assert prunable == [entries[0][0]]
    assert keep == [entries[1][0]]


def test_select_prunable_rejects_negative_retention(tmp_path):
    with pytest.raises(ValueError):
        backup.select_prunable([], ts("20261112T033000Z"), keep_daily=-1)


def test_verify_toc_accepts_complete_dump():
    assert backup.verify_toc(TOC_SAMPLE) > 0


def test_verify_toc_rejects_missing_core_table():
    broken = "\n".join(line for line in TOC_SAMPLE.splitlines() if "audit_logs" not in line)
    with pytest.raises(backup.BackupError) as excinfo:
        backup.verify_toc(broken)
    assert excinfo.value.exit_code == 3
    assert "audit_logs" in str(excinfo.value)


def test_verify_toc_rejects_empty_archive():
    with pytest.raises(backup.BackupError) as excinfo:
        backup.verify_toc(";\n; Archive created at ...\n;\n")
    assert excinfo.value.exit_code == 3


def test_parse_table_data_names_ignores_schema_only_entries():
    assert "users" in backup.parse_table_data_names(TOC_SAMPLE)
    assert "public" not in backup.parse_table_data_names(TOC_SAMPLE)


def test_dump_command_goes_through_compose_exec(tmp_path):
    command = backup.pg_dump_command(compose_file=tmp_path / "compose.yml",
                                     env_file=tmp_path / ".env", project="99ipxin")
    assert command[:4] == ["docker", "compose", "--project-name", "99ipxin"]
    tail = command[command.index("exec"):]
    assert tail == ["exec", "-T", "postgres", "pg_dump", "--username=ops_platform",
                    "--dbname=ops_platform", "--format=custom", "--no-password"]


def test_restore_list_command_reads_stdin():
    command = backup.pg_restore_list_command(project="99ipxin")
    assert command[-2:] == ["pg_restore", "--list"]
    assert command[command.index("exec"):] == ["exec", "-T", "postgres", "pg_restore", "--list"]


@pytest.mark.skipif(os.name != "posix", reason="Windows 没有 POSIX 权限位")
def test_ensure_private_dir_rejects_group_readable(tmp_path):
    loose = tmp_path / "backups"
    loose.mkdir(mode=0o755)
    os.chmod(loose, 0o755)
    with pytest.raises(backup.BackupError):
        backup.ensure_private_dir(loose)


@pytest.mark.skipif(os.name != "posix", reason="Windows 没有 POSIX 权限位")
def test_ensure_private_dir_accepts_0700(tmp_path):
    tight = tmp_path / "backups"
    tight.mkdir(mode=0o700)
    os.chmod(tight, 0o700)
    backup.ensure_private_dir(tight)
    assert tight.is_dir()


def test_ensure_private_dir_creates_missing_dir(tmp_path):
    target = tmp_path / "nested" / "backups"
    backup.ensure_private_dir(target)
    assert target.is_dir()


def test_sha256_of_matches_hashlib(tmp_path):
    import hashlib

    target = tmp_path / "blob"
    target.write_bytes(b"99ipxin")
    assert backup.sha256_of(target) == hashlib.sha256(b"99ipxin").hexdigest()


def test_backup_lock_blocks_concurrent_run(tmp_path):
    lock = tmp_path / "backup.lock"
    with backup.backup_lock(lock):
        with pytest.raises(backup.BackupError) as excinfo:
            with backup.backup_lock(lock):
                pass
        assert excinfo.value.exit_code == 5
    assert not lock.exists()


def test_backup_lock_takes_over_stale_lock(tmp_path):
    lock = tmp_path / "backup.lock"
    lock.write_text("stale\n", encoding="utf-8")
    old = datetime.now(timezone.utc).timestamp() - 60 * 60 * 24
    os.utime(lock, (old, old))
    with backup.backup_lock(lock, stale_minutes=60):
        assert lock.exists()
    assert not lock.exists()


def test_dry_run_prints_command_without_writing_dump(tmp_path, capsys):
    env_file = tmp_path / ".env.production"
    env_file.write_text("POSTGRES_PASSWORD=placeholder\n", encoding="utf-8")
    compose_file = tmp_path / "docker-compose.prod.yml"
    compose_file.write_text("services: {}\n", encoding="utf-8")
    backup_dir = tmp_path / "backups"

    code = backup.main([
        "--dry-run",
        "--env-file", str(env_file),
        "--compose-file", str(compose_file),
        "--backup-dir", str(backup_dir),
    ])

    assert code == 0
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["dry_run"] is True
    assert "pg_dump" in payload["command"]
    assert not any(backup_dir.glob("*.dump")) if backup_dir.exists() else True


def test_check_only_flags_missing_and_stale_backups(tmp_path, capsys):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir(mode=0o700)
    os.chmod(backup_dir, 0o700)

    assert backup.main(["--check-only", "--backup-dir", str(backup_dir)]) == 6
    capsys.readouterr()

    stale = backup_dir / backup.backup_filename(datetime.now(timezone.utc) - timedelta(hours=72))
    stale.write_bytes(b"x" * 2048)
    assert backup.main(["--check-only", "--backup-dir", str(backup_dir), "--max-age-hours", "30"]) == 6
    capsys.readouterr()

    fresh = backup_dir / backup.backup_filename(datetime.now(timezone.utc) - timedelta(hours=2))
    fresh.write_bytes(b"x" * 2048)
    capsys.readouterr()
    assert backup.main(["--check-only", "--backup-dir", str(backup_dir), "--max-age-hours", "30"]) == 0
    assert json.loads(capsys.readouterr().out.strip())["status"] == "fresh"


def test_missing_env_file_fails_before_touching_docker(tmp_path, capsys):
    code = backup.main([
        "--env-file", str(tmp_path / "nope.env"),
        "--backup-dir", str(tmp_path / "backups"),
    ])
    assert code == 1
    assert "环境文件不存在" in capsys.readouterr().err
