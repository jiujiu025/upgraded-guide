"""仅为隔离 CI 生成一次性的生产形态配置。"""

import secrets
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / ".env.production"


def main() -> None:
    if OUTPUT.exists():
        raise SystemExit("隔离环境文件已存在，拒绝覆盖")
    database_password = secrets.token_hex(24)
    lines = [
        f"POSTGRES_PASSWORD={database_password}",
        f"DATABASE_URL=postgresql+psycopg://ops_platform:{database_password}@postgres/ops_platform",
        f"JWT_SECRET_KEY={secrets.token_hex(48)}",
        f"SESSION_TOKEN_HASH_KEY={secrets.token_hex(48)}",
    ]
    OUTPUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    OUTPUT.chmod(0o600)
    print("已生成隔离 CI 环境文件；内容不输出。")


if __name__ == "__main__":
    main()
