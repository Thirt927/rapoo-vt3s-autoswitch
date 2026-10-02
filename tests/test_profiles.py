"""快照采集 / 保存 / 下发 / 差异比较，以及会话读写校验。"""

import pytest

from rapoo_autoswitch import profiles, protocol as p
from rapoo_autoswitch.device import DeviceError, Session

from fakes import FakeTransport, status_frame


def make_session(registers=None, status_frames=None) -> Session:
    return Session(FakeTransport(registers=registers, status_frames=status_frames))


def full_registers(value=0x11):
    return {(p.BANK_SYSTEM, reg.addr): bytes([value]) for reg in p.SYSTEM_REGISTERS}


def test_capture_save_load_roundtrip():
    session = make_session(full_registers(0x22))
    snapshot = profiles.capture(session, "办公", 0x1411)
    assert snapshot.size == len(p.SYSTEM_REGISTERS)

    profiles.save(snapshot)
    assert profiles.list_names() == ["办公"]

    loaded = profiles.load("办公")
    assert loaded.registers == snapshot.registers
    assert loaded.product_id == 0x1411


def test_apply_writes_every_register_and_verifies():
    session = make_session({})
    snapshot = profiles.Snapshot(
        name="办公",
        product_id=0x1411,
        registers={"08:80": "01", "08:c2": "05"},
    )
    written = profiles.apply(session, snapshot)
    assert sorted(written) == ["08:80", "08:c2"]
    assert session.transport.registers[(0x08, 0x80)] == b"\x01"
    assert session.transport.registers[(0x08, 0xC2)] == b"\x05"


def test_apply_only_selected_keys():
    session = make_session({})
    snapshot = profiles.Snapshot(name="x", product_id=1, registers={"08:80": "01", "08:c2": "05"})
    profiles.apply(session, snapshot, only=["08:c2"])
    assert (0x08, 0xC2) in session.transport.registers
    assert (0x08, 0x80) not in session.transport.registers


def test_diff_reports_only_changed_keys():
    session = make_session({(p.BANK_SYSTEM, 0x80): b"\x01", (p.BANK_SYSTEM, 0xC2): b"\x02"})
    snapshot = profiles.Snapshot(name="x", product_id=1, registers={"08:80": "01", "08:c2": "05"})
    changes = profiles.diff(session, snapshot)
    assert list(changes) == ["08:c2"]
    assert changes["08:c2"] == ("02", "05")


def test_write_register_verifies_readback():
    session = make_session({(p.BANK_SYSTEM, 0x85): b"\x00"})
    reg = p.REGISTRY["motion_sync"]
    session.write_register(reg, b"\x01")
    assert session.transport.registers[(p.BANK_SYSTEM, 0x85)] == b"\x01"


def test_write_register_raises_when_device_ignores_write():
    class StubbornTransport(FakeTransport):
        def write_control(self, frame):
            # 记录命令，但永远不真正改寄存器
            self.control_frames.append(bytes(frame))

    transport = StubbornTransport({(p.BANK_SYSTEM, 0x80): b"\x08"})
    session = Session(transport)
    with pytest.raises(DeviceError):
        session.write_register(p.REGISTRY["polling_hz"], b"\x01")


def test_read_register_returns_device_value():
    transport = FakeTransport({(p.BANK_SYSTEM, 0x84): b"\x03"})
    session = Session(transport)
    assert session.read_register(p.REGISTRY["lod"]) == b"\x03"


def test_session_reads_status_and_caches_battery():
    transport = FakeTransport(status_frames=[status_frame(battery=0), status_frame(battery=42)])
    session = Session(transport)
    # 第一次读到 0（无缓存）会按满电处理
    report = session.read_status()
    assert report is not None and report.battery == 100
    report = session.read_status()
    assert report is not None and report.battery == 42


def test_missing_snapshot_raises():
    with pytest.raises(FileNotFoundError):
        profiles.load("不存在")
