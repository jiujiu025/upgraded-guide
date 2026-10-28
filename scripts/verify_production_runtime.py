"""在隔离 CI 中检查独立生产栈的实际容器状态。"""

import json
import subprocess
import time
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASE = ["docker", "compose", "--env-file", str(ROOT / ".env.production"),
        "-f", str(ROOT / "docker-compose.prod.yml")]


def command(*args: str) -> str:
    result = subprocess.run([*BASE, *args], cwd=ROOT, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError("隔离生产容器检查命令失败")
    return result.stdout.strip()


def inspect(service: str) -> dict:
    container_id = command("ps", "-q", service)
    if not container_id:
        raise RuntimeError(f"服务未运行: {service}")
    result = subprocess.run(["docker", "inspect", container_id], text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(f"无法检查服务: {service}")
    return json.loads(result.stdout)[0]


def main() -> None:
    for service in ("postgres", "redis", "api", "web"):
        info = inspect(service)
        for _ in range(60):
            if info["State"].get("Health", {}).get("Status") == "healthy":
                break
            if info["State"].get("Health", {}).get("Status") == "unhealthy":
                raise RuntimeError(f"健康检查失败: {service}")
            time.sleep(2)
            info = inspect(service)
        else:
            raise RuntimeError(f"健康检查超时: {service}")
        if any(mount["Type"] == "bind" for mount in info.get("Mounts", [])):
            raise RuntimeError(f"存在绑定挂载: {service}")
        ports = info["NetworkSettings"].get("Ports") or {}
        bindings = [binding for group in ports.values() for binding in (group or [])]
        if service == "web":
            if len(bindings) != 1 or bindings[0]["HostIp"] != "127.0.0.1" or bindings[0]["HostPort"] != "18081":
                raise RuntimeError("Web 宿主机端口配置不符")
        elif bindings:
            raise RuntimeError(f"服务不应发布宿主机端口: {service}")
        result = subprocess.run(["docker", "exec", info["Id"], "id", "-u"],
                                text=True, capture_output=True)
        if result.returncode or not result.stdout.strip().isdigit() or int(result.stdout.strip()) == 0:
            raise RuntimeError(f"服务未以非 root 运行: {service}")
    with urllib.request.urlopen("http://127.0.0.1:18081/healthz", timeout=5) as response:
        if response.status != 200:
            raise RuntimeError("Web 健康端点未返回 200")
    result = subprocess.run([*BASE, "exec", "-T", "api", "python", "-m", "app.bootstrap_admin"],
                            cwd=ROOT, text=True, capture_output=True)
    if result.returncode == 0 or "必须通过交互终端输入" not in result.stderr:
        raise RuntimeError("管理员初始化未拒绝非交互输入")
    print("独立容器健康、非 root、端口隔离和非交互初始化拒绝均通过。")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError) as exc:
        raise SystemExit(f"隔离生产容器验收失败: {exc}") from None
