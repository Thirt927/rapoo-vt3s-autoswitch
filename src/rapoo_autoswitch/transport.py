"""HID 传输层。

真实实现基于 hidapi：雷柏二代会暴露三个 HID 接口，分别负责下发命令、读回 Feature
应答、接收状态广播，因此 :class:`HidapiTransport` 同时持有三个句柄。

**依赖要点**：PyPI 上提供 ``import hid`` 的包有两个，必须选对——

* ``hidapi``（trezor/cython-hidapi）—— 编译扩展，原生库静态链接，装完即用 ✅
* ``hid``（apmorton/pyhidapi）—— 只是 ctypes 绑定，**不含原生库**，Windows 上会因找不到
  ``hidapi.dll`` 而在导入时失败 ❌

两者提供的模块名都是 ``hid``、API 也一致，因此本项目统一依赖 ``hidapi``。

``hid`` 属于必装依赖，但为了能在离线环境跑单元测试，这里仍做导入降级处理。
"""

from __future__ import annotations

import re
from typing import Optional

from .protocol import RID_FEATURE

_HID_IMPORT_ERROR: Optional[BaseException] = None
try:  # pragma: no cover - 取决于运行环境
    import hid as _hidapi
except Exception as _exc:  # noqa: BLE001 - 缺库或原生库加载失败都要降级
    _hidapi = None  # type: ignore[assignment]
    _HID_IMPORT_ERROR = _exc

INSTALL_HINT = (
    "请安装带原生库的 HID 绑定：pip install hidapi\n"
    "若已装过 hid（apmorton/pyhidapi），请先卸载它——它只是 ctypes 绑定、不含原生库，\n"
    "在 Windows 上会因找不到 hidapi.dll 而导入失败：pip uninstall -y hid"
)


def hidapi_available() -> bool:
    """当前环境是否能真正访问 HID 设备。"""
    return _hidapi is not None


def import_error() -> Optional[BaseException]:
    """导入 hid 失败时抛出的原始异常，便于排查（成功时为 None）。"""
    return _HID_IMPORT_ERROR


def require_hidapi() -> None:
    """确保 HID 后端可用，否则抛出带安装指引的异常。"""
    if _hidapi is not None:
        return
    detail = ""
    if _HID_IMPORT_ERROR is not None:
        detail = f"\n底层错误：{type(_HID_IMPORT_ERROR).__name__}: {_HID_IMPORT_ERROR}"
    raise RuntimeError(f"未找到可用的 HID 通信库。\n{INSTALL_HINT}{detail}")


def short_path(path) -> str:
    """把 Windows 设备路径压成可读片段。

    原始路径长这样::

        \\\\?\\HID#VID_24AE&PID_1464&MI_01&Col05#7&be251db&0&0004#{guid}

    报错时只需要 ``VID_24AE&PID_1464&MI_01&Col05`` 就够定位了。
    """
    text = path.decode("utf-8", "replace") if isinstance(path, (bytes, bytearray)) else str(path)
    match = re.search(r"VID_[0-9A-Fa-f]{4}&PID_[0-9A-Fa-f]{4}&MI_\d+&Col\d+", text)
    return match.group(0) if match else text


class Transport:
    """一次设备连接的字节通道。"""

    def write_control(self, frame: bytes) -> None:
        raise NotImplementedError

    def get_feature(self, length: int = 33) -> bytes:
        raise NotImplementedError

    def read_status(self, timeout_ms: int = 200) -> Optional[bytes]:
        raise NotImplementedError

    def close(self) -> None:
        pass


class HidapiTransport(Transport):
    """基于 hidapi 的三接口实现。"""

    def __init__(self, control_path, feature_path, status_path=None):
        require_hidapi()
        self._control = self._open(control_path)
        self._feature = self._open(feature_path)
        self._status = self._open(status_path) if status_path else None

    @staticmethod
    def _open(path):
        """打开一个接口。

        ``cython-hidapi`` 的 ``open_path`` **成功时返回 None、失败时抛 IOError**，
        所以不能拿返回值当布尔判断——那会把"成功"判成"失败"，导致永远打不开设备。
        """
        device = _hidapi.device()
        try:
            device.open_path(path)
        except Exception as exc:  # noqa: BLE001 - 统一成带上下文的错误
            raise RuntimeError(
                f"无法打开 HID 接口 {short_path(path)}：{exc}\n"
                "常见原因：官方 A-Hub 等驱动正以独占方式占用该接口，退出它们后重试。"
            ) from exc
        return device

    def write_control(self, frame: bytes) -> None:
        self._control.write(bytes(frame))

    def get_feature(self, length: int = 33) -> bytes:
        return bytes(self._feature.get_feature_report(RID_FEATURE, length))

    def read_status(self, timeout_ms: int = 200) -> Optional[bytes]:
        if self._status is None:
            return None
        buf = self._status.read(64, timeout_ms)
        return bytes(buf) if buf else None

    def close(self) -> None:
        for device in (self._control, self._feature, self._status):
            if device is not None:
                try:
                    device.close()
                except Exception:  # noqa: BLE001 - 关闭失败不影响退出
                    pass
