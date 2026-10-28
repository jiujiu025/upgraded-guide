import socket
import ssl
from pathlib import Path


ca = Path(__file__).resolve().parents[1] / ".ci-tls" / "ca.crt"
host = "acceptance.localhost"
port = 18443


def request(context: ssl.SSLContext) -> tuple[str, str]:
    with socket.create_connection(("127.0.0.1", port), timeout=5) as raw:
        with context.wrap_socket(raw, server_hostname=host) as secure:
            secure.sendall(f"GET /healthz HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode("ascii"))
            response = b""
            while block := secure.recv(8192):
                response += block
            return secure.version(), response.decode("ascii", errors="replace")


trusted = ssl.create_default_context(cafile=str(ca))
trusted.minimum_version = ssl.TLSVersion.TLSv1_2
version, response = request(trusted)
if " 200 " not in response.split("\r\n", 1)[0]:
    raise RuntimeError("受信任证书下 HTTPS 健康检查未返回 200")
if "strict-transport-security: max-age=300" not in response.lower():
    raise RuntimeError("TLS 入口缺少 HSTS")

try:
    request(ssl.create_default_context())
except ssl.SSLCertVerificationError:
    pass
else:
    raise RuntimeError("未显式信任隔离 CA 时证书意外通过验证")

print(f"受信任测试 CA 的证书链、主机名与 HTTPS 健康检查通过：{version}")
