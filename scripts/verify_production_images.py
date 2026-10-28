import json
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path


COMPOSE_FILE = "docker-compose.acceptance.yml"
IMAGES = {
    "99ipxin-api:acceptance": ("app/tests", "app/data", "app/.env", "app/.pytest_cache"),
    "99ipxin-web:acceptance": ("app/src", "app/node_modules", "usr/share/nginx/html/.env"),
}


def run(*args: str, capture: bool = False) -> str:
    result = subprocess.run(args, check=True, text=True, capture_output=capture)
    return result.stdout.strip() if capture else ""


def inspect_non_root(image: str) -> None:
    raw = run("docker", "image", "inspect", image, capture=True)
    user = json.loads(raw)[0]["Config"].get("User", "")
    if not user or user.lower() == "root" or user == "0":
        raise RuntimeError(f"镜像未配置非 root 用户: {image}")


def inspect_image_files(image: str, forbidden: tuple[str, ...]) -> None:
    container_id = run("docker", "create", image, capture=True)
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            archive = Path(temp_dir) / "filesystem.tar"
            run("docker", "export", "-o", str(archive), container_id)
            with tarfile.open(archive) as exported:
                names = [name.lstrip("./") for name in exported.getnames()]
            matches = [name for name in names if any(name == item or name.startswith(f"{item}/") for item in forbidden)]
            root = "app/" if image.endswith("api:acceptance") else "usr/share/nginx/html/"
            matches.extend(name for name in names if name.startswith(root) and (
                Path(name).name.startswith(".env") or Path(name).suffix in {".db", ".sqlite", ".sqlite3"}
                or ".pytest_cache" in Path(name).parts or "__pycache__" in Path(name).parts
            ))
            if matches:
                raise RuntimeError(f"镜像包含禁止文件: {image}: {matches[:10]}")
    finally:
        subprocess.run(["docker", "rm", "-f", container_id], check=False, capture_output=True)


def inspect_runtime(service: str) -> None:
    container_id = run("docker", "compose", "-f", COMPOSE_FILE, "ps", "-q", service, capture=True)
    if not container_id:
        raise RuntimeError(f"服务未运行: {service}")
    for _ in range(60):
        raw = run("docker", "inspect", container_id, capture=True)
        info = json.loads(raw)[0]
        health = info["State"].get("Health", {}).get("Status")
        if health == "healthy":
            break
        if info["State"]["Status"] in {"exited", "dead"} or health == "unhealthy":
            raise RuntimeError(f"服务未通过健康检查: {service}")
        time.sleep(2)
    else:
        raise RuntimeError(f"等待健康检查超时: {service}")
    if any(mount["Type"] == "bind" for mount in info.get("Mounts", [])):
        raise RuntimeError(f"生产容器存在源码绑定挂载: {service}")
    uid = run("docker", "exec", container_id, "id", "-u", capture=True)
    if not uid.isdigit() or int(uid) == 0:
        raise RuntimeError(f"生产容器未以非 root 身份运行: {service}")


def inspect_redis_interruption() -> None:
    api_id = run("docker", "compose", "-f", COMPOSE_FILE, "ps", "-q", "api", capture=True)
    request = """import json, urllib.request, urllib.error
cases = [('/api/auth/login', {'username':'missing-acceptance','password':'Invalid_123!'}),
         ('/api/public/register', {'username':'redis-unavailable','password':'Register_123!',
          'name':'isolated','email':'isolated@example.invalid'})]
for path, body in cases:
    req = urllib.request.Request('http://127.0.0.1:8000' + path,
        data=json.dumps(body).encode(), headers={'Content-Type':'application/json'}, method='POST')
    try:
        urllib.request.urlopen(req, timeout=5)
        raise SystemExit('认证接口未在 Redis 中断时失败关闭: ' + path)
    except urllib.error.HTTPError as exc:
        if exc.code != 503:
            raise SystemExit('认证接口返回非预期状态: ' + path + ': ' + str(exc.code))
print('Redis 中断期间登录与注册均返回 503')
"""
    run("docker", "compose", "-f", COMPOSE_FILE, "stop", "redis")
    try:
        run("docker", "exec", api_id, "python", "-c", request)
    finally:
        run("docker", "compose", "-f", COMPOSE_FILE, "start", "redis")


def main() -> int:
    for image, forbidden in IMAGES.items():
        inspect_non_root(image)
        inspect_image_files(image, forbidden)
    inspect_runtime("api")
    inspect_runtime("web")
    inspect_redis_interruption()
    print("生产镜像内容、非 root 用户、健康检查和绑定挂载检查通过")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"镜像验收失败: {exc}", file=sys.stderr)
        raise SystemExit(1)
