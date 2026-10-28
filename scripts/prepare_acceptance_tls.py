import subprocess
from pathlib import Path


root = Path(__file__).resolve().parents[1]
target = root / ".ci-tls"
target.mkdir(exist_ok=True)
(target / "openssl.cnf").write_text("[req]\ndistinguished_name=dn\n[dn]\n", encoding="ascii")
(target / "server.ext").write_text(
    "basicConstraints=critical,CA:FALSE\n"
    "subjectAltName=DNS:acceptance.localhost\n"
    "keyUsage=critical,digitalSignature,keyEncipherment\n"
    "extendedKeyUsage=serverAuth\n",
    encoding="ascii",
)


def openssl(*args: str) -> None:
    subprocess.run(["openssl", *args], cwd=target, check=True, capture_output=True)


openssl("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
        "-subj", "/CN=Acceptance Test CA", "-config", "openssl.cnf",
        "-addext", "basicConstraints=critical,CA:TRUE",
        "-addext", "keyUsage=critical,keyCertSign,cRLSign",
        "-keyout", "ca.key", "-out", "ca.crt")
openssl("req", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=acceptance.localhost",
        "-config", "openssl.cnf", "-keyout", "server.key", "-out", "server.csr")
openssl("x509", "-req", "-in", "server.csr", "-CA", "ca.crt", "-CAkey", "ca.key",
        "-CAcreateserial", "-days", "1", "-extfile", "server.ext", "-out", "server.crt")
openssl("verify", "-CAfile", "ca.crt", "server.crt")
print("隔离测试 CA 签发的 HTTPS 证书已生成，客户端须显式信任 ca.crt")
