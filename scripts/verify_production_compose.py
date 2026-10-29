"""静态验证独立生产 Compose，不输出环境变量或凭据。"""

import argparse
import ipaddress
import json
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]


def verify(env_file: Path, allow_placeholders: bool = False) -> None:
    if not env_file.is_file():
        raise ValueError("环境文件不存在")
    command = ["docker", "compose", "--profile", "maintenance", "--env-file", str(env_file), "-f",
               str(ROOT / "docker-compose.prod.yml"), "config", "--format", "json"]
    result = subprocess.run(command, capture_output=True, text=True, cwd=ROOT)
    if result.returncode:
        raise ValueError("Compose 解析失败；请在私有终端检查变量与语法")
    config = json.loads(result.stdout)
    services = config["services"]
    if config["name"] != "99ipxin" or set(services) != {"postgres", "redis", "migrate", "api", "web"}:
        raise ValueError("项目名称或服务集合与独立部署契约不符")
    if services["postgres"].get("ports") or services["redis"].get("ports") or services["api"].get("ports"):
        raise ValueError("数据库、Redis 或 API 不得发布宿主机端口")
    if services["postgres"].get("user") != "postgres" or services["redis"].get("user") != "redis":
        raise ValueError("数据库或 Redis 未配置非 root 服务账号")
    ports = services["web"].get("ports", [])
    if len(ports) != 1 or ports[0].get("host_ip") != "127.0.0.1" or ports[0].get("published") != "18081":
        raise ValueError("Web 必须仅绑定 127.0.0.1:18081")
    if set(config["volumes"]) != {"postgres_data", "redis_data"}:
        raise ValueError("持久卷配置不完整")
    for service, volume in (("postgres", "postgres_data"), ("redis", "redis_data")):
        if not any(mount.get("type") == "volume" and mount.get("source") == volume
                   for mount in services[service].get("volumes", [])):
            raise ValueError(f"持久卷未挂载到服务: {service}")
    for service in services.values():
        if any(mount.get("type") == "bind" for mount in service.get("volumes", [])):
            raise ValueError("生产服务不得绑定宿主机源码或配置目录")

    edge = config["networks"]["edge"]["ipam"]["config"][0]
    data = config["networks"]["data"]["ipam"]["config"][0]
    edge_network = ipaddress.ip_network(edge["subnet"])
    data_network = ipaddress.ip_network(data["subnet"])
    web_ip = ipaddress.ip_address(services["web"]["networks"]["edge"]["ipv4_address"])
    api_ip = ipaddress.ip_address(services["api"]["networks"]["edge"]["ipv4_address"])
    gateway = ipaddress.ip_address(edge["gateway"])
    trusted = services["api"]["environment"]["TRUSTED_PROXY_CIDRS"]
    expected = f"{web_ip}/32,{gateway}/32"
    if edge_network.overlaps(data_network) or any(ip not in edge_network for ip in (web_ip, api_ip, gateway)):
        raise ValueError("独立网络的地址配置无效")
    if trusted != expected or not config["networks"]["data"].get("internal"):
        raise ValueError("可信代理或数据网络配置不符")
    if (services["web"]["networks"].keys() != {"edge"}
            or services["redis"]["networks"].keys() != {"data"}
            or services["postgres"]["networks"].keys() != {"data"}
            or services["api"]["networks"].keys() != {"data", "edge"}):
        raise ValueError("服务网络隔离配置不符")
    api_env = services["api"]["environment"]
    if "GLOBAL_PROXY_API_KEY" not in api_env:
        raise ValueError("供应商 API Key 必须仅注入后端 API")
    if any("GLOBAL_PROXY_API_KEY" in services[name].get("environment", {})
           for name in ("postgres", "redis", "migrate", "web")):
        raise ValueError("供应商 API Key 不得注入数据库、迁移或前端服务")
    database_url = urlsplit(api_env["DATABASE_URL"])
    if (database_url.hostname != "postgres" or database_url.username != "ops_platform"
            or database_url.path != "/ops_platform" or unquote(database_url.password or "") != services["postgres"]["environment"]["POSTGRES_PASSWORD"]):
        raise ValueError("API 数据库地址与独立 PostgreSQL 凭据不匹配")
    if api_env["REDIS_URL"] != "redis://redis:6379/0" or api_env["FRONTEND_ORIGIN"] != "https://1.ipip99.com":
        raise ValueError("Redis 或前端来源配置不符")
    if api_env.get("SUPPLIER_AUTO_PROCUREMENT_ENABLED", "false") != "false":
        raise ValueError("自动采购必须关闭")
    if api_env["JWT_SECRET_KEY"] == api_env["SESSION_TOKEN_HASH_KEY"]:
        raise ValueError("JWT 与会话哈希密钥必须不同")
    if not allow_placeholders and any("REPLACE_WITH_" in value for value in (
            api_env["DATABASE_URL"], api_env["JWT_SECRET_KEY"], api_env["SESSION_TOKEN_HASH_KEY"])):
        raise ValueError("环境文件仍包含占位密钥")


def main() -> int:
    parser = argparse.ArgumentParser(description="99ipxin 独立生产 Compose 静态检查")
    parser.add_argument("--template", action="store_true", help="检查仓库中的无密钥模板")
    args = parser.parse_args()
    env_file = ROOT / (".env.production.example" if args.template else ".env.production")
    try:
        verify(env_file, allow_placeholders=args.template)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(f"静态配置未通过: {exc}")
        return 1
    print("独立生产 Compose 静态配置通过；未启动任何容器。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
