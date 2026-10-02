"""协议层纯字节逻辑测试：不需要任何硬件。"""

import pytest

from rapoo_autoswitch import protocol as p


def test_build_unlock_frame():
    frame = p.build_unlock()
    assert len(frame) == p.FRAME_SIZE == 33
    assert frame[:3] == bytes([p.RID_CONTROL, p.CMD_WRITE, p.CMD_UNLOCK])
    assert frame[3:] == bytes(30)


def test_build_read_frame_layout():
    frame = p.build_read(p.BANK_SYSTEM, 0x80, 1)
    assert frame[0] == p.RID_CONTROL
    assert frame[1] == p.CMD_WRITE
    assert frame[2] == p.CMD_READ
    assert frame[3] == 1      # 长度
    assert frame[4] == 0x80   # 地址
    assert frame[5] == p.BANK_SYSTEM
    assert frame[6:] == bytes(27)


def test_build_write_frame_carries_payload():
    frame = p.build_write(p.BANK_SYSTEM, 0xC2, b"\x05")
    assert frame[2] == p.CMD_WRITE
    assert frame[3] == 1
    assert frame[4] == 0xC2
    assert frame[5] == p.BANK_SYSTEM
    assert frame[8:9] == b"\x05"


@pytest.mark.parametrize("length", [0, p.MAX_PAYLOAD + 1])
def test_build_read_rejects_bad_length(length):
    with pytest.raises(ValueError):
        p.build_read(p.BANK_SYSTEM, 0x80, length)


def test_build_write_rejects_oversized_payload():
    with pytest.raises(ValueError):
        p.build_write(p.BANK_SYSTEM, 0x80, bytes(p.MAX_PAYLOAD + 1))


def test_parse_feature_read_without_report_id():
    buf = bytes([p.ACK_OK, 0x00, 0x00, 0x00]) + b"\x2a"
    assert p.parse_feature_read(buf, 1) == b"\x2a"


def test_parse_feature_read_with_report_id():
    buf = bytes([p.RID_FEATURE, p.ACK_OK, 0x00, 0x00, 0x00]) + b"\x2a\x2b"
    assert p.parse_feature_read(buf, 2) == b"\x2a\x2b"


def test_parse_feature_read_rejects_busy_or_short():
    assert p.parse_feature_read(bytes([0x02, 0, 0, 0, 9]), 1) is None
    assert p.parse_feature_read(bytes([p.RID_FEATURE, p.ACK_OK]), 4) is None


def _status_bytes(dpi_level=1, dpi_x=800, dpi_y=800, battery=57, mode=0x20, status=0x00):
    buf = bytearray(10)
    buf[0] = p.RID_STATUS
    buf[1] = mode
    buf[2] = dpi_level - 1
    buf[3:5] = dpi_x.to_bytes(2, "little")
    buf[5:7] = dpi_y.to_bytes(2, "little")
    buf[7] = status
    buf[8] = battery
    return bytes(buf)


def test_parse_status_basic():
    report = p.parse_status(_status_bytes(dpi_level=3, dpi_x=1600, dpi_y=800, battery=57))
    assert report is not None
    assert report.dpi_level == 3
    assert report.dpi_x == 1600
    assert report.dpi_y == 800
    assert report.battery == 57
    assert report.wired is False
    assert report.charging is False


def test_parse_status_detects_wired_and_charging():
    report = p.parse_status(_status_bytes(mode=0x10, status=0x02))
    assert report is not None
    assert report.wired is True
    assert report.charging is True


def test_parse_status_keeps_cached_battery_on_transient_zero():
    transient = _status_bytes(battery=0)
    assert p.parse_status(transient, cached_battery=57).battery == 57
    # 没有历史值时，0 视为满电的握手瞬间
    assert p.parse_status(transient).battery == 100


def test_parse_status_ignores_non_status_reports():
    assert p.parse_status(bytes(10)) is None
    assert p.parse_status(_status_bytes()[:5]) is None
    # 设备标记不认识
    assert p.parse_status(_status_bytes(mode=0x30)) is None


def test_polling_codec_roundtrip():
    for hz, code in p.POLLING_HZ_TO_CODE.items():
        assert p.POLLING_CODE_TO_HZ[code] == hz
    reg = p.REGISTRY["polling_hz"]
    assert reg.decode(bytes([0x01])) == 1000
    assert reg.encode(1000) == b"\x01"
    with pytest.raises(ValueError):
        reg.encode(333)


def test_bool_and_angle_registers():
    assert p.REGISTRY["motion_sync"].decode(b"\x01") is True
    assert p.REGISTRY["motion_sync"].encode(False) == b"\x00"
    assert p.REGISTRY["sensor_angle"].decode(b"\xff") == -1
    assert p.REGISTRY["sensor_angle"].encode(-1) == b"\xff"


def test_register_keys_are_stable():
    reg = p.REGISTRY["polling_hz"]
    assert reg.key == "08:80"
    assert reg.bank == p.BANK_SYSTEM


# --- DPI 寄存器 -----------------------------------------------------------
def test_dpi_slot_count_is_stored_as_code():
    reg = p.REGISTRY["dpi_slot_count"]
    assert reg.decode(b"\x02") == 3          # 协议码 2 = 3 档
    assert reg.encode(3) == b"\x02"
    assert reg.decode(b"\xff") == p.DPI_MAX_SLOTS  # 越界值被夹住


def test_dpi_active_index_is_one_based_outside():
    reg = p.REGISTRY["dpi_active_index"]
    assert reg.decode(b"\x00") == 1
    assert reg.encode(2) == b"\x01"


def test_dpi_table_decode_and_encode():
    reg = p.REGISTRY["dpi_table_x"]
    assert reg.length == p.DPI_TABLE_BYTES == 12
    assert reg.decode(bytes.fromhex("20034006")) == [800, 1600]
    assert reg.encode([800, 1600]) == bytes.fromhex("20034006")
    assert reg.encode("800,1600") == bytes.fromhex("20034006")


def test_dpi_registers_declare_write_order():
    """协议依赖：两张表必须早于档位数，档位数早于当前档位。"""
    assert p.REGISTRY["dpi_table_x"].order < p.REGISTRY["dpi_slot_count"].order
    assert p.REGISTRY["dpi_table_y"].order < p.REGISTRY["dpi_slot_count"].order
    assert p.REGISTRY["dpi_slot_count"].order < p.REGISTRY["dpi_active_index"].order
    assert p.REGISTRY_BY_KEY["08:88"].name == "dpi_table_x"
    assert p.DPI_SLOT_COUNT_KEY == "08:96"
