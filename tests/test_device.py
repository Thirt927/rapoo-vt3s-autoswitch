"""设备发现的角色分配、机型识别与 CLI 解析。"""

import pytest

from rapoo_autoswitch import cli, transport
from rapoo_autoswitch.device import (
    USAGE_CONTROL,
    USAGE_FEATURE,
    USAGE_STATUS,
    VENDOR_USAGE_PAGE,
    DeviceInfo,
    _assign_roles,
)


def _interface(path, usage_page=VENDOR_USAGE_PAGE, usage=0):
    return {"path": path, "usage_page": usage_page, "usage": usage}


def test_assign_roles_by_usage():
    roles = _assign_roles([
        _interface(b"c", usage=USAGE_STATUS),
        _interface(b"a", usage=USAGE_CONTROL),
        _interface(b"b", usage=USAGE_FEATURE),
    ])
    assert roles == {"control": b"a", "feature": b"b", "status": b"c"}


def test_assign_roles_falls_back_to_order():
    roles = _assign_roles([
        _interface(b"b", usage_page=0),
        _interface(b"a", usage_page=0),
        _interface(b"c", usage_page=0),
    ])
    assert roles["control"] == b"a"
    assert roles["feature"] == b"b"
    assert roles["status"] == b"c"


def test_assign_roles_ignores_other_usage_pages():
    roles = _assign_roles([_interface(b"x", usage_page=0x0001, usage=0x0002)])
    assert roles == {}


def test_device_info_model_and_wired_flag():
    known = DeviceInfo(product_id=0x1411, product_string="")
    assert known.model == "雷柏 VT3S"
    assert known.wired is False

    unknown = DeviceInfo(product_id=0xBEEF, product_string="Rapoo Device")
    assert unknown.model == "Rapoo Device"  # 未收录机型退回产品名
    assert DeviceInfo(product_id=0x4611, product_string="").wired is True


def test_cli_parser_accepts_core_commands():
    parser = cli.build_parser()
    assert parser.parse_args(["list"]).command == "list"
    args = parser.parse_args(["set", "polling_hz=1000"])
    assert args.assignments == ["polling_hz=1000"]
    probe = parser.parse_args(["probe", "0x08", "0x80", "1"])
    assert (probe.bank, probe.addr, probe.length) == (8, 0x80, 1)
    assert parser.parse_args(["daemon", "--once"]).once is True


def test_cli_parser_accepts_dpi_command():
    parser = cli.build_parser()
    args = parser.parse_args(["dpi", "800,1600,3200", "--index", "2"])
    assert args.stages == "800,1600,3200"
    assert args.index == 2
    assert parser.parse_args(["dpi"]).stages is None


def test_cli_set_encodes_values():
    from rapoo_autoswitch import protocol as p

    assert cli._parse_value(p.REGISTRY["motion_sync"], "on") is True
    assert cli._parse_value(p.REGISTRY["polling_hz"], "1000") == 1000
    with pytest.raises(ValueError):
        cli._parse_value(p.REGISTRY["polling_hz"], "abc")


def test_cli_snapshots_without_any(capsys):
    assert cli.main(["snapshots"]) == 0
    assert "还没有任何快照" in capsys.readouterr().out


def test_cli_current_shows_machine(capsys):
    assert cli.main(["current"]) == 0
    out = capsys.readouterr().out
    assert "预设" in out and "VID" in out


def test_cli_use_requires_existing_snapshot(capsys):
    assert cli.main(["use", "不存在的预设"]) == 2
    assert "错误" in capsys.readouterr().err


# --- HID 后端：装错包是最常见的坑 -------------------------------------------
def test_install_hint_names_the_right_package():
    """两个包都提供 import hid，提示必须指明该装哪个、该卸哪个。"""
    assert "pip install hidapi" in transport.INSTALL_HINT
    assert "pip uninstall -y hid" in transport.INSTALL_HINT


def test_require_hidapi_passes_when_backend_present(monkeypatch):
    monkeypatch.setattr(transport, "_hidapi", object())
    transport.require_hidapi()  # 不抛异常即通过


def test_require_hidapi_reports_hint_and_underlying_error(monkeypatch):
    monkeypatch.setattr(transport, "_hidapi", None)
    monkeypatch.setattr(transport, "_HID_IMPORT_ERROR", OSError("找不到 hidapi.dll"))
    with pytest.raises(RuntimeError) as excinfo:
        transport.require_hidapi()
    message = str(excinfo.value)
    assert "pip install hidapi" in message
    assert "找不到 hidapi.dll" in message


# --- 接口打开：open_path 的返回值语义 ---------------------------------------
WINDOWS_PATH = (
    b"\\\\?\\HID#VID_24AE&PID_1464&MI_01&Col05#7&be251db&0&0004"
    b"#{4d1e55b2-f16f-11cf-88cb-001111000030}"
)


class _FakeHidModule:
    """替身 hid 模块，只提供 device()。"""

    def __init__(self, device):
        self._device = device

    def device(self):
        return self._device


class _OkDevice:
    def __init__(self):
        self.opened = []

    def open_path(self, path):
        self.opened.append(path)
        return None  # cython-hidapi 成功时就是返回 None


class _FailDevice:
    def open_path(self, path):
        raise OSError("Unable to open device")


def test_open_path_treats_none_return_as_success(monkeypatch):
    """回归：曾把 open_path 的 None 返回值当成失败，导致设备永远打不开。"""
    device = _OkDevice()
    monkeypatch.setattr(transport, "_hidapi", _FakeHidModule(device))
    assert transport.HidapiTransport._open(WINDOWS_PATH) is device
    assert device.opened == [WINDOWS_PATH]


def test_open_path_failure_gives_readable_error(monkeypatch):
    monkeypatch.setattr(transport, "_hidapi", _FakeHidModule(_FailDevice()))
    with pytest.raises(RuntimeError) as excinfo:
        transport.HidapiTransport._open(WINDOWS_PATH)
    message = str(excinfo.value)
    assert "VID_24AE&PID_1464&MI_01&Col05" in message  # 可读片段，不是整条长路径
    assert "Unable to open device" in message
    assert "A-Hub" in message  # 提示独占占用的常见原因


def test_short_path_compresses_windows_device_path():
    assert transport.short_path(WINDOWS_PATH) == "VID_24AE&PID_1464&MI_01&Col05"
    assert transport.short_path("/dev/hidraw3") == "/dev/hidraw3"


def test_vt3s_v2_pid_is_recognised():
    from rapoo_autoswitch.device import DeviceInfo

    assert DeviceInfo(product_id=0x1464, product_string="Rapoo Gaming Device").model == "雷柏 VT3S V2"
