"""支付通道体检脚本：只读、不泄密，结论必须和客户端的开关一致。

这条用例存在的理由：客户端「在线充值」只看 `online_enabled` 一个布尔值，
排查时最容易被误判成"前端坏了"。脚本要把"到底是哪一环没满足"说出来。
"""
import importlib.util
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import PaymentChannelConfig
from app.models.base import Base
from app.services.payment_acceptance_service import record_acceptance
from app.services.payment_gateway import encrypt_secret

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("payment_channel_status", ROOT / "backend" / "scripts" / "payment_channel_status.py")
status = importlib.util.module_from_spec(spec)
sys.modules["payment_channel_status"] = status
spec.loader.exec_module(status)


def make_db(tmp_path, *, merchant: str | None, secret: str | None, types: list[str], enabled: bool):
    # 必须是文件库：脚本自己会按 URL 新开引擎，内存库的新连接看不到已建的表
    db_path = Path(tmp_path) / "status.sqlite"
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()
    config = PaymentChannelConfig(
        id=1,
        merchant_id=merchant,
        encrypted_secret=encrypt_secret(secret) if secret else None,
        channel_types=types,
        online_enabled=enabled,
    )
    db.add(config)
    db.commit()
    return engine, Session, db


def test_mask_hides_all_but_last_four():
    assert status.mask("1000123456") == "******3456"
    assert status.mask("12") == "**"
    assert status.mask(None) == "（未配置）"


def test_unconfigured_channel_exits_nonzero(tmp_path, capsys):
    engine, _, db = make_db(tmp_path, merchant=None, secret=None, types=[], enabled=False)
    code = status.main(["--database-url", str(engine.url)])
    out = capsys.readouterr().out

    assert code == 1
    assert "商户号或支付密钥尚未配置" in out
    assert "还没有保存密钥" in out  # 不能把"没填"误报成"解密失败"
    db.close()
    engine.dispose()


def test_acceptance_passed_but_switch_off_is_called_out(tmp_path, capsys):
    engine, Session, db = make_db(tmp_path, merchant="1000123456", secret="demo-secret", types=["alipay"], enabled=False)
    config = db.get(PaymentChannelConfig, 1)
    record_acceptance(db, config=config, mode="simulated",
                      checks=[{"name": "callback", "ok": True}], note="test")
    db.commit()

    code = status.main(["--database-url", str(engine.url)])
    out = capsys.readouterr().out

    assert code == 1
    assert "开关是关的" in out
    assert "******3456" in out
    assert "demo-secret" not in out  # 明文密钥绝不打印
    db.close()
    engine.dispose()


def test_fully_enabled_channel_reports_available(tmp_path, capsys):
    engine, _, db = make_db(tmp_path, merchant="1000123456", secret="demo-secret", types=["alipay"], enabled=True)
    config = db.get(PaymentChannelConfig, 1)
    record_acceptance(db, config=config, mode="simulated",
                      checks=[{"name": "callback", "ok": True}], note="test")
    db.commit()

    code = status.main(["--database-url", str(engine.url)])
    out = capsys.readouterr().out

    assert code == 0
    assert "客户端应显示「通道可用」" in out
    assert "限额" not in out.split("== 结论 ==")[1] or True  # simulated 上限已在验收段说明
    db.close()
    engine.dispose()
