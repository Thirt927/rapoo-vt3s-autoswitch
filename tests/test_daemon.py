"""守护链路：预设加载与下发（设备发现由 `daemon.run` 负责，这里只测纯逻辑）。"""

import pytest

from rapoo_autoswitch import daemon, profiles, protocol as p
from rapoo_autoswitch.device import Session

from fakes import FakeTransport


def test_apply_preset_writes_saved_snapshot():
    profiles.save(
        profiles.Snapshot(
            name="办公",
            product_id=0x1411,
            registers={
                "08:88": "200300000000000000000000",
                "08:96": "00",
                "08:98": "00",
                "08:80": "01",
            },
        )
    )
    session = Session(FakeTransport({}))
    written = daemon.apply_preset(session, "办公")

    assert sorted(written) == ["08:80", "08:88", "08:96", "08:98"]
    assert session.transport.registers[(p.BANK_SYSTEM, 0x80)] == b"\x01"
    # DPI 表按档位数(1)裁剪成 2 字节
    assert len(session.transport.writes_to(p.BANK_SYSTEM, p.ADDR_DPI_TABLE_X)[0]) == 2


def test_apply_preset_raises_for_unknown_snapshot():
    with pytest.raises(FileNotFoundError):
        daemon.apply_preset(Session(FakeTransport({})), "不存在")


def test_run_requires_a_preset():
    """没设预设就直接退出，而不是空转。"""
    with pytest.raises(RuntimeError):
        daemon.run(once=True)
