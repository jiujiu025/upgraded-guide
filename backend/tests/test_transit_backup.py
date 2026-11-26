"""面板备份与迁移：备份要能原样重建，恢复要认端口冲突。

最关键的约束：端口、UUID、reality 私钥、shortId 必须原样带过去，
否则客户手里的链接就废了，迁移等于让所有客户重新配置一次。
"""
import gzip
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import hash_password
from app.db import get_db
from app.main import app
from app.models import TransitBackup, TransitVps, User
from app.models.base import Base
from app.services import transit_backup_service


class FakePanel:
    def __init__(self, inbounds=None, xray=None, fail=None):
        self.inbounds = inbounds if inbounds is not None else []
        self.xray = xray or {
            "outbounds": [
                {"tag": "direct", "protocol": "freedom"},
                {"tag": "blocked", "protocol": "blackhole"},
                {"tag": "socks-ORD-1-1", "protocol": "socks",
                 "settings": {"servers": [{"address": "1.2.3.4", "port": 1080}]}},
            ],
            "routing": {"domainStrategy": "AsIs", "rules": [
                {"type": "field", "inboundTag": ["api"], "outboundTag": "api"},
                {"type": "field", "inboundTag": ["inbound-52000"], "outboundTag": "socks-ORD-1-1"},
            ]},
        }
        self.fail = fail
        self.added = []
        self.saved = 0
        self.restarts = 0

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return None

    def list_inbounds(self):
        return self.inbounds

    def used_ports(self):
        return {int(item["port"]) for item in self.inbounds}

    def add_inbound(self, payload):
        if self.fail:
            raise self.fail
        self.added.append(payload)
        self.inbounds.append({"id": len(self.inbounds) + 1, "port": payload["port"],
                              "tag": f"inbound-{payload['port']}", **payload})
        return {"id": len(self.inbounds)}

    def get_xray_template(self):
        return json.loads(json.dumps(self.xray))

    def save_xray_template(self, config):
        self.xray = config
        self.saved += 1

    def restart_xray(self):
        self.restarts += 1


SAMPLE_INBOUND = {
    "id": 7, "port": 52000, "tag": "inbound-52000", "remark": "越南-5.6.7.8",
    "protocol": "vless", "enable": True, "up": 100, "down": 200, "total": 0,
    "settings": json.dumps({"clients": [{"id": "uuid-b", "flow": "", "email": "ORD-1-1"}],
                            "decryption": "none"}),
    "streamSettings": json.dumps({"security": "reality", "realitySettings": {
        "privateKey": "priv-key", "shortIds": ["d816"], "serverNames": ["yahoo.com"]}}),
    "sniffing": json.dumps({"enabled": True}),
}


@pytest.fixture()
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("TRANSIT_BACKUP_DIR", str(tmp_path / "backups"))
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()

    root = User(username="root", password_hash=hash_password("Admin_123456"),
                role="super_admin", display_name="超级管理员")
    ops = User(username="ops", password_hash=hash_password("Admin_123456"),
               role="admin", display_name="管理员")
    db.add_all([root, ops])
    source = TransitVps(name="HK-01", group_code="hk", base_url="https://old.example.com/x",
                        username="admin", connect_host="9.9.9.9",
                        reality_private_key=None, reality_public_key=None, reality_short_id=None,
                        port_start=52000, port_end=52999, max_inbounds=200, is_enabled=True)
    target = TransitVps(name="HK-02", group_code="hk", base_url="https://new.example.com/x",
                        username="admin", connect_host="8.8.8.8",
                        reality_private_key=None, reality_public_key=None, reality_short_id=None,
                        port_start=52000, port_end=52999, max_inbounds=200, is_enabled=False)
    db.add_all([source, target])
    db.commit()

    def override_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    yield {"client": client, "db": db, "source": source, "target": target, "tmp": tmp_path}
    app.dependency_overrides.clear()
    db.close()
    engine.dispose()


def login(client, username="root") -> dict:
    response = client.post("/api/auth/login", json={"username": username, "password": "Admin_123456"})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_backup_contains_everything_needed_to_rebuild(api, monkeypatch):
    panel = FakePanel(inbounds=[SAMPLE_INBOUND])
    monkeypatch.setattr(transit_backup_service, "XuiClient", lambda *a, **k: panel)

    client = api["client"]
    headers = login(client)
    response = client.post(f"/api/admin/transit/vps/{api['source'].id}/backups", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["inbound_count"] == 1
    assert body["outbound_count"] == 3
    assert body["rule_count"] == 2
    assert body["status"] == "ready"

    path = api["tmp"] / "backups" / body["filename"]
    assert path.exists()
    payload = json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))
    inbound = payload["inbounds"][0]
    # 端口、UUID、私钥、shortId 缺一不可，否则客户链接会失效
    assert inbound["port"] == 52000
    assert "uuid-b" in inbound["settings"]
    assert "priv-key" in inbound["streamSettings"]
    assert payload["xray"]["outbounds"][2]["tag"] == "socks-ORD-1-1"


def test_only_super_admin_can_touch_backups(api, monkeypatch):
    monkeypatch.setattr(transit_backup_service, "XuiClient", lambda *a, **k: FakePanel(inbounds=[SAMPLE_INBOUND]))
    headers = login(api["client"], "ops")
    response = api["client"].post(f"/api/admin/transit/vps/{api['source'].id}/backups", headers=headers)
    assert response.status_code == 403


def test_restore_dry_run_reports_conflicts_without_writing(api, monkeypatch):
    source_panel = FakePanel(inbounds=[SAMPLE_INBOUND])
    target_panel = FakePanel(inbounds=[{"id": 1, "port": 52000, "remark": "已占用"}])
    monkeypatch.setattr(transit_backup_service, "XuiClient",
                        lambda *a, **k: source_panel if "old" in str(a[0]) else target_panel)

    client = api["client"]
    headers = login(client)
    backup = client.post(f"/api/admin/transit/vps/{api['source'].id}/backups", headers=headers).json()

    plan = client.post(f"/api/admin/transit/backups/{backup['id']}/restore",
                       json={"target_vps_id": api["target"].id, "dry_run": True},
                       headers=headers).json()
    assert plan["dry_run"] is True
    assert plan["conflicts"] == [52000]
    assert plan["will_create"] == 0
    assert target_panel.added == []


def test_restore_rebuilds_inbounds_and_xray(api, monkeypatch):
    source_panel = FakePanel(inbounds=[SAMPLE_INBOUND])
    target_panel = FakePanel(inbounds=[])
    monkeypatch.setattr(transit_backup_service, "XuiClient",
                        lambda *a, **k: source_panel if "old" in str(a[0]) else target_panel)

    client = api["client"]
    headers = login(client)
    backup = client.post(f"/api/admin/transit/vps/{api['source'].id}/backups", headers=headers).json()

    plan = client.post(f"/api/admin/transit/backups/{backup['id']}/restore",
                       json={"target_vps_id": api["target"].id, "dry_run": False},
                       headers=headers).json()
    assert plan["created"] == [52000]
    assert plan["skipped"] == []

    added = target_panel.added[0]
    assert added["port"] == 52000
    assert "uuid-b" in added["settings"]
    assert "priv-key" in added["streamSettings"]
    # 目标面板自带 direct/blocked 不能被重复添加，备份里的 socks 出站要补上
    tags = [item["tag"] for item in target_panel.xray["outbounds"]]
    assert tags.count("direct") == 1
    assert "socks-ORD-1-1" in tags
    assert target_panel.saved == 1 and target_panel.restarts == 1

    detail = client.get("/api/admin/transit/backups", headers=headers).json()[0]
    assert detail["status"] == "restored"
    assert detail["restored_to_vps_id"] == api["target"].id


def test_download_returns_archive(api, monkeypatch):
    monkeypatch.setattr(transit_backup_service, "XuiClient", lambda *a, **k: FakePanel(inbounds=[SAMPLE_INBOUND]))
    client = api["client"]
    headers = login(client)
    backup = client.post(f"/api/admin/transit/vps/{api['source'].id}/backups", headers=headers).json()

    response = client.get(f"/api/admin/transit/backups/{backup['id']}/download", headers=headers)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/gzip")
    payload = json.loads(gzip.decompress(response.content).decode("utf-8"))
    assert payload["vps"]["name"] == "HK-01"


def test_delete_removes_file_and_record(api, monkeypatch):
    monkeypatch.setattr(transit_backup_service, "XuiClient", lambda *a, **k: FakePanel(inbounds=[SAMPLE_INBOUND]))
    client = api["client"]
    headers = login(client)
    backup = client.post(f"/api/admin/transit/vps/{api['source'].id}/backups", headers=headers).json()
    path = api["tmp"] / "backups" / backup["filename"]
    assert path.exists()

    assert client.delete(f"/api/admin/transit/backups/{backup['id']}", headers=headers).status_code == 200
    assert not path.exists()
    assert api["db"].scalar(select(TransitBackup)) is None
